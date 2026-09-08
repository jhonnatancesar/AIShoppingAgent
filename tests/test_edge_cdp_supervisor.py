"""Lifecycle sob demanda do EdgeCdpSupervisor (TASK-109): lease/idle-timeout.

Testes locais, sem Edge/CDP/psutil reais -- `_ensure_running`,
`_cdp_ready`, `_close_dedicated_browser` e `_terminate_launcher` são
substituídos por dublês para exercitar só a máquina de estados
(`_acquire`/`_release`/monitor/timer de ociosidade). Convenção do
projeto: `asyncio.run(...)` dentro de teste síncrono, sem
pytest-asyncio (não é dependência do projeto)."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import psutil
import pytest
from app.collection.providers.edge_cdp_supervisor import EdgeCdpSupervisor


@pytest.fixture
def tmp_path():
    """`tempfile.mkdtemp()` direto -- o `tmp_path` padrão do pytest usa
    `%TEMP%/pytest-of-User`, que neste ambiente pode ficar com ACL travada
    (Kaspersky), sem relação com o código sob teste."""
    path = Path(tempfile.mkdtemp(prefix="aishoppingagent-edge-cdp-test-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _build_supervisor(
    tmp_path: Path, *, idle_timeout_seconds: float = 0.05
) -> EdgeCdpSupervisor:
    fake_executable = tmp_path / "msedge.exe"
    fake_executable.write_bytes(b"")
    return EdgeCdpSupervisor(
        "http://127.0.0.1:9223",
        executable=fake_executable,
        profile_dir=tmp_path / "profile",
        idle_timeout_seconds=idle_timeout_seconds,
        probe_interval_seconds=0.02,
    )


def _patch_lifecycle(monkeypatch, supervisor: EdgeCdpSupervisor):
    """Dublês: `_cdp_ready` reflete um estado local simples (nunca "adota"
    um Edge inexistente); `_ensure_running` só marca `_started_process` e
    conta chamadas -- sem processo/subprocesso real."""
    calls = {"ensure_running": 0, "close": 0, "terminate": 0}
    state = {"ready": False}

    async def fake_ensure_running() -> None:
        # Replica o guard real de `_ensure_running`: `_acquire()` sempre
        # chama, mas o Edge só é (re)lançado se ainda não estiver pronto.
        if state["ready"]:
            return
        calls["ensure_running"] += 1
        supervisor._started_process = True
        state["ready"] = True

    async def fake_cdp_ready() -> bool:
        return state["ready"]

    async def fake_close() -> None:
        calls["close"] += 1
        state["ready"] = False

    async def fake_terminate() -> None:
        calls["terminate"] += 1

    monkeypatch.setattr(supervisor, "_ensure_running", fake_ensure_running)
    monkeypatch.setattr(supervisor, "_cdp_ready", fake_cdp_ready)
    monkeypatch.setattr(supervisor, "_close_dedicated_browser", fake_close)
    monkeypatch.setattr(supervisor, "_terminate_launcher", fake_terminate)
    return calls, state


def test_lease_launches_edge_on_demand(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        assert supervisor.active_leases == 0
        assert calls["ensure_running"] == 0

        async with supervisor.lease():
            assert supervisor.active_leases == 1
            assert calls["ensure_running"] == 1

        await supervisor._stop_monitor()

    asyncio.run(scenario())


def test_lease_reused_across_batch_without_relaunch(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            async with supervisor.lease():
                assert supervisor.active_leases == 2
                assert calls["ensure_running"] == 1
            assert supervisor.active_leases == 1
            assert calls["ensure_running"] == 1

        for _ in range(3):
            async with supervisor.lease():
                pass
        assert calls["ensure_running"] == 1

        await supervisor._stop_monitor()

    asyncio.run(scenario())


def test_idle_timeout_closes_edge_after_configured_period(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path, idle_timeout_seconds=0.05)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            pass

        assert calls["close"] == 0
        await asyncio.sleep(0.2)

        assert calls["close"] == 1
        assert calls["terminate"] == 1
        assert supervisor._started_process is False
        assert supervisor.active_leases == 0

    asyncio.run(scenario())


def test_closed_during_idle_does_not_relaunch_until_new_lease(
    tmp_path, monkeypatch
) -> None:
    supervisor = _build_supervisor(tmp_path, idle_timeout_seconds=0.05)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            pass
        await asyncio.sleep(0.2)
        assert calls["ensure_running"] == 1

        await asyncio.sleep(0.2)
        assert calls["ensure_running"] == 1

        async with supervisor.lease():
            assert calls["ensure_running"] == 2

        await supervisor._stop_monitor()

    asyncio.run(scenario())


def test_manual_close_while_idle_before_timeout_does_not_relaunch(
    tmp_path, monkeypatch
) -> None:
    supervisor = _build_supervisor(tmp_path, idle_timeout_seconds=1.0)
    calls, state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            pass
        assert calls["ensure_running"] == 1

        state["ready"] = False
        await asyncio.sleep(0.15)

        assert calls["ensure_running"] == 1

    asyncio.run(scenario())


def test_edge_killed_during_active_lease_recovers(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path)
    calls, state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            assert calls["ensure_running"] == 1

            state["ready"] = False
            await asyncio.sleep(0.15)

            assert calls["ensure_running"] >= 2

        await supervisor._stop_monitor()

    asyncio.run(scenario())


def _build_supervisor_with_real_decoy_process(tmp_path: Path) -> EdgeCdpSupervisor:
    """DEC-129: diferente de `_build_supervisor` (arquivo de 0 bytes,
    nunca executado -- `_ensure_running` é sempre trocado por dublê nos
    testes acima), este decoy é um `.bat` real que o Windows sabe
    executar via `CreateProcess` (delegação nativa pra `cmd.exe`) e que
    IGNORA quaisquer argumentos (`--remote-debugging-address=...` etc.)
    -- fica vivo (`ping` interno) até ser morto. Usado só pelos testes
    abaixo, que exercitam `_ensure_running`/`_terminate_launcher` DE
    VERDADE (nunca mockados), especificamente para provar a integração
    real com `EdgeLifecycleJob`."""
    decoy = tmp_path / "fake-edge.bat"
    decoy.write_text("@echo off\r\nping -n 30 127.0.0.1 >nul\r\n", encoding="utf-8")
    # Achado real: porta 19223 (nunca a 9223 real de produção) -- este
    # servidor pode ter um `collection_worker` nativo de verdade rodando
    # em paralelo, com Edge real escutando em 127.0.0.1:9223; usar a
    # mesma porta faria `_cdp_ready()` enxergar o Edge ALHEIO como se
    # fosse o decoy já pronto, pulando o `_ensure_running` real por
    # engano.
    return EdgeCdpSupervisor(
        "http://127.0.0.1:19223",
        executable=decoy,
        profile_dir=tmp_path / "profile",
        probe_interval_seconds=0.02,
    )


def test_ensure_running_creates_and_assigns_a_real_job_object(tmp_path, monkeypatch) -> None:
    """`_ensure_running` real (processo real lançado), só `wait_until_
    ready` trocado por dublê (evita depender de um CDP de verdade --
    responsabilidade de outro teste). Prova que o job é criado, o
    processo real fica vivo, e o job realmente controla o lifecycle dele
    (fechar o job mata o processo, mesmo sem `_terminate_launcher`)."""
    supervisor = _build_supervisor_with_real_decoy_process(tmp_path)

    async def fake_wait_until_ready(*, timeout_seconds=None) -> None:
        return None

    monkeypatch.setattr(supervisor, "wait_until_ready", fake_wait_until_ready)

    async def scenario() -> None:
        await supervisor._ensure_running()
        assert supervisor._job is not None
        assert supervisor.process_id is not None
        assert psutil.pid_exists(supervisor.process_id)

        # Fecha o job DIRETO (sem passar por `_terminate_launcher`) --
        # prova que é o JOB, não outra coisa, controlando o processo.
        pid = supervisor.process_id
        supervisor._job.close()
        deadline = asyncio.get_event_loop().time() + 5
        while psutil.pid_exists(pid) and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)
        assert not psutil.pid_exists(pid)

    try:
        asyncio.run(scenario())
    finally:
        if supervisor.process_id is not None and psutil.pid_exists(supervisor.process_id):
            psutil.Process(supervisor.process_id).kill()


def test_terminate_launcher_closes_job_and_kills_real_process(tmp_path, monkeypatch) -> None:
    """`_terminate_launcher` real -- prova que o cleanup normal (não o
    cenário de kill abrupto, coberto em `test_job_object.py`) também
    limpa o job corretamente, sem deixar handle vazando nem processo
    remanescente."""
    supervisor = _build_supervisor_with_real_decoy_process(tmp_path)

    async def fake_wait_until_ready(*, timeout_seconds=None) -> None:
        return None

    monkeypatch.setattr(supervisor, "wait_until_ready", fake_wait_until_ready)

    async def scenario() -> None:
        await supervisor._ensure_running()
        pid = supervisor.process_id
        assert psutil.pid_exists(pid)

        await supervisor._terminate_launcher()

        assert supervisor._job is None
        assert supervisor._process is None
        assert not psutil.pid_exists(pid)

    asyncio.run(scenario())
