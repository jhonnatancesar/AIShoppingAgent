"""Testes unitários (`AsyncSession`/`AIProviderManager`/`WebSearchManager`/
`CesarCoreFetchProvider` mockados) das funções `async def` de
`app.market_research.service` (TASK-113) -- mesmo padrão já estabelecido em
`tests/test_historical_bootstrap_service_async.py`: `MagicMock`/`AsyncMock`
configurado via `side_effect`/`return_value`, verificando comportamento real
(branches, chamadas mockadas, exceções, valores persistidos via
`.compile().params`) -- nenhum teste raso de execução de linha. A lógica
pura/quase-pura (gatilho, `finalize_market_research`, parsing) já está
coberta em `tests/test_market_research_service.py`; aqui as funções puras
auxiliares (`resolve_product_variant`, `should_trigger_market_research`,
`_search_with_enrichment`, `_interpret_evidence`, `finalize_market_research`)
são monkeypatchadas quando não são o alvo direto do teste, para isolar a
camada de orquestração sob teste (mesma disciplina "camada por camada" já
usada nos demais arquivos desta suíte)."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.ai_provider.contracts import AIMessageRole
from app.alerts.models import MissionProductAlertState
from app.collection.cadence import CadenceConfig, CadenceDecision
from app.core.config import Settings
from app.historical_bootstrap.models import ExternalPriceReference
from app.market_research.models import (
    AssessmentConfidence,
    MarketAssessmentStatus,
    MarketPriceClassification,
)
from app.market_research.service import (
    AssessmentSnapshot,
    ClaimResult,
    ClaimStatus,
    EvidenceItem,
    MarketResearchOutcome,
    _interpret_evidence,
    _resolve_expiry_with_own_session,
    _search_with_enrichment,
    claim_assessment,
    evaluate_trigger_and_maybe_research,
    get_current_assessment_snapshot,
    mark_assessment_failed,
    mark_assessment_ready,
    resolve_assessment_expiry,
    resolve_market_mode_hours,
    resolve_realert_window,
    run_market_research,
)
from app.products.models import Product
from app.search.cesar_core_fetch import CesarCoreFetchError, CesarCoreFetchResult
from app.search.contracts import WebSearchResponse, WebSearchResult
from app.users.models import UserRole

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _async_cm(value=None):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value if value is not None else cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_async_session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    session.begin = MagicMock(side_effect=lambda: _async_cm())
    return session


def _session_factory(*sessions: MagicMock) -> MagicMock:
    return MagicMock(side_effect=list(sessions))


def _execute_result(*, first=None) -> MagicMock:
    """`AsyncMock().return_value` cria filhos também assíncronos por
    padrão (mesma classe do pai) -- `.first()` retornaria uma coroutine
    nunca aguardada em vez do valor configurado. Um `MagicMock()` comum,
    atribuído explicitamente a `session.execute.return_value`, evita
    isso e reproduz o `Result` síncrono real do SQLAlchemy."""
    result = MagicMock()
    result.first.return_value = first
    return result


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def _product(
    *, identity_key: str | None = "v1:asus-tuf-b650", product_id=None
) -> Product:
    return Product(
        id=product_id or uuid4(),
        name="ASUS TUF Gaming B650-Plus WiFi",
        display_name="ASUS TUF Gaming B650-Plus WiFi",
        family_key="asus-tuf-b650",
        identity_key=identity_key,
    )


def _snapshot(**overrides: object) -> AssessmentSnapshot:
    base = dict(
        product_id=uuid4(),
        status=MarketAssessmentStatus.READY,
        classification=MarketPriceClassification.GOOD_DEAL,
        market_low=Decimal("10"),
        market_high=Decimal("20"),
        historical_low_external=None,
        historical_low_source=None,
        historical_low_observed_at=None,
        confidence=AssessmentConfidence.MEDIUM,
        reference_price=Decimal("15"),
        expires_at=NOW + timedelta(hours=1),
    )
    base.update(overrides)
    return AssessmentSnapshot(**base)


class _FakeAssessmentRow:
    def __init__(
        self, *, status: MarketAssessmentStatus = MarketAssessmentStatus.READY
    ) -> None:
        self.product_id = uuid4()
        self.status = status
        self.classification = MarketPriceClassification.GOOD_DEAL
        self.market_low = Decimal("10")
        self.market_high = Decimal("20")
        self.historical_low_external = None
        self.historical_low_source = None
        self.historical_low_observed_at = None
        self.confidence = AssessmentConfidence.HIGH
        self.reference_price = Decimal("15")
        self.expires_at = NOW + timedelta(hours=1)
        self.failure_count = 0


def _search_result(
    url: str, *, title: str = "Título", snippet: str = "Trecho"
) -> WebSearchResult:
    return WebSearchResult(title=title, url=url, snippet=snippet)


def _search_response(results: list[WebSearchResult]) -> WebSearchResponse:
    return WebSearchResponse(
        results=tuple(results),
        provider="cesar_core",
        source="search",
        correlation_id="c1",
    )


def _identity_matcher(monkeypatch, product: Product) -> None:
    """Qualquer texto sem a marca `INCOMPATIBLE` resolve para a MESMA
    identidade do `product`; textos marcados resolvem para outra --
    permite controlar `_evidence_matches_identity` por item de teste sem
    depender do motor determinístico real (já coberto em outro arquivo)."""

    def _resolve(text: str):
        if "INCOMPATIBLE" in text:
            return type("Resolved", (), {"identity_key": "v1:other-product"})()
        return type("Resolved", (), {"identity_key": product.identity_key})()

    monkeypatch.setattr("app.market_research.service.resolve_product_variant", _resolve)


# ---------------------------------------------------------------------------
# resolve_market_mode_hours / resolve_assessment_expiry / resolve_realert_window
# ---------------------------------------------------------------------------


def test_resolve_market_mode_hours_normal_mode(monkeypatch):
    async def _scenario():
        session = _mock_async_session()
        mode_mock = AsyncMock(
            return_value=CadenceDecision(min_minutes=1, max_minutes=2, mode="normal")
        )
        monkeypatch.setattr(
            "app.market_research.service.resolve_product_market_mode", mode_mock
        )

        hours = await resolve_market_mode_hours(
            session, product_id=uuid4(), now=NOW, normal_hours=24, promo_hours=6
        )

        assert hours == 24

    asyncio.run(_scenario())


def test_resolve_market_mode_hours_promo_mode(monkeypatch):
    async def _scenario():
        session = _mock_async_session()
        mode_mock = AsyncMock(
            return_value=CadenceDecision(
                min_minutes=1, max_minutes=2, mode="promo_calendar"
            )
        )
        monkeypatch.setattr(
            "app.market_research.service.resolve_product_market_mode", mode_mock
        )

        hours = await resolve_market_mode_hours(
            session, product_id=uuid4(), now=NOW, normal_hours=24, promo_hours=6
        )

        assert hours == 6

    asyncio.run(_scenario())


def test_resolve_market_mode_hours_passes_cadence_config_through_or_defaults(
    monkeypatch,
):
    async def _scenario():
        session = _mock_async_session()
        mode_mock = AsyncMock(
            return_value=CadenceDecision(min_minutes=1, max_minutes=2, mode="normal")
        )
        monkeypatch.setattr(
            "app.market_research.service.resolve_product_market_mode", mode_mock
        )
        custom_config = CadenceConfig()

        await resolve_market_mode_hours(
            session,
            product_id=uuid4(),
            now=NOW,
            normal_hours=24,
            promo_hours=6,
            cadence_config=custom_config,
        )
        assert mode_mock.call_args.kwargs["config"] is custom_config

        await resolve_market_mode_hours(
            session, product_id=uuid4(), now=NOW, normal_hours=24, promo_hours=6
        )
        assert isinstance(mode_mock.call_args.kwargs["config"], CadenceConfig)

    asyncio.run(_scenario())


def test_resolve_assessment_expiry_adds_resolved_hours_to_now(monkeypatch):
    async def _scenario():
        session = _mock_async_session()
        hours_mock = AsyncMock(return_value=6)
        monkeypatch.setattr(
            "app.market_research.service.resolve_market_mode_hours", hours_mock
        )
        settings = _settings(
            market_assessment_ttl_normal_hours=24, market_assessment_ttl_promo_hours=6
        )

        result = await resolve_assessment_expiry(
            session, product_id=uuid4(), now=NOW, settings=settings
        )

        assert result == NOW + timedelta(hours=6)
        assert hours_mock.call_args.kwargs["normal_hours"] == 24
        assert hours_mock.call_args.kwargs["promo_hours"] == 6

    asyncio.run(_scenario())


def test_resolve_realert_window_uses_realert_hours(monkeypatch):
    async def _scenario():
        session = _mock_async_session()
        hours_mock = AsyncMock(return_value=48)
        monkeypatch.setattr(
            "app.market_research.service.resolve_market_mode_hours", hours_mock
        )
        settings = _settings(realert_normal_hours=168, realert_promo_hours=48)

        result = await resolve_realert_window(
            session, product_id=uuid4(), now=NOW, settings=settings
        )

        assert result == timedelta(hours=48)
        assert hours_mock.call_args.kwargs["normal_hours"] == 168
        assert hours_mock.call_args.kwargs["promo_hours"] == 48

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# get_current_assessment_snapshot
# ---------------------------------------------------------------------------


def test_get_current_assessment_snapshot_maps_row():
    async def _scenario():
        session = _mock_async_session()
        row = _FakeAssessmentRow()
        session.get.return_value = row

        result = await get_current_assessment_snapshot(session, product_id=uuid4())

        assert result.product_id == row.product_id
        assert result.market_low == row.market_low

    asyncio.run(_scenario())


def test_get_current_assessment_snapshot_none_when_row_missing():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = None

        result = await get_current_assessment_snapshot(session, product_id=uuid4())

        assert result is None

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# claim_assessment
# ---------------------------------------------------------------------------


def test_claim_assessment_won_short_circuits_without_reading_row():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _execute_result(first=("won-row",))
        session_factory = _session_factory(session)
        settings = _settings()

        result = await claim_assessment(
            session_factory,
            product_id=uuid4(),
            store_id=uuid4(),
            reference_price=Decimal("10"),
            reference_currency="BRL",
            now=NOW,
            settings=settings,
        )

        assert result == ClaimResult(ClaimStatus.WON, None)
        session.get.assert_not_awaited()
        params = session.execute.call_args.args[1]
        assert params["reference_price"] == Decimal("10")
        assert params["reference_currency"] == "BRL"
        assert params["lease_until"] == NOW + timedelta(
            seconds=settings.market_assessment_lease_seconds
        )

    asyncio.run(_scenario())


def test_claim_assessment_lost_and_row_missing_is_in_progress():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _execute_result(first=None)
        session.get.return_value = None
        session_factory = _session_factory(session)

        result = await claim_assessment(
            session_factory,
            product_id=uuid4(),
            store_id=None,
            reference_price=Decimal("10"),
            reference_currency="BRL",
            now=NOW,
            settings=_settings(),
        )

        assert result == ClaimResult(ClaimStatus.IN_PROGRESS, None)

    asyncio.run(_scenario())


def test_claim_assessment_lost_ready_row_is_cache_hit():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _execute_result(first=None)
        row = _FakeAssessmentRow(status=MarketAssessmentStatus.READY)
        session.get.return_value = row
        session_factory = _session_factory(session)

        result = await claim_assessment(
            session_factory,
            product_id=uuid4(),
            store_id=None,
            reference_price=Decimal("10"),
            reference_currency="BRL",
            now=NOW,
            settings=_settings(),
        )

        assert result.status is ClaimStatus.CACHE_HIT
        assert result.snapshot.product_id == row.product_id

    asyncio.run(_scenario())


def test_claim_assessment_lost_non_ready_row_is_in_progress_with_snapshot():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _execute_result(first=None)
        row = _FakeAssessmentRow(status=MarketAssessmentStatus.PROCESSING)
        session.get.return_value = row
        session_factory = _session_factory(session)

        result = await claim_assessment(
            session_factory,
            product_id=uuid4(),
            store_id=None,
            reference_price=Decimal("10"),
            reference_currency="BRL",
            now=NOW,
            settings=_settings(),
        )

        assert result.status is ClaimStatus.IN_PROGRESS
        assert result.snapshot is not None

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# mark_assessment_ready / mark_assessment_failed
# ---------------------------------------------------------------------------


def test_mark_assessment_ready_updates_row_with_all_fields():
    async def _scenario():
        session = _mock_async_session()
        session_factory = _session_factory(session)
        expires_at = NOW + timedelta(hours=24)

        await mark_assessment_ready(
            session_factory,
            product_id=uuid4(),
            classification=MarketPriceClassification.GOOD_DEAL,
            market_low=Decimal("10"),
            market_high=Decimal("20"),
            historical_low_external=Decimal("5"),
            historical_low_source="https://x.com",
            historical_low_observed_at=date(2026, 1, 1),
            confidence=AssessmentConfidence.HIGH,
            evidence={"a": 1},
            expires_at=expires_at,
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["status"] == MarketAssessmentStatus.READY
        assert params["lease_until"] is None
        assert params["retry_after"] is None
        assert params["last_error"] is None
        assert params["failure_count"] == 0
        assert params["classification"] == MarketPriceClassification.GOOD_DEAL
        assert params["market_low"] == Decimal("10")
        assert params["market_high"] == Decimal("20")
        assert params["historical_low_external"] == Decimal("5")
        assert params["historical_low_source"] == "https://x.com"
        assert params["historical_low_observed_at"] == date(2026, 1, 1)
        assert params["confidence"] == AssessmentConfidence.HIGH
        assert params["evidence"] == {"a": 1}
        assert params["expires_at"] == expires_at

    asyncio.run(_scenario())


def test_mark_assessment_failed_increments_existing_failure_count():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = MagicMock(failure_count=2)
        session_factory = _session_factory(session)
        settings = _settings(
            market_assessment_failure_backoff_minutes=10.0,
            market_assessment_failure_backoff_max_minutes=1000.0,
        )

        await mark_assessment_failed(
            session_factory,
            product_id=uuid4(),
            error="boom",
            now=NOW,
            settings=settings,
        )

        assert session.get.call_args.kwargs["with_for_update"] is True
        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["status"] == MarketAssessmentStatus.FAILED
        assert params["failure_count"] == 3
        assert params["retry_after"] == NOW + timedelta(minutes=10.0 * (2**2))
        assert params["lease_until"] is None
        assert "boom" in params["last_error"]

    asyncio.run(_scenario())


def test_mark_assessment_failed_defaults_to_one_when_row_missing():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = None
        session_factory = _session_factory(session)
        settings = _settings(
            market_assessment_failure_backoff_minutes=10.0,
            market_assessment_failure_backoff_max_minutes=1000.0,
        )

        await mark_assessment_failed(
            session_factory,
            product_id=uuid4(),
            error="boom",
            now=NOW,
            settings=settings,
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["failure_count"] == 1
        assert params["retry_after"] == NOW + timedelta(minutes=10.0)

    asyncio.run(_scenario())


def test_mark_assessment_failed_caps_backoff_at_max():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = MagicMock(failure_count=20)
        session_factory = _session_factory(session)
        settings = _settings(
            market_assessment_failure_backoff_minutes=10.0,
            market_assessment_failure_backoff_max_minutes=60.0,
        )

        await mark_assessment_failed(
            session_factory,
            product_id=uuid4(),
            error="boom",
            now=NOW,
            settings=settings,
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["retry_after"] == NOW + timedelta(minutes=60.0)

    asyncio.run(_scenario())


def test_mark_assessment_failed_redacts_sensitive_query_values():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = None
        session_factory = _session_factory(session)

        await mark_assessment_failed(
            session_factory,
            product_id=uuid4(),
            error="failed fetching https://x.com/?api_key=topsecret",
            now=NOW,
            settings=_settings(),
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert "topsecret" not in params["last_error"]
        assert "api_key=[REDACTED]" in params["last_error"]

    asyncio.run(_scenario())


def test_mark_assessment_failed_truncates_long_error_message():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = MagicMock(failure_count=0)
        session_factory = _session_factory(session)

        await mark_assessment_failed(
            session_factory,
            product_id=uuid4(),
            error="x" * 3000,
            now=NOW,
            settings=_settings(),
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert len(params["last_error"]) <= 2000

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# _search_with_enrichment
# ---------------------------------------------------------------------------


def test_search_with_enrichment_quorum_reached_without_calling_enrichment(monkeypatch):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [_search_result("https://a.com/1"), _search_result("https://b.com/1")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock()

        result = await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert len(result) == 2
        enrichment.scrape_basic.assert_not_awaited()

    asyncio.run(_scenario())


def test_search_with_enrichment_returns_compatible_only_when_enrichment_none():
    async def _scenario():
        product = _product()
        response = _search_response(
            [_search_result("https://a.com/1", title="INCOMPATIBLE")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)

        result = await _search_with_enrichment(
            search_manager, enrichment=None, query="q", product=product, min_items=2
        )

        assert result == ()

    asyncio.run(_scenario())


def test_search_with_enrichment_skips_unsafe_url(monkeypatch):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [_search_result("https://a.com/x?api_key=secret", title="INCOMPATIBLE")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock()

        result = await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert result == ()
        enrichment.scrape_basic.assert_not_awaited()

    asyncio.run(_scenario())


def test_search_with_enrichment_skips_url_on_fetch_error(monkeypatch):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [_search_result("https://a.com/x", title="INCOMPATIBLE")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock(
            side_effect=CesarCoreFetchError("core_fetch_failed")
        )

        result = await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert result == ()
        enrichment.scrape_basic.assert_awaited_once()

    asyncio.run(_scenario())


def test_search_with_enrichment_skips_when_page_is_none(monkeypatch):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [_search_result("https://a.com/x", title="INCOMPATIBLE")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock(return_value=None)

        result = await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert result == ()

    asyncio.run(_scenario())


def test_search_with_enrichment_adds_matching_enriched_page(monkeypatch):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [_search_result("https://a.com/x", title="INCOMPATIBLE")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        page = CesarCoreFetchResult(
            url="https://a.com/x", title="ASUS TUF B650", markdown="ficha técnica"
        )
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock(return_value=page)

        result = await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert len(result) == 1
        assert result[0].url == "https://a.com/x"
        assert result[0].title == "ASUS TUF B650"
        assert result[0].description == "ficha técnica"

    asyncio.run(_scenario())


def test_search_with_enrichment_discards_enriched_page_that_does_not_match(monkeypatch):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [_search_result("https://a.com/x", title="INCOMPATIBLE")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        page = CesarCoreFetchResult(
            url="https://a.com/x", title="INCOMPATIBLE product", markdown=None
        )
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock(return_value=page)

        result = await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert result == ()

    asyncio.run(_scenario())


def test_search_with_enrichment_falls_back_to_safe_url_when_page_has_no_title(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [_search_result("https://a.com/x", title="INCOMPATIBLE")]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        page = CesarCoreFetchResult(url="https://a.com/x", title=None, markdown="ficha")
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock(return_value=page)

        result = await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert len(result) == 1
        assert result[0].title == "https://a.com/x"

    asyncio.run(_scenario())


def test_search_with_enrichment_respects_scrape_budget_of_three_urls(monkeypatch):
    async def _scenario():
        product = _product()
        _identity_matcher(monkeypatch, product)
        response = _search_response(
            [
                _search_result("https://a.com/x", title="INCOMPATIBLE"),
                _search_result("https://b.com/x", title="INCOMPATIBLE"),
                _search_result("https://c.com/x", title="INCOMPATIBLE"),
                _search_result("https://d.com/x", title="INCOMPATIBLE"),
            ]
        )
        search_manager = MagicMock()
        search_manager.search = AsyncMock(return_value=response)
        enrichment = MagicMock()
        enrichment.scrape_basic = AsyncMock(return_value=None)

        await _search_with_enrichment(
            search_manager,
            enrichment=enrichment,
            query="q",
            product=product,
            min_items=2,
        )

        assert enrichment.scrape_basic.await_count == 3

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# _interpret_evidence
# ---------------------------------------------------------------------------


def test_interpret_evidence_returns_parsed_payload_and_builds_request():
    async def _scenario():
        product = _product()
        ai_manager = MagicMock()
        response = MagicMock(content=json.dumps({"classification": "good_deal"}))
        ai_manager.generate = AsyncMock(return_value=response)

        payload = await _interpret_evidence(
            ai_manager,
            profile=UserRole.USER,
            product=product,
            reference_price=Decimal("10"),
            reference_currency="BRL",
            market_evidence=(),
            history_evidence=(),
            requested_at=NOW,
        )

        assert payload == {"classification": "good_deal"}
        request = ai_manager.generate.call_args.args[0]
        assert request.profile is UserRole.USER
        assert request.purpose == "market_price_assessment"
        assert request.messages[0].role is AIMessageRole.SYSTEM
        assert request.messages[1].role is AIMessageRole.USER
        content = json.loads(request.messages[1].content)
        assert content["currency"] == "BRL"
        assert content["reference_price"] == "10"

    asyncio.run(_scenario())


def test_interpret_evidence_returns_none_when_payload_is_not_a_dict():
    async def _scenario():
        product = _product()
        ai_manager = MagicMock()
        response = MagicMock(content=json.dumps(["not", "a", "dict"]))
        ai_manager.generate = AsyncMock(return_value=response)

        payload = await _interpret_evidence(
            ai_manager,
            profile=UserRole.USER,
            product=product,
            reference_price=Decimal("10"),
            reference_currency="BRL",
            market_evidence=(),
            history_evidence=(),
            requested_at=NOW,
        )

        assert payload is None

    asyncio.run(_scenario())


def test_interpret_evidence_returns_none_when_generate_raises():
    async def _scenario():
        product = _product()
        ai_manager = MagicMock()
        ai_manager.generate = AsyncMock(side_effect=RuntimeError("boom"))

        payload = await _interpret_evidence(
            ai_manager,
            profile=UserRole.USER,
            product=product,
            reference_price=Decimal("10"),
            reference_currency="BRL",
            market_evidence=(),
            history_evidence=(),
            requested_at=NOW,
        )

        assert payload is None

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# run_market_research
# ---------------------------------------------------------------------------


def test_run_market_research_cache_hit_returns_snapshot_without_research(monkeypatch):
    async def _scenario():
        snapshot = _snapshot()
        monkeypatch.setattr(
            "app.market_research.service.claim_assessment",
            AsyncMock(return_value=ClaimResult(ClaimStatus.CACHE_HIT, snapshot)),
        )
        search_mock = AsyncMock()
        monkeypatch.setattr(
            "app.market_research.service._search_with_enrichment", search_mock
        )

        result = await run_market_research(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            product=_product(),
            store_id=uuid4(),
            reference_price=Decimal("1"),
            reference_currency="BRL",
            profile=UserRole.USER,
            now=NOW,
            settings=_settings(),
        )

        assert result is snapshot
        search_mock.assert_not_awaited()

    asyncio.run(_scenario())


def test_run_market_research_in_progress_returns_none_without_research(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.market_research.service.claim_assessment",
            AsyncMock(return_value=ClaimResult(ClaimStatus.IN_PROGRESS, None)),
        )
        search_mock = AsyncMock()
        monkeypatch.setattr(
            "app.market_research.service._search_with_enrichment", search_mock
        )

        result = await run_market_research(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            product=_product(),
            store_id=uuid4(),
            reference_price=Decimal("1"),
            reference_currency="BRL",
            profile=UserRole.USER,
            now=NOW,
            settings=_settings(),
        )

        assert result is None
        search_mock.assert_not_awaited()

    asyncio.run(_scenario())


def test_run_market_research_success_persists_and_returns_snapshot(monkeypatch):
    async def _scenario():
        product = _product()
        # `market_research_external_reference_enabled` deixou de nascer
        # `False` (2026-09-21, decisão explícita do usuário) -- este
        # teste prova especificamente o caminho SEM referência externa
        # (2 chamadas de busca: mercado + histórico ao vivo), então
        # precisa desligar a flag explicitamente agora.
        settings = _settings(market_research_external_reference_enabled=False)
        monkeypatch.setattr(
            "app.market_research.service.claim_assessment",
            AsyncMock(return_value=ClaimResult(ClaimStatus.WON, None)),
        )
        monkeypatch.setattr(
            "app.market_research.service.build_web_search_manager",
            MagicMock(return_value=MagicMock()),
        )
        market_evidence = (
            EvidenceItem(
                url="https://a.com/1", domain="a.com", title="t", description=None
            ),
        )
        history_evidence = (
            EvidenceItem(
                url="https://hist.com/1", domain="hist.com", title="h", description=None
            ),
        )
        search_mock = AsyncMock(side_effect=[market_evidence, history_evidence])
        monkeypatch.setattr(
            "app.market_research.service._search_with_enrichment", search_mock
        )
        monkeypatch.setattr(
            "app.market_research.service._interpret_evidence",
            AsyncMock(return_value={"classification": "good_deal"}),
        )
        outcome = MarketResearchOutcome(
            classification=MarketPriceClassification.GOOD_DEAL,
            market_low=Decimal("10"),
            market_high=Decimal("20"),
            confidence=AssessmentConfidence.MEDIUM,
            historical_low_external=None,
            historical_low_source=None,
            historical_low_observed_at=None,
            evidence={"k": "v"},
        )
        monkeypatch.setattr(
            "app.market_research.service.finalize_market_research",
            MagicMock(return_value=outcome),
        )
        expires_at = NOW + timedelta(hours=24)
        monkeypatch.setattr(
            "app.market_research.service._resolve_expiry_with_own_session",
            AsyncMock(return_value=expires_at),
        )
        mark_ready_mock = AsyncMock()
        monkeypatch.setattr(
            "app.market_research.service.mark_assessment_ready", mark_ready_mock
        )

        result = await run_market_research(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            product=product,
            store_id=uuid4(),
            reference_price=Decimal("15"),
            reference_currency="BRL",
            profile=UserRole.USER,
            now=NOW,
            settings=settings,
        )

        assert result is not None
        assert result.classification is MarketPriceClassification.GOOD_DEAL
        assert result.market_low == Decimal("10")
        assert result.expires_at == expires_at
        assert result.reference_price == Decimal("15")
        assert search_mock.await_count == 2
        mark_ready_mock.assert_awaited_once()
        assert mark_ready_mock.call_args.kwargs["evidence"] == {"k": "v"}

    asyncio.run(_scenario())


def test_run_market_research_reuses_external_reference_and_skips_history_search(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        settings = _settings(market_research_external_reference_enabled=True)
        monkeypatch.setattr(
            "app.market_research.service.claim_assessment",
            AsyncMock(return_value=ClaimResult(ClaimStatus.WON, None)),
        )
        monkeypatch.setattr(
            "app.market_research.service.build_web_search_manager",
            MagicMock(return_value=MagicMock()),
        )
        reference = MagicMock(
            spec=ExternalPriceReference,
            id=uuid4(),
            amount=Decimal("120"),
            safe_url="https://ext.com/x",
            historical_date=date(2026, 1, 1),
            quality="verified",
        )
        monkeypatch.setattr(
            "app.market_research.service.get_external_price_reference_evidence",
            AsyncMock(return_value=reference),
        )
        market_evidence = (
            EvidenceItem(
                url="https://a.com/1", domain="a.com", title="t", description=None
            ),
        )
        search_mock = AsyncMock(return_value=market_evidence)
        monkeypatch.setattr(
            "app.market_research.service._search_with_enrichment", search_mock
        )
        monkeypatch.setattr(
            "app.market_research.service._interpret_evidence",
            AsyncMock(return_value=None),
        )
        outcome = MarketResearchOutcome(
            classification=MarketPriceClassification.INSUFFICIENT_EVIDENCE,
            market_low=None,
            market_high=None,
            confidence=None,
            historical_low_external=None,
            historical_low_source=None,
            historical_low_observed_at=None,
            evidence={"ai_payload": None},
        )
        monkeypatch.setattr(
            "app.market_research.service.finalize_market_research",
            MagicMock(return_value=outcome),
        )
        monkeypatch.setattr(
            "app.market_research.service._resolve_expiry_with_own_session",
            AsyncMock(return_value=NOW),
        )
        mark_ready_mock = AsyncMock()
        monkeypatch.setattr(
            "app.market_research.service.mark_assessment_ready", mark_ready_mock
        )

        result = await run_market_research(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            product=product,
            store_id=uuid4(),
            reference_price=Decimal("100"),
            reference_currency="BRL",
            profile=UserRole.USER,
            now=NOW,
            settings=settings,
        )

        assert search_mock.await_count == 1
        assert result.historical_low_external == Decimal("120")
        assert result.historical_low_source == "https://ext.com/x"
        assert result.historical_low_observed_at == date(2026, 1, 1)
        persisted_evidence = mark_ready_mock.call_args.kwargs["evidence"]
        assert persisted_evidence["historical_low_reference"] == {
            "method": "f1_bootstrap_reuse",
            "reference_id": str(reference.id),
            "quality": "verified",
        }

    asyncio.run(_scenario())


def test_run_market_research_marks_failed_on_exception(monkeypatch):
    async def _scenario():
        product = _product()
        monkeypatch.setattr(
            "app.market_research.service.claim_assessment",
            AsyncMock(return_value=ClaimResult(ClaimStatus.WON, None)),
        )
        monkeypatch.setattr(
            "app.market_research.service.build_web_search_manager",
            MagicMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "app.market_research.service._search_with_enrichment",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        mark_failed_mock = AsyncMock()
        monkeypatch.setattr(
            "app.market_research.service.mark_assessment_failed", mark_failed_mock
        )

        result = await run_market_research(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            product=product,
            store_id=uuid4(),
            reference_price=Decimal("1"),
            reference_currency="BRL",
            profile=UserRole.USER,
            now=NOW,
            settings=_settings(),
        )

        assert result is None
        mark_failed_mock.assert_awaited_once()
        kwargs = mark_failed_mock.call_args.kwargs
        assert kwargs["product_id"] == product.id
        assert "boom" in kwargs["error"]

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# evaluate_trigger_and_maybe_research
# ---------------------------------------------------------------------------


def test_evaluate_trigger_and_maybe_research_returns_none_when_product_missing(
    monkeypatch,
):
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = None
        session_factory = _session_factory(session)
        internal_best_mock = AsyncMock()
        monkeypatch.setattr(
            "app.market_research.service.get_internal_historical_best",
            internal_best_mock,
        )

        result = await evaluate_trigger_and_maybe_research(
            session_factory,
            MagicMock(),
            MagicMock(),
            mission_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            current_amount=Decimal("10"),
            current_currency="BRL",
            previous_amount=None,
            target_amount=None,
            profile=UserRole.USER,
            now=NOW,
            settings=_settings(),
        )

        assert result is None
        internal_best_mock.assert_not_awaited()

    asyncio.run(_scenario())


def test_evaluate_trigger_and_maybe_research_returns_none_when_identity_key_missing(
    monkeypatch,
):
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = _product(identity_key=None)
        session_factory = _session_factory(session)
        internal_best_mock = AsyncMock()
        monkeypatch.setattr(
            "app.market_research.service.get_internal_historical_best",
            internal_best_mock,
        )

        result = await evaluate_trigger_and_maybe_research(
            session_factory,
            MagicMock(),
            MagicMock(),
            mission_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            current_amount=Decimal("10"),
            current_currency="BRL",
            previous_amount=None,
            target_amount=None,
            profile=UserRole.USER,
            now=NOW,
            settings=_settings(),
        )

        assert result is None
        internal_best_mock.assert_not_awaited()

    asyncio.run(_scenario())


def test_evaluate_trigger_and_maybe_research_returns_cached_snapshot_when_no_trigger(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        checkpoint = MagicMock(
            spec=MissionProductAlertState,
            best_notified_amount=Decimal("50"),
            rearmed_at=None,
            last_notified_at=None,
        )
        session = _mock_async_session()
        session.get = AsyncMock(side_effect=[product, checkpoint])
        session_factory = _session_factory(session)

        monkeypatch.setattr(
            "app.market_research.service.get_internal_historical_best",
            AsyncMock(return_value=None),
        )
        snapshot = _snapshot()
        monkeypatch.setattr(
            "app.market_research.service.get_current_assessment_snapshot",
            AsyncMock(return_value=snapshot),
        )
        monkeypatch.setattr(
            "app.market_research.service.resolve_realert_window",
            AsyncMock(return_value=timedelta(hours=1)),
        )
        monkeypatch.setattr(
            "app.market_research.service.should_trigger_market_research",
            MagicMock(return_value=False),
        )
        run_mock = AsyncMock()
        monkeypatch.setattr("app.market_research.service.run_market_research", run_mock)

        result = await evaluate_trigger_and_maybe_research(
            session_factory,
            MagicMock(),
            MagicMock(),
            mission_id=uuid4(),
            product_id=product.id,
            store_id=uuid4(),
            current_amount=snapshot.reference_price,
            current_currency="BRL",
            previous_amount=None,
            target_amount=None,
            profile=UserRole.USER,
            now=NOW,
            settings=_settings(),
        )

        assert result is snapshot
        run_mock.assert_not_awaited()

    asyncio.run(_scenario())


def test_evaluate_trigger_and_maybe_research_returns_none_when_no_trigger_and_no_cache(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        session = _mock_async_session()
        session.get = AsyncMock(side_effect=[product, None])
        session_factory = _session_factory(session)

        monkeypatch.setattr(
            "app.market_research.service.get_internal_historical_best",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.market_research.service.get_current_assessment_snapshot",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.market_research.service.resolve_realert_window",
            AsyncMock(return_value=timedelta(hours=1)),
        )
        monkeypatch.setattr(
            "app.market_research.service.should_trigger_market_research",
            MagicMock(return_value=False),
        )
        run_mock = AsyncMock()
        monkeypatch.setattr("app.market_research.service.run_market_research", run_mock)

        result = await evaluate_trigger_and_maybe_research(
            session_factory,
            MagicMock(),
            MagicMock(),
            mission_id=uuid4(),
            product_id=product.id,
            store_id=uuid4(),
            current_amount=Decimal("10"),
            current_currency="BRL",
            previous_amount=None,
            target_amount=None,
            profile=UserRole.USER,
            now=NOW,
            settings=_settings(),
        )

        assert result is None
        run_mock.assert_not_awaited()

    asyncio.run(_scenario())


def test_evaluate_trigger_and_maybe_research_delegates_to_run_market_research(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        session = _mock_async_session()
        session.get = AsyncMock(side_effect=[product, None])
        session_factory = _session_factory(session)

        monkeypatch.setattr(
            "app.market_research.service.get_internal_historical_best",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.market_research.service.get_current_assessment_snapshot",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.market_research.service.resolve_realert_window",
            AsyncMock(return_value=timedelta(hours=1)),
        )
        monkeypatch.setattr(
            "app.market_research.service.should_trigger_market_research",
            MagicMock(return_value=True),
        )
        sentinel_snapshot = _snapshot()
        run_mock = AsyncMock(return_value=sentinel_snapshot)
        monkeypatch.setattr("app.market_research.service.run_market_research", run_mock)

        ai_manager = MagicMock()
        firecrawl = MagicMock()
        result = await evaluate_trigger_and_maybe_research(
            session_factory,
            ai_manager,
            firecrawl,
            mission_id=uuid4(),
            product_id=product.id,
            store_id=uuid4(),
            current_amount=Decimal("77"),
            current_currency="BRL",
            previous_amount=None,
            target_amount=None,
            profile=UserRole.ADMIN,
            now=NOW,
            settings=_settings(),
        )

        assert result is sentinel_snapshot
        run_mock.assert_awaited_once()
        kwargs = run_mock.call_args.kwargs
        assert kwargs["product"] is product
        assert kwargs["reference_price"] == Decimal("77")
        assert kwargs["reference_currency"] == "BRL"
        assert kwargs["profile"] is UserRole.ADMIN

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# _resolve_expiry_with_own_session
# ---------------------------------------------------------------------------


def test_resolve_expiry_with_own_session_opens_session_and_delegates(monkeypatch):
    async def _scenario():
        session = _mock_async_session()
        session_factory = _session_factory(session)
        resolve_mock = AsyncMock(return_value=NOW)
        monkeypatch.setattr(
            "app.market_research.service.resolve_assessment_expiry", resolve_mock
        )

        result = await _resolve_expiry_with_own_session(
            session_factory, product_id=uuid4(), now=NOW, settings=_settings()
        )

        assert result == NOW
        resolve_mock.assert_awaited_once()

    asyncio.run(_scenario())
