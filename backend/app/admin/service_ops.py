"""Contrato runtime-neutral para operações de serviços lógicos."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum

from app.core.config import Settings


class ManagedService(StrEnum):
    COLLECTION_WORKER = "collection_worker"
    TELEGRAM_NOTIFIER = "telegram_notifier"


class ServiceOperation(StrEnum):
    STATUS = "status"
    START = "start"
    RESTART = "restart"


@dataclass(frozen=True)
class ServiceState:
    service: ManagedService
    status: str
    detail: str | None = None


class ServiceOpsUnavailable(RuntimeError):
    pass


class ServiceOps:
    def status(self, service: ManagedService) -> ServiceState: ...
    def execute(
        self, service: ManagedService, operation: ServiceOperation
    ) -> ServiceState: ...


class ControllerServiceOps(ServiceOps):
    """Adapter HTTP; desconhece Docker e usa somente nomes lógicos."""

    def __init__(self, settings: Settings):
        self._url = settings.ops_controller_url
        self._secret = (
            settings.ops_controller_secret.get_secret_value()
            if settings.ops_controller_secret
            else None
        )

    def status(self, service: ManagedService) -> ServiceState:
        return self._request(service, ServiceOperation.STATUS)

    def execute(
        self, service: ManagedService, operation: ServiceOperation
    ) -> ServiceState:
        return self._request(service, operation)

    def _request(
        self, service: ManagedService, operation: ServiceOperation
    ) -> ServiceState:
        if not self._url or not self._secret:
            raise ServiceOpsUnavailable("ops_controller não configurado")
        body = json.dumps(
            {"service": service.value, "operation": operation.value},
            separators=(",", ":"),
        ).encode()
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        signature = hmac.new(
            self._secret.encode(),
            timestamp.encode() + b"." + nonce.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            f"{self._url.rstrip('/')}/v1/services/execute",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Ops-Timestamp": timestamp,
                "X-Ops-Signature": signature,
                "X-Ops-Nonce": nonce,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, ValueError) as error:
            raise ServiceOpsUnavailable("ops_controller indisponível") from error
        return ServiceState(
            service=service, status=str(payload["status"]), detail=payload.get("detail")
        )
