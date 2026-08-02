"""Utilitários de tempo compartilhados pelos modelos persistentes."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Retorna o instante atual consciente de fuso em UTC."""
    return datetime.now(UTC)
