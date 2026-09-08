"""`app.collection.providers.job_object` -- mecanismo puro de Windows Job
Object (`EdgeLifecycleJob`), sem depender de `EdgeCdpSupervisor` nem de um
Edge real. Usa processos reais e descartáveis (`ping`, sempre disponível
no Windows e imune ao problema de stdin redirecionado do `timeout.exe`)
para provar o comportamento real do kernel, não só mockar as chamadas
Win32."""

import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from app.collection.providers.job_object import EdgeLifecycleJob, JobObjectError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Object é só Windows")


def _spawn_disposable_process() -> subprocess.Popen:
    """`ping -n 30` -- processo real, inofensivo, sempre presente no
    Windows, que ficaria vivo por ~30s se ninguém o matasse. Achado real
    (não usar `timeout.exe`): ele exige console interativo e morre quase
    imediatamente com stdin redirecionado -- `ping` funciona igual com
    stdio redirecionado ou não."""
    return subprocess.Popen(
        ["ping", "-n", "30", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def _wait_until_gone(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.1)
    return not psutil.pid_exists(pid)


def test_closing_job_kills_the_assigned_process() -> None:
    process = _spawn_disposable_process()
    try:
        assert psutil.pid_exists(process.pid)
        job = EdgeLifecycleJob()
        job.assign(process.pid)

        job.close()

        assert _wait_until_gone(process.pid), "processo atribuido ao job deveria morrer ao fechar o handle"
    finally:
        if psutil.pid_exists(process.pid):
            process.kill()
        process.wait(timeout=5)


def test_closing_job_never_kills_a_process_not_assigned_to_it() -> None:
    """Regra explícita: nunca matar processo de fora do job -- prova com
    DOIS processos reais, só um atribuído."""
    assigned = _spawn_disposable_process()
    untouched = _spawn_disposable_process()
    try:
        job = EdgeLifecycleJob()
        job.assign(assigned.pid)

        job.close()

        assert _wait_until_gone(assigned.pid)
        assert psutil.pid_exists(untouched.pid), "processo NAO atribuido nunca deveria morrer"
    finally:
        for proc in (assigned, untouched):
            if psutil.pid_exists(proc.pid):
                proc.kill()
            proc.wait(timeout=5)


def test_process_already_exited_is_a_clean_error_not_a_crash() -> None:
    process = _spawn_disposable_process()
    process.kill()
    process.wait(timeout=5)

    job = EdgeLifecycleJob()
    try:
        with pytest.raises(JobObjectError):
            job.assign(process.pid)
    finally:
        job.close()


def test_close_without_any_assignment_is_a_harmless_noop() -> None:
    job = EdgeLifecycleJob()
    job.close()  # não deveria levantar
    job.close()  # segunda chamada tambem nao -- idempotente


def test_child_dies_when_parent_process_is_killed_abruptly_no_close_called() -> None:
    """O cenário real que motivou esta correção: `Stop-Process -Force`/
    crash do processo pai -- SEM sinal capturável, SEM `close()` explícito
    nenhum rodando. Um processo Python FILHO (não o processo de teste em
    si) cria o job, atribui um `ping` real a ele, e é morto sem chance de
    limpeza -- o Windows precisa fechar o handle sozinho e matar o `ping`
    junto, só por isso."""
    script = (
        "import subprocess, sys, time\n"
        "sys.path.insert(0, r'{backend_dir}')\n"
        "from app.collection.providers.job_object import EdgeLifecycleJob\n"
        "child = subprocess.Popen(['ping', '-n', '30', '127.0.0.1'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
        "job = EdgeLifecycleJob()\n"
        "job.assign(child.pid)\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(30)\n"  # nunca chega a rodar `job.close()` -- morre antes, de propósito
    ).format(backend_dir=str(Path(__file__).resolve().parents[1] / "backend"))

    parent = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        grandchild_pid_line = parent.stdout.readline().strip()
        assert grandchild_pid_line, "processo pai (dublê) nunca chegou a criar/atribuir o job"
        grandchild_pid = int(grandchild_pid_line)
        assert psutil.pid_exists(grandchild_pid)

        # Mata o PAI sem chance nenhuma de cleanup -- exatamente
        # `Stop-Process -Force`/crash: TerminateProcess direto, nenhum
        # `finally`/`atexit` roda no lado dele.
        parent.kill()
        parent.wait(timeout=5)

        assert _wait_until_gone(
            grandchild_pid, timeout_seconds=10
        ), "processo do 'Edge' deveria morrer sozinho quando o pai e' morto abruptamente, mesmo sem close() nenhum"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        try:
            if grandchild_pid_line and psutil.pid_exists(int(grandchild_pid_line)):
                psutil.Process(int(grandchild_pid_line)).kill()
        except (psutil.NoSuchProcess, NameError):
            pass


def test_multiple_jobs_are_independent() -> None:
    """Um segundo job fechado nunca afeta o processo do primeiro -- cada
    `EdgeLifecycleJob` é seu próprio objeto de kernel."""
    first = _spawn_disposable_process()
    second = _spawn_disposable_process()
    try:
        job_a = EdgeLifecycleJob()
        job_a.assign(first.pid)
        job_b = EdgeLifecycleJob()
        job_b.assign(second.pid)

        job_b.close()

        assert _wait_until_gone(second.pid)
        assert psutil.pid_exists(first.pid)

        job_a.close()
        assert _wait_until_gone(first.pid)
    finally:
        for proc in (first, second):
            if psutil.pid_exists(proc.pid):
                proc.kill()
            proc.wait(timeout=5)
