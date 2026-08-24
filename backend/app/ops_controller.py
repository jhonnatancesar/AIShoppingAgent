"""Controlador mínimo; Docker e o Windows Ops Agent ficam restritos a um
adapter de runtime cada -- nenhuma outra parte do sistema fala com eles
diretamente."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as secrets_module
import time
import urllib.parse
import urllib.request
from enum import StrEnum

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel


class LogicalService(StrEnum):
    COLLECTION_WORKER = "collection_worker"
    TELEGRAM_NOTIFIER = "telegram_notifier"


class Operation(StrEnum):
    STATUS = "status"
    START = "start"
    RESTART = "restart"


class Command(BaseModel):
    service: LogicalService
    operation: Operation


app = FastAPI(
    title="AIShoppingAgent Ops Controller",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
_DOCKER = os.getenv("DOCKER_HOST", "http://docker-socket-proxy:2375").rstrip("/")
_SECRET_FILE = os.getenv(
    "OPS_CONTROLLER_SECRET_FILE", "/run/secrets/ops_controller_secret"
)
# TASK-109 (fechamento): `collection_worker` não roda mais em Docker --
# `host.docker.internal` é resolvido pelo Docker Desktop for Windows para
# o host, inclusive portas em loopback (127.0.0.1) do host, sem
# `extra_hosts`/rede nova.
_WINDOWS_OPS_AGENT_URL = os.getenv(
    "WINDOWS_OPS_AGENT_URL", "http://host.docker.internal:8021"
).rstrip("/")
_WINDOWS_OPS_AGENT_SECRET_FILE = os.getenv(
    "WINDOWS_OPS_AGENT_SECRET_FILE", "/run/secrets/windows_ops_agent_secret"
)
_WINDOWS_OPS_AGENT_TIMEOUT_SECONDS = 5
_SEEN_NONCES: dict[str, float] = {}


def _secret() -> bytes:
    with open(_SECRET_FILE, "rb") as file:
        return file.read().strip()


def _windows_ops_agent_secret() -> bytes:
    with open(_WINDOWS_OPS_AGENT_SECRET_FILE, "rb") as file:
        return file.read().strip()


class DockerOpsAdapter:
    """Único ponto que conhece Docker; a allowlist vem do enum lógico."""

    def _request(self, method: str, path: str) -> object:
        request = urllib.request.Request(f"{_DOCKER}{path}", method=method)
        with urllib.request.urlopen(request, timeout=3) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}

    def _container(self, service: LogicalService) -> dict | None:
        filters = urllib.parse.quote(
            json.dumps({"label": [f"com.docker.compose.service={service.value}"]})
        )
        rows = self._request("GET", f"/containers/json?all=1&filters={filters}")
        return rows[0] if isinstance(rows, list) and len(rows) == 1 else None

    def execute(self, command: Command) -> dict[str, str]:
        container = self._container(command.service)
        if container is None:
            return {
                "service": command.service.value,
                "status": "unavailable",
                "detail": "service not found",
            }
        state, container_id = container.get("State", "unknown"), container["Id"]
        if command.operation is Operation.START and state != "running":
            self._request("POST", f"/containers/{container_id}/start")
            state = "running"
        elif command.operation is Operation.RESTART:
            self._request("POST", f"/containers/{container_id}/restart?t=10")
            state = "running"
        status_text = str(container.get("Status", "")).casefold()
        if state == "running":
            logical_state = "healthy" if "(healthy)" in status_text else "running"
        elif state in {"created", "exited", "dead", "paused"}:
            logical_state = "stopped"
        else:
            logical_state = "unknown"
        return {"service": command.service.value, "status": logical_state}


class WindowsOpsAgentAdapter:
    """Único ponto que fala com o Windows Ops Agent; allowlist vem do
    enum lógico (`Operation`), nunca de shell/comando genérico."""

    _ROUTES: dict[Operation, str] = {
        Operation.STATUS: "/v1/collection_worker/status",
        Operation.START: "/v1/collection_worker/start",
        Operation.RESTART: "/v1/collection_worker/restart",
    }
    # Get-ScheduledTask.State (TASK-109/ops_agent) -- sempre em inglês,
    # independente do idioma do SO (ver ops_agent/collection_worker_ops_agent.py
    # _query_task_state). "Unavailable" é o próprio fallback do Ops Agent
    # quando a consulta da task falha (ex.: task não existe).
    _TASK_STATE_TO_LOGICAL_STATE = {
        "Running": "running",
        "Ready": "stopped",
        "Queued": "stopped",
        "Disabled": "stopped",
        "Unavailable": "unavailable",
    }

    def _signed_headers(self, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = secrets_module.token_hex(16)
        signature = hmac.new(
            _windows_ops_agent_secret(),
            timestamp.encode() + b"." + nonce.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Ops-Timestamp": timestamp,
            "X-Ops-Nonce": nonce,
            "X-Ops-Signature": signature,
        }

    def _post(self, route: str) -> dict:
        body = b""
        request = urllib.request.Request(
            f"{_WINDOWS_OPS_AGENT_URL}{route}",
            data=body,
            method="POST",
            headers=self._signed_headers(body),
        )
        with urllib.request.urlopen(
            request, timeout=_WINDOWS_OPS_AGENT_TIMEOUT_SECONDS
        ) as response:
            return json.loads(response.read())

    def execute(self, command: Command) -> dict[str, str]:
        payload = self._post(self._ROUTES[command.operation])
        if command.operation is Operation.STATUS:
            task_state = str(payload.get("task_state", ""))
        else:
            if payload.get("status") == "already_running":
                task_state = "Running"
            elif payload.get("triggered"):
                task_state = "Running"
            else:
                task_state = ""
        logical_state = self._TASK_STATE_TO_LOGICAL_STATE.get(task_state, "unknown")
        return {"service": command.service.value, "status": logical_state}


_ADAPTER = DockerOpsAdapter()
_WINDOWS_ADAPTER = WindowsOpsAgentAdapter()
_ADAPTERS: dict[LogicalService, object] = {
    LogicalService.COLLECTION_WORKER: _WINDOWS_ADAPTER,
    LogicalService.TELEGRAM_NOTIFIER: _ADAPTER,
}


@app.post("/v1/services/execute")
async def execute(
    command: Command,
    request: Request,
    x_ops_timestamp: str = Header(),
    x_ops_signature: str = Header(),
    x_ops_nonce: str = Header(),
):
    body = await request.body()
    try:
        if abs(time.time() - int(x_ops_timestamp)) > 30:
            raise ValueError
    except ValueError as error:
        raise HTTPException(401, "invalid signature") from error
    expected = hmac.new(
        _secret(),
        x_ops_timestamp.encode() + b"." + x_ops_nonce.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, x_ops_signature):
        raise HTTPException(401, "invalid signature")
    now = time.time()
    for nonce, seen_at in tuple(_SEEN_NONCES.items()):
        if now - seen_at > 60:
            _SEEN_NONCES.pop(nonce, None)
    if not x_ops_nonce or x_ops_nonce in _SEEN_NONCES:
        raise HTTPException(409, "replayed request")
    _SEEN_NONCES[x_ops_nonce] = now
    return _ADAPTERS[command.service].execute(command)
