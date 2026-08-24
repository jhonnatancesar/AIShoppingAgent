"""Validação genérica de endpoint CDP loopback.

Compartilhada por `EdgeCdpSupervisor` e `EdgeCdpTransport` (TASK-109) --
nenhum dos dois é específico de loja; extraído de `magalu_transport.py`
para não obrigar infraestrutura genérica a depender de um módulo
nomeado por uma única loja.

A descoberta do executável do Edge (`discover_edge_executable`) mora em
`app.collection.edge_discovery`, fora do pacote `providers` -- também é
usada por `BrowserSession` (`app/collection/browser.py`, TASK-109
fechamento), que não pode depender deste pacote sem criar import
circular."""

from app.core.urls import normalize_loopback_http_endpoint


def validate_loopback_cdp_endpoint(endpoint: str) -> str:
    """Aceita somente CDP HTTP local; nunca conecta a porta pública/remota."""
    value = normalize_loopback_http_endpoint(endpoint)
    if value is None:
        raise ValueError("Edge CDP endpoint must be loopback HTTP with explicit port")
    return value
