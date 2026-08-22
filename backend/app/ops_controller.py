"""Controlador mínimo; Docker fica restrito a este adapter de runtime."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
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
_SEEN_NONCES: dict[str, float] = {}


def _secret() -> bytes:
    with open(_SECRET_FILE, "rb") as file:
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


_ADAPTER = DockerOpsAdapter()


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
    return _ADAPTER.execute(command)
