"""Windows Service: supervisor headless do collection_worker nativo (TASK-109).

Roda em Session 0, headless. NAO abre nem controla o Edge diretamente --
apenas monitora o estado nativo da Scheduled Task
"AIShoppingAgent-CollectionWorker" (que o proprio Windows ja rastreia) e,
se o worker morrer, aciona essa mesma tarefa para reiniciar. Expoe uma API
HTTP minima (status/start/restart), assinada por HMAC, em loopback apenas.

Instalar como servico (executar como Administrador):
    python collection_worker_ops_agent.py install
    python collection_worker_ops_agent.py start

Configurar recuperacao nativa do proprio servico (reinicio pelo SCM em
caso de queda do Ops Agent -- nao depende deste script):
    sc failure AIShoppingAgentOpsAgent reset= 86400 actions= restart/5000/restart/15000/restart/30000
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import servicemanager
import win32event
import win32service
import win32serviceutil

TASK_NAME = "AIShoppingAgent-CollectionWorker"
POLL_SECONDS = 15
MAX_RESTARTS_PER_WINDOW = 5
RESTART_WINDOW_SECONDS = 600
BACKOFF_SECONDS = (5, 15, 30, 60, 120)
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8021
RUNTIME_DIR = r"C:\aishoppingagent-runtime"
LOG_FILE = os.path.join(RUNTIME_DIR, "ops-agent.log")
# Local definitivo do segredo: ProgramData (nao o perfil do usuario), com ACL
# restrita a SYSTEM + Administrators -- ver _harden_secrets_dir().
SECRETS_DIR = r"C:\ProgramData\AIShoppingAgent\secrets"
SECRET_FILE = os.path.join(SECRETS_DIR, "ops-agent-secret")

logger = logging.getLogger("ops_agent")


def _configure_logging() -> None:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _harden_secrets_dir() -> None:
    """Remove heranca de ACL e restringe a pasta a SYSTEM + Administrators."""
    result = subprocess.run(
        [
            "icacls",
            SECRETS_DIR,
            "/inheritance:r",
            "/grant:r",
            "SYSTEM:(OI)(CI)F",
            "/grant:r",
            "*S-1-5-32-544:(OI)(CI)F",  # BUILTIN\Administrators (SID fixo, independe de idioma)
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        logger.warning("falha ao aplicar ACL em %s: %s", SECRETS_DIR, result.stderr.strip())


def _load_or_create_secret() -> bytes:
    if not os.path.isdir(SECRETS_DIR):
        os.makedirs(SECRETS_DIR, exist_ok=True)
        _harden_secrets_dir()
    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "w", encoding="utf-8") as file:
            file.write(secrets.token_hex(32))
        logger.info("segredo do Ops Agent gerado em %s", SECRET_FILE)
    with open(SECRET_FILE, "rb") as file:
        return file.read().strip()


def _query_task_state() -> str:
    # schtasks /query devolve o status localizado no idioma do SO (ex.: "Pronto"
    # em PT-BR), o que quebra comparacao por string fixa. Get-ScheduledTask
    # expoe o TaskState como enum .NET, sempre em ingles, independente do
    # idioma da UI -- usado aqui por isso, nao por preferencia de shell.
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-ScheduledTask -TaskName '{TASK_NAME}').State.ToString()",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "Unavailable"
    return result.stdout.strip()


def _run_task() -> bool:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Start-ScheduledTask -TaskName '{TASK_NAME}'",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


class RestartGovernor:
    """Evita restart loop: backoff crescente + teto de tentativas por janela."""

    def __init__(self) -> None:
        self._attempts: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> tuple[bool, float]:
        with self._lock:
            now = time.time()
            self._attempts = [t for t in self._attempts if now - t < RESTART_WINDOW_SECONDS]
            if len(self._attempts) >= MAX_RESTARTS_PER_WINDOW:
                return False, 0.0
            delay = BACKOFF_SECONDS[min(len(self._attempts), len(BACKOFF_SECONDS) - 1)]
            self._attempts.append(now)
            return True, delay


class WorkerMonitor:
    """Thread de supervisao: detecta queda do worker e aciona a task."""

    def __init__(self) -> None:
        self._governor = RestartGovernor()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_state = "unknown"
        self.last_action: dict | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=POLL_SECONDS + 5)

    def restart_worker(self, *, reason: str) -> dict:
        allowed, delay = self._governor.allow()
        if not allowed:
            action = {
                "triggered": False,
                "reason": reason,
                "detail": "limite de restarts na janela atingido -- possivel loop, aguardando",
            }
            logger.warning("restart negado (limite de janela): %s", reason)
            self.last_action = action
            return action
        if delay:
            time.sleep(delay)
        triggered = _run_task()
        action = {
            "triggered": triggered,
            "reason": reason,
            "backoff_seconds": delay,
        }
        logger.info("restart acionado (%s): motivo=%s backoff=%ss", triggered, reason, delay)
        self.last_action = action
        return action

    def _loop(self) -> None:
        previously_running = False
        while not self._stop_event.is_set():
            try:
                state = _query_task_state()
                self.last_state = state
                is_running = state == "Running"
                logger.info("poll: task_state=%s previously_running=%s", state, previously_running)
                if previously_running and not is_running:
                    logger.warning("worker caiu (estado da task: %s) -- acionando restart", state)
                    self.restart_worker(reason=f"task state changed to {state}")
                previously_running = is_running
            except Exception:
                logger.exception("erro no ciclo de monitoramento")
            self._stop_event.wait(POLL_SECONDS)


_MONITOR = WorkerMonitor()
_SEEN_NONCES: dict[str, float] = {}
_NONCE_LOCK = threading.Lock()


def _verify_signature(headers: dict, body: bytes) -> bool:
    try:
        timestamp = headers.get("X-Ops-Timestamp", "")
        nonce = headers.get("X-Ops-Nonce", "")
        signature = headers.get("X-Ops-Signature", "")
        if abs(time.time() - int(timestamp)) > 30:
            return False
    except ValueError:
        return False
    expected = hmac.new(
        _load_or_create_secret(),
        timestamp.encode() + b"." + nonce.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    with _NONCE_LOCK:
        now = time.time()
        for seen_nonce, seen_at in tuple(_SEEN_NONCES.items()):
            if now - seen_at > 60:
                _SEEN_NONCES.pop(seen_nonce, None)
        if not nonce or nonce in _SEEN_NONCES:
            return False
        _SEEN_NONCES[nonce] = now
    return True


class OpsAgentHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.info("http: " + format, *args)

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        if not _verify_signature(dict(self.headers), body):
            self._reply(401, {"error": "invalid signature"})
            return
        route = self.path.rstrip("/")
        if route == "/v1/collection_worker/status":
            self._reply(200, {"service": "collection_worker", "task_state": _query_task_state()})
        elif route == "/v1/collection_worker/start":
            state = _query_task_state()
            if state == "Running":
                self._reply(200, {"service": "collection_worker", "status": "already_running"})
            else:
                action = _MONITOR.restart_worker(reason="start solicitado via API")
                self._reply(200, {"service": "collection_worker", **action})
        elif route == "/v1/collection_worker/restart":
            action = _MONITOR.restart_worker(reason="restart solicitado via API")
            self._reply(200, {"service": "collection_worker", **action})
        else:
            self._reply(404, {"error": "not found"})


class CollectionWorkerOpsAgent(win32serviceutil.ServiceFramework):
    _svc_name_ = "AIShoppingAgentOpsAgent"
    _svc_display_name_ = "AIShoppingAgent Collection Worker Ops Agent"
    _svc_description_ = (
        "Supervisiona o collection_worker nativo (TASK-109): detecta queda via "
        "estado da Scheduled Task e aciona restart. Nao controla o Edge."
    )

    def __init__(self, args) -> None:
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._httpd: HTTPServer | None = None

    def SvcStop(self) -> None:  # noqa: N802
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        _MONITOR.stop()
        if self._httpd is not None:
            self._httpd.shutdown()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self) -> None:  # noqa: N802
        _configure_logging()
        logger.info("Ops Agent iniciando (TASK-109)")
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        _MONITOR.start()
        self._httpd = HTTPServer((HTTP_HOST, HTTP_PORT), OpsAgentHandler)
        http_thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        http_thread.start()
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        logger.info("Ops Agent finalizado")


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(CollectionWorkerOpsAgent)
