"""Registro manual dos comandos do bot contra a Bot API real do Telegram."""

from app.core.config import get_settings
from app.telegram.bot_api import call_bot_api

_COMMANDS = [
    {"command": "start", "description": "Iniciar o bot"},
    {"command": "ajuda", "description": "Ver ajuda"},
    {"command": "cadastro", "description": "Cadastrar dados básicos do seu perfil"},
    {"command": "senha", "description": "Criar ou alterar sua senha"},
    {"command": "entrar", "description": "Autenticar por senha"},
    {"command": "sair", "description": "Encerrar a sessão autenticada"},
    {"command": "recuperar", "description": "Recuperar sua senha"},
    {"command": "preferencias", "description": "Configurar notificações"},
    {"command": "upgrade", "description": "Mudar de perfil (em breve)"},
]


def set_commands() -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("AISHOPPING_TELEGRAM_BOT_TOKEN is required")
    print(
        call_bot_api(
            "setMyCommands",
            {"commands": _COMMANDS},
            bot_token=settings.telegram_bot_token,
        )
    )


if __name__ == "__main__":
    set_commands()
