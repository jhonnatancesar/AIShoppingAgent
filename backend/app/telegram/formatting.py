"""Formatação compartilhada das mensagens principais do Telegram (TASK-063).

Templates continuam fixos no código; nada aqui é gerado por IA — só
funções puras de apresentação (dinheiro, rótulo/ícone de status de missão,
lista de lojas) reaproveitadas por `router.py`, `confirmation.py` e
`notifications.py` para manter os mesmos formatos em todas as mensagens.
"""

from collections.abc import Iterable
from decimal import Decimal

from app.missions.models import MissionStatus

_CURRENCY_SYMBOLS: dict[str, str] = {"BRL": "R$"}

MISSION_STATUS_LABELS: dict[MissionStatus, str] = {
    MissionStatus.DRAFT: "rascunho",
    MissionStatus.ACTIVE: "ativa",
    MissionStatus.PAUSED: "pausada",
    MissionStatus.COMPLETED: "concluída",
    MissionStatus.CANCELLED: "cancelada",
    MissionStatus.EXPIRED: "expirada",
}

MISSION_STATUS_ICONS: dict[MissionStatus, str] = {
    MissionStatus.DRAFT: "📝",
    MissionStatus.ACTIVE: "🟢",
    MissionStatus.PAUSED: "⏸️",
    MissionStatus.COMPLETED: "✅",
    MissionStatus.CANCELLED: "❌",
    MissionStatus.EXPIRED: "⌛",
}


def format_money(amount: Decimal, currency: str) -> str:
    """Formata um valor monetário no padrão brasileiro (`R$ 1.234,56`).

    Moedas sem símbolo mapeado usam o próprio código ISO como prefixo.
    """
    formatted = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    prefix = _CURRENCY_SYMBOLS.get(currency, currency)
    return f"{prefix} {formatted}"


def format_mission_status(status: MissionStatus) -> str:
    """Rótulo em português para exibição — nunca o valor bruto do enum."""
    return MISSION_STATUS_LABELS[status]


def format_store_list(codes: Iterable[str]) -> str:
    """Nomes de loja capitalizados para exibição a partir dos códigos internos."""
    return ", ".join(code.capitalize() for code in codes)
