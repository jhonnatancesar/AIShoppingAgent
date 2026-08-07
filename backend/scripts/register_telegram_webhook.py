"""Registro manual do webhook contra a Bot API real do Telegram.

Usa somente a biblioteca padrão (`urllib`), sem SDK do Telegram: a resposta
da Bot API nunca inclui o token nem o segredo de volta, então ela pode ser
impressa com segurança; a URL chamada (que contém o token) nunca é impressa.
"""

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import get_settings


def _call(method: str, params: dict[str, str] | None = None) -> dict:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("AISHOPPING_TELEGRAM_BOT_TOKEN is required")
    token = settings.telegram_bot_token.get_secret_value()
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
        raise SystemExit(f"telegram api unreachable: {error.reason}") from error


def set_webhook(url: str) -> None:
    settings = get_settings()
    if settings.telegram_webhook_secret is None:
        raise SystemExit("AISHOPPING_TELEGRAM_WEBHOOK_SECRET is required")
    secret = settings.telegram_webhook_secret.get_secret_value()
    print(_call("setWebhook", {"url": url, "secret_token": secret}))


def delete_webhook() -> None:
    print(_call("deleteWebhook"))


def webhook_info() -> None:
    print(_call("getWebhookInfo"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["set", "delete", "info"], required=True)
    parser.add_argument(
        "--url", help="URL pública HTTPS; obrigatória para --action set"
    )
    args = parser.parse_args()

    if args.action == "set":
        if not args.url:
            raise SystemExit("--url is required for --action set")
        set_webhook(args.url)
    elif args.action == "delete":
        delete_webhook()
    else:
        webhook_info()


if __name__ == "__main__":
    main()
