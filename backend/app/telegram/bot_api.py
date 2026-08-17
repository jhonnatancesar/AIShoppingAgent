"""Chamadas de saída à Bot API real do Telegram.

Usa somente a biblioteca padrão (`urllib`), sem SDK do Telegram. A resposta
da Bot API nunca inclui o token nem o segredo de volta, então pode ser
tratada e logada com segurança; a URL chamada (que contém o token) nunca é
exposta fora deste módulo.
"""

import asyncio
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import SecretStr

from app.core.resilience import CIRCUITS, CircuitOpenError, parse_retry_after
from app.observability.metrics import observe_resilience_event

_TELEGRAM_CIRCUIT_KEY = "telegram:bot_api:delivery"
_MEDIA_REJECTION_MARKERS = (
    "failed to get http url content",
    "wrong type of the web page content",
    "wrong file identifier/http url specified",
    "photo_invalid_dimensions",
    "image_process_failed",
)


class TelegramDeliveryError(RuntimeError):
    """Falha sanitizada da entrega, classificada sem expor a URL/token."""

    def __init__(
        self,
        code: str,
        *,
        transient: bool,
        ambiguous: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.code = code
        self.transient = transient
        self.ambiguous = ambiguous
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


class TelegramBotAPIError(TelegramDeliveryError):
    """Indica que a Bot API recebeu a chamada, mas rejeitou a operação."""

    def __init__(
        self,
        error_code: int | None = None,
        *,
        description: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.error_code = error_code
        transient = error_code == 429 or (
            isinstance(error_code, int) and error_code >= 500
        )
        super().__init__(
            "telegram_api_rejected",
            transient=transient,
            retry_after_seconds=retry_after_seconds,
        )


class TelegramMediaRejected(TelegramDeliveryError):
    """A chamada chegou ao Telegram, mas a URL da mídia foi rejeitada."""

    def __init__(self) -> None:
        super().__init__("telegram_media_rejected", transient=False)


class TelegramDeliveryAmbiguous(TelegramDeliveryError):
    def __init__(self) -> None:
        super().__init__("telegram_delivery_ambiguous", transient=True, ambiguous=True)


class TelegramBotAPIUnavailable(TelegramDeliveryError):
    def __init__(self, code: str = "telegram_api_unavailable") -> None:
        super().__init__(code, transient=True)


def call_bot_api(
    method: str,
    params: dict[str, object] | None = None,
    *,
    bot_token: SecretStr,
    timeout_seconds: float = 10.0,
    retry_after_cap_seconds: float = 30.0,
) -> dict:
    """Chama um método síncrono da Bot API e devolve a resposta decodificada."""
    token = bot_token.get_secret_value()
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(params or {}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read())
    except HTTPError as error:
        try:
            return json.loads(error.read())
        except json.JSONDecodeError, UnicodeDecodeError:
            return {"ok": False, "error_code": error.code}
    except URLError as error:
        if isinstance(error.reason, (socket.gaierror, ConnectionRefusedError)):
            raise TelegramBotAPIUnavailable() from None
        raise TelegramDeliveryAmbiguous() from None
    except TimeoutError:
        raise TelegramDeliveryAmbiguous() from None


async def send_message(
    chat_id: int,
    text: str,
    *,
    bot_token: SecretStr,
    timeout_seconds: float = 10.0,
    retry_after_cap_seconds: float = 30.0,
    circuit_failure_threshold: int = 5,
    circuit_open_seconds: float = 30.0,
) -> None:
    """Envia uma mensagem de texto para uma conversa, sem bloquear o loop de eventos."""
    circuit = CIRCUITS.get(
        _TELEGRAM_CIRCUIT_KEY,
        failure_threshold=circuit_failure_threshold,
        open_seconds=circuit_open_seconds,
    )
    try:
        circuit.before_call()
    except CircuitOpenError:
        observe_resilience_event("telegram", "circuit_open")
        raise TelegramBotAPIUnavailable("telegram_api_circuit_open") from None
    try:
        response = await asyncio.to_thread(
            call_bot_api,
            "sendMessage",
            {"chat_id": chat_id, "text": text},
            bot_token=bot_token,
            timeout_seconds=timeout_seconds,
            retry_after_cap_seconds=retry_after_cap_seconds,
        )
        if response.get("ok") is not True:
            raw_error_code = response.get("error_code")
            error_code = raw_error_code if isinstance(raw_error_code, int) else None
            raw_retry_after = response.get("parameters", {}).get("retry_after")
            retry_after = parse_retry_after(
                str(raw_retry_after) if raw_retry_after is not None else None,
                cap_seconds=retry_after_cap_seconds,
            )
            description = response.get("description")
            raise TelegramBotAPIError(
                error_code,
                description=description if isinstance(description, str) else None,
                retry_after_seconds=retry_after,
            )
    except TelegramDeliveryError as error:
        circuit.record_failure(transient=error.transient)
        raise
    circuit.record_success()


async def send_photo(
    chat_id: int,
    photo_url: str,
    caption: str,
    *,
    bot_token: SecretStr,
    timeout_seconds: float = 10.0,
    retry_after_cap_seconds: float = 30.0,
    circuit_failure_threshold: int = 5,
    circuit_open_seconds: float = 30.0,
) -> None:
    """Envia foto por URL; rejeição específica da mídia é distinguível."""
    if len(caption) > 1024:
        raise TelegramDeliveryError("telegram_caption_too_long", transient=False)
    circuit = CIRCUITS.get(
        _TELEGRAM_CIRCUIT_KEY,
        failure_threshold=circuit_failure_threshold,
        open_seconds=circuit_open_seconds,
    )
    try:
        circuit.before_call()
    except CircuitOpenError:
        observe_resilience_event("telegram", "circuit_open")
        raise TelegramBotAPIUnavailable("telegram_api_circuit_open") from None
    try:
        response = await asyncio.to_thread(
            call_bot_api,
            "sendPhoto",
            {"chat_id": chat_id, "photo": photo_url, "caption": caption},
            bot_token=bot_token,
            timeout_seconds=timeout_seconds,
            retry_after_cap_seconds=retry_after_cap_seconds,
        )
        if response.get("ok") is not True:
            description = response.get("description")
            normalized = description.lower() if isinstance(description, str) else ""
            if any(marker in normalized for marker in _MEDIA_REJECTION_MARKERS):
                raise TelegramMediaRejected
            raw_error_code = response.get("error_code")
            error_code = raw_error_code if isinstance(raw_error_code, int) else None
            raw_retry_after = response.get("parameters", {}).get("retry_after")
            retry_after = parse_retry_after(
                str(raw_retry_after) if raw_retry_after is not None else None,
                cap_seconds=retry_after_cap_seconds,
            )
            raise TelegramBotAPIError(
                error_code,
                description=description if isinstance(description, str) else None,
                retry_after_seconds=retry_after,
            )
    except TelegramMediaRejected:
        # A Bot API está saudável; só a mídia específica é inválida. Não
        # contaminar o circuito compartilhado que fará o fallback textual.
        circuit.record_success()
        raise
    except TelegramDeliveryError as error:
        circuit.record_failure(transient=error.transient)
        raise
    circuit.record_success()
