"""Contexto seguro de correlação para logs e requisições."""

from contextvars import ContextVar, Token
from uuid import UUID, uuid4

REQUEST_ID_HEADER = "X-Request-ID"
MAX_EXTERNAL_REQUEST_ID_LENGTH = 64

_request_id: ContextVar[UUID | None] = ContextVar("request_id", default=None)


def accepted_request_id(raw_value: str | None) -> UUID:
    """Aceita somente UUID curto; entradas inválidas nunca são reutilizadas."""
    if raw_value is not None and len(raw_value) <= MAX_EXTERNAL_REQUEST_ID_LENGTH:
        try:
            return UUID(raw_value)
        except ValueError, AttributeError:
            pass
    return uuid4()


def set_request_id(request_id: UUID) -> Token[UUID | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[UUID | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> UUID | None:
    return _request_id.get()
