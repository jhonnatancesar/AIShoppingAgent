"""Registro manual do webhook contra a Bot API real do Telegram."""

import argparse

from app.core.config import get_settings
from app.telegram.bot_api import call_bot_api


def _require_success(operation: str, response: dict) -> dict:
    if response.get("ok") is not True:
        code = response.get("error_code", "unknown")
        raise SystemExit(f"Telegram {operation} failed with error_code={code}")
    return response


def set_webhook(url: str) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("AISHOPPING_TELEGRAM_BOT_TOKEN is required")
    if settings.telegram_webhook_secret is None:
        raise SystemExit("AISHOPPING_TELEGRAM_WEBHOOK_SECRET is required")
    secret = settings.telegram_webhook_secret.get_secret_value()
    print(
        _require_success(
            "setWebhook",
            call_bot_api(
                "setWebhook",
                {"url": url, "secret_token": secret},
                bot_token=settings.telegram_bot_token,
            ),
        )
    )


def delete_webhook() -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("AISHOPPING_TELEGRAM_BOT_TOKEN is required")
    print(
        _require_success(
            "deleteWebhook",
            call_bot_api("deleteWebhook", bot_token=settings.telegram_bot_token),
        )
    )


def webhook_info() -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("AISHOPPING_TELEGRAM_BOT_TOKEN is required")
    print(
        _require_success(
            "getWebhookInfo",
            call_bot_api("getWebhookInfo", bot_token=settings.telegram_bot_token),
        )
    )


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
