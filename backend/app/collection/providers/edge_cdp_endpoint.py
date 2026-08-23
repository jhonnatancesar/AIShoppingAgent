"""Validação genérica de endpoint CDP loopback.

Compartilhada por `EdgeCdpSupervisor` e `EdgeCdpTransport` (TASK-109) --
nenhum dos dois é específico de loja; extraído de `magalu_transport.py`
para não obrigar infraestrutura genérica a depender de um módulo
nomeado por uma única loja.
"""

from app.core.urls import normalize_loopback_http_endpoint


def validate_loopback_cdp_endpoint(endpoint: str) -> str:
    """Aceita somente CDP HTTP local; nunca conecta a porta pública/remota."""
    value = normalize_loopback_http_endpoint(endpoint)
    if value is None:
        raise ValueError("Edge CDP endpoint must be loopback HTTP with explicit port")
    return value
