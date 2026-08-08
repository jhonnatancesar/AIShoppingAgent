"""Chamadas de saída à Bot API real do Telegram.

Usa somente a biblioteca padrão (`urllib`), sem SDK do Telegram. A resposta
da Bot API nunca inclui o token nem o segredo de volta, então pode ser
tratada e logada com segurança; a URL chamada (que contém o token) nunca é
exposta fora deste módulo.
"""

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import SecretStr


class TelegramBotAPIError(RuntimeError):
    """Indica que a Bot API recebeu a chamada, mas rejeitou a operação."""

    def __init__(self, error_code: int | None = None) -> None:
        self.error_code = error_code
        suffix = f" ({error_code})" if error_code is not None else ""
        super().__init__(f"telegram api rejected the request{suffix}")


def call_bot_api(
    method: str,
    params: dict[str, object] | None = None,
    *,
    bot_token: SecretStr,
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
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except HTTPError as error:
        return json.loads(error.read())
    except URLError as error:
        raise ConnectionError(f"telegram api unreachable: {error.reason}") from error


async def send_message(chat_id: int, text: str, *, bot_token: SecretStr) -> None:
    """Envia uma mensagem de texto para uma conversa, sem bloquear o loop de eventos."""
    response = await asyncio.to_thread(
        call_bot_api,
        "sendMessage",
        {"chat_id": chat_id, "text": text},
        bot_token=bot_token,
    )
    if response.get("ok") is not True:
        raw_error_code = response.get("error_code")
        error_code = raw_error_code if isinstance(raw_error_code, int) else None
        raise TelegramBotAPIError(error_code)
