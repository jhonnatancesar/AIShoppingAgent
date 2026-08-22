"""Registro manual dos comandos do bot contra a Bot API real do Telegram."""

from app.core.config import get_settings
from app.telegram.bot_api import call_bot_api

_COMMANDS = [
    {"command": "start", "description": "Iniciar o bot"},
    {"command": "ajuda", "description": "Ver ajuda"},
    {"command": "criar_missao", "description": "Criar uma nova missão"},
    {"command": "cancelar_missao", "description": "Cancelar uma ou mais missões"},
    {"command": "pausar", "description": "Pausar uma ou mais missões ativas"},
    {"command": "retomar", "description": "Retomar uma ou mais missões pausadas"},
    {
        "command": "listar_missoes",
        "description": "Listar missões ativas, pausadas e canceladas",
    },
    {"command": "missao", "description": "Entender o fluxo de missões"},
    {
        "command": "editar_missao",
        "description": "Editar lojas ou preço-alvo de uma missão",
    },
    {"command": "cadastro", "description": "Cadastrar dados básicos do seu perfil"},
    {"command": "entrar", "description": "Autenticar por senha"},
    {"command": "recuperar", "description": "Criar ou recuperar sua senha"},
    {"command": "sair", "description": "Encerrar a sessão autenticada"},
    {"command": "vincular", "description": "Vincular uma conta Web com código"},
    {"command": "preferencias", "description": "Configurar notificações"},
    {"command": "privacidade", "description": "Consultar uso e proteção de dados"},
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
