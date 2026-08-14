"""Testes do circuit breaker de `PlaywrightStoreProvider` (TASK-083).

Nomes de `source_code` exclusivos por teste: `CIRCUITS` é um registro
global persistente por processo (mesma lição da TASK-083/SUBETAPA 2 para o
AI Provider Manager) -- compartilhar uma chave com outro teste ou com um
provider de produção poluiria estado entre eles.
"""

from uuid import uuid4

import pytest
from app.collection.providers.base import PlaywrightStoreProvider
from app.core.resilience import CircuitOpenError


def _make_provider(
    source_code: str, *, circuit_namespace: str = "search"
) -> PlaywrightStoreProvider:
    class _DummyProvider(PlaywrightStoreProvider):
        pass

    _DummyProvider.source_code = source_code
    _DummyProvider.result_selector = "a"
    return _DummyProvider(
        circuit_failure_threshold=1, circuit_namespace=circuit_namespace
    )


def test_default_circuit_namespace_preserves_existing_key() -> None:
    """Duas instâncias com o mesmo source_code e namespace default
    compartilham o mesmo circuito -- comportamento inalterado desde antes
    da TASK-083."""
    source_code = f"dummy-{uuid4().hex[:8]}"
    first = _make_provider(source_code)
    second = _make_provider(source_code)

    first._circuit.record_failure(transient=True)

    with pytest.raises(CircuitOpenError):
        second._circuit.before_call()


def test_distinct_circuit_namespace_isolates_failures() -> None:
    """Um namespace diferente (ex.: resolução de identidade) nunca abre o
    circuito usado pela coleta normal da mesma fonte, e vice-versa."""
    source_code = f"dummy-{uuid4().hex[:8]}"
    collection_provider = _make_provider(source_code, circuit_namespace="search")
    identity_provider = _make_provider(
        source_code, circuit_namespace="identity_resolution"
    )

    identity_provider._circuit.record_failure(transient=True)

    with pytest.raises(CircuitOpenError):
        identity_provider._circuit.before_call()
    collection_provider._circuit.before_call()  # não levanta -- circuito distinto


def test_circuit_namespace_rejects_blank_value() -> None:
    source_code = f"dummy-{uuid4().hex[:8]}"
    with pytest.raises(ValueError, match="circuit_namespace"):
        _make_provider(source_code, circuit_namespace="   ")
