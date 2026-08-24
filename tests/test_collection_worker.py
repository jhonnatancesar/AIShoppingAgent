"""Inicialização e ciclo rápido do worker de coleta."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.collection.identity_resolution import StoreProductIdentityResolver
from app.collection.providers.edge_cdp_transport import EdgeCdpTransport
from app.collection.providers.magalu_transport import CdpMagaluSearchTransport
from app.collection.worker import (
    build_collection_adapter,
    run_worker,
    start_edge_supervisor,
)
from app.core.config import Settings
from app.observability.metrics import mark_worker_started, observe_worker_failure


def test_build_adapter_registers_exactly_v1_sources() -> None:
    adapter = build_collection_adapter(Settings(_env_file=None))
    assert adapter.supported_sources == (
        "amazon",
        "kabum",
        "magalu",
        "mercadolivre",
        "pichau",
        "terabyte",
    )


def test_build_adapter_uses_headed_only_for_configured_sources() -> None:
    """Pichau/Terabyte/Magalu exigem headed (Xvfb) na coleta real; ver
    docs/architecture/playwright.md e backend/scripts/validate_store_providers.py."""
    adapter = build_collection_adapter(Settings(_env_file=None))

    assert adapter._providers["pichau"].settings.headless is False
    assert adapter._providers["terabyte"].settings.headless is False
    assert adapter._providers["amazon"].settings.headless is True
    assert adapter._providers["kabum"].settings.headless is True
    assert adapter._providers["magalu"].settings.headless is False
    assert adapter._providers["mercadolivre"].settings.headless is False


def test_build_adapter_decouples_navigation_timeout_from_action_timeout() -> None:
    """TASK-075 (correção): navegação de página (Playwright) usa seu próprio
    timeout, desacoplado do timeout de chamada de API/HTTP externo."""
    settings = Settings(_env_file=None)
    adapter = build_collection_adapter(settings)

    for provider in adapter._providers.values():
        assert provider.settings.action_timeout_ms == int(
            settings.external_http_timeout_seconds * 1000
        )
        assert provider.settings.navigation_timeout_ms == int(
            settings.browser_navigation_timeout_seconds * 1000
        )
    assert settings.external_http_timeout_seconds == 10.0
    assert settings.browser_navigation_timeout_seconds == 45.0


def test_build_adapter_uses_configured_loopback_cdp_for_magalu_and_ml_primary() -> None:
    adapter = build_collection_adapter(
        Settings(edge_cdp_url="http://127.0.0.1:9223", _env_file=None)
    )

    transport = adapter._providers["magalu"]._search_transport
    ml_transport = adapter._providers["mercadolivre"]._cdp_transport

    assert isinstance(transport, CdpMagaluSearchTransport)
    assert transport.endpoint == "http://127.0.0.1:9223"
    assert isinstance(ml_transport, EdgeCdpTransport)
    assert ml_transport.endpoint == "http://127.0.0.1:9223"


def test_build_adapter_uses_configured_loopback_cdp_for_amazon_and_kabum() -> None:
    adapter = build_collection_adapter(
        Settings(edge_cdp_url="http://127.0.0.1:9223", _env_file=None)
    )

    amazon_transport = adapter._providers["amazon"]._cdp_transport
    kabum_transport = adapter._providers["kabum"]._cdp_transport

    assert isinstance(amazon_transport, EdgeCdpTransport)
    assert amazon_transport.endpoint == "http://127.0.0.1:9223"
    assert isinstance(kabum_transport, EdgeCdpTransport)
    assert kabum_transport.endpoint == "http://127.0.0.1:9223"


def test_build_adapter_uses_configured_loopback_cdp_for_terabyte_primary() -> None:
    """TASK-105: mesma infraestrutura CDP da Magalu, reaproveitada como
    transporte primário -- e único -- da Terabyte."""
    adapter = build_collection_adapter(
        Settings(edge_cdp_url="http://127.0.0.1:9223", _env_file=None)
    )

    transport = adapter._providers["terabyte"]._cdp_transport

    assert isinstance(transport, EdgeCdpTransport)
    assert transport.endpoint == "http://127.0.0.1:9223"


def test_build_adapter_leaves_terabyte_cdp_unset_without_configured_endpoint() -> None:
    """Sem `edge_cdp_url`, a Terabyte não ganha transporte nenhum -- nunca
    cai de volta ao Playwright, já comprovadamente bloqueado (DEC-070)."""
    adapter = build_collection_adapter(Settings(_env_file=None))

    assert adapter._providers["terabyte"]._cdp_transport is None


def test_unavailable_edge_does_not_block_worker_setup(monkeypatch) -> None:
    class UnavailableSupervisor:
        def __init__(self, *args, **kwargs):
            from app.collection.providers.edge_cdp_supervisor import (
                EdgeCdpSupervisorError,
            )

            raise EdgeCdpSupervisorError("Edge unavailable")

    monkeypatch.setattr(
        "app.collection.worker.EdgeCdpSupervisor", UnavailableSupervisor
    )
    settings = Settings(edge_cdp_url="http://127.0.0.1:9223", _env_file=None)

    assert asyncio.run(start_edge_supervisor(settings)) is None


def test_collection_worker_is_an_allowlisted_metric_dimension() -> None:
    mark_worker_started("collection_orchestrator")
    observe_worker_failure("collection_orchestrator")


def test_worker_once_records_batch_and_disposes(monkeypatch) -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    factory = MagicMock()
    orchestrator = MagicMock()
    orchestrator.run_batch = MagicMock(
        return_value=SimpleNamespace(
            __await__=lambda self: iter(()),
        )
    )

    async def result(*_args, **_kwargs):
        return SimpleNamespace(claimed=2, succeeded=1, failed=1, recovered_stale=0)

    orchestrator.run_batch = result
    observe = MagicMock()
    captured_kwargs: dict[str, object] = {}

    def _capture_orchestrator(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return orchestrator

    monkeypatch.setattr(
        "app.collection.worker.create_collection_async_database_engine",
        lambda *_, **__: engine,
    )
    monkeypatch.setattr(
        "app.collection.worker.create_async_session_factory", lambda *_: factory
    )
    monkeypatch.setattr("app.collection.worker.build_collection_adapter", MagicMock())
    monkeypatch.setattr(
        "app.collection.worker.build_admin_dev_ai_provider_manager", MagicMock()
    )
    monkeypatch.setattr(
        "app.collection.worker.CollectionOrchestrator", _capture_orchestrator
    )
    monkeypatch.setattr("app.collection.worker.observe_worker_batch", observe)
    monkeypatch.setattr(
        "app.collection.worker.trace.get_tracer",
        lambda *_: SimpleNamespace(
            start_as_current_span=lambda *_a, **_k: nullcontext()
        ),
    )

    asyncio.run(run_worker(Settings(_env_file=None), once=True))

    observe.assert_called_once()
    engine.dispose.assert_called_once()
    assert isinstance(
        captured_kwargs["identity_resolver"], StoreProductIdentityResolver
    )


def test_worker_identity_resolver_uses_dedicated_kabum_amazon_providers() -> None:
    """TASK-083: nunca reutiliza os providers da coleta normal -- instâncias
    próprias, namespace de circuito separado ("identity")."""
    resolver = StoreProductIdentityResolver()
    source_codes = [provider.source_code for provider in resolver._providers]  # noqa: SLF001
    assert source_codes == ["kabum", "amazon"]


@pytest.mark.parametrize(("poll", "batch"), [(0, 1), (1, 0), (1, 1001)])
def test_worker_rejects_invalid_limits(poll: float, batch: int) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            run_worker(
                Settings(_env_file=None), once=True, poll_seconds=poll, batch_size=batch
            )
        )
