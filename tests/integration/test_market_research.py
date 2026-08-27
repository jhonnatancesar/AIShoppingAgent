"""TASK-113: `MarketPriceAssessment` -- single-flight, TTL, refresh, falha.

Prova contra PostgreSQL real os invariantes que unit test não alcança:
claim atômico sob concorrência real (asyncpg, conexões distintas),
estados PROCESSING/READY/FAILED com lease/retry_after, e o fallback
`/v2/scrape` só quando a busca sozinha não basta. Sem rede real --
Firecrawl/IA fake, mesmo padrão do resto da suíte de integração
(`tests/integration/test_shared_collection.py`).
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from app.ai_provider.contracts import AIRequest, AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.core.config import Settings
from app.market_research.models import (
    MarketAssessmentStatus,
    MarketPriceAssessment,
)
from app.market_research.service import (
    ClaimStatus,
    claim_assessment,
    run_market_research,
)
from app.missions.models import MissionMonitoringItem
from app.missions.service import create_mission_from_criteria_async
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.search.firecrawl import (
    FirecrawlScrapeResult,
    FirecrawlSearchError,
    FirecrawlSearchResult,
)
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
_SETTINGS = Settings(_env_file=None)


def _gpu_product(sessions, *, title: str = "NVIDIA GeForce RTX 5070 Ti") -> Product:
    """Product com `identity_key` real, resolvido pelo MESMO motor
    (`resolve_product_variant`) usado em produção -- nunca um valor
    inventado que a validação de evidência (§33.31) rejeitaria."""
    variant = resolve_product_variant(title)
    assert variant is not None
    with sessions.begin() as session:
        product = Product(
            name=title,
            brand=variant.brand,
            model=variant.model,
            category=variant.category,
            family=variant.family,
            variant=variant.variant,
            attributes=dict(variant.attributes),
            family_key=variant.family_key,
            identity_key=variant.identity_key,
            identity_version=IDENTITY_VERSION,
        )
        session.add(product)
        session.flush()
        session.expunge(product)
        return product


class _FakeFirecrawl:
    """Conta chamadas de `search`/`scrape_basic` -- nunca toca rede real."""

    def __init__(
        self,
        *,
        search_results: tuple[FirecrawlSearchResult, ...] = (),
        scrape_result: FirecrawlScrapeResult | None = None,
        raise_on_search: bool = False,
    ) -> None:
        self.search_calls: list[str] = []
        self.scrape_calls: list[str] = []
        self._search_results = search_results
        self._scrape_result = scrape_result
        self._raise_on_search = raise_on_search

    async def search(self, query, *, sources=("web",), limit=2):
        self.search_calls.append(query)
        if self._raise_on_search:
            raise FirecrawlSearchError("firecrawl_unavailable")
        from app.search.firecrawl import FirecrawlSearchResponse

        return FirecrawlSearchResponse(
            success=True, results=self._search_results, status_code=200
        )

    async def scrape_basic(self, url):
        self.scrape_calls.append(url)
        return self._scrape_result


def _evidence(domain: str, title: str = "NVIDIA GeForce RTX 5070 Ti à venda") -> FirecrawlSearchResult:
    return FirecrawlSearchResult(
        title=title, description="Confira o preço", url=f"https://{domain}/produto"
    )


class _StubAIManager:
    """Sempre responde `good_deal` com 2 fontes -- quórum mínimo padrão."""

    def __init__(self, *, content: str | None = None) -> None:
        self.calls: list[str] = []
        self._content = content or json.dumps(
            {
                "classification": "good_deal",
                "market_low": "4200.00",
                "market_high": "4800.00",
                "confidence": "medium",
                "historical_low_external": None,
                "historical_low_source": None,
                "historical_low_observed_at": None,
            }
        )

    async def generate(self, request: AIRequest) -> AIResponse:
        self.calls.append(request.purpose)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=self._content,
            finished_at=datetime.now(UTC),
        )


def _session_factory(integration_database):
    return integration_database.async_sessions


# ---------------------------------------------------------------------------
# A: single-flight sob concorrência real
# ---------------------------------------------------------------------------


def test_single_flight_only_one_worker_wins_claim(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    sessions = _session_factory(integration_database)

    async def _attempt():
        return await claim_assessment(
            sessions,
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )

    async def _run_all():
        return await asyncio.gather(*(_attempt() for _ in range(5)))

    results = asyncio.run(_run_all())
    won = [r for r in results if r.status is ClaimStatus.WON]
    others = [r for r in results if r.status is not ClaimStatus.WON]
    assert len(won) == 1
    assert len(others) == 4
    assert all(r.status is ClaimStatus.IN_PROGRESS for r in others)


# ---------------------------------------------------------------------------
# B: lease expirado
# ---------------------------------------------------------------------------


def test_processing_with_valid_lease_blocks_reclaim(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        session.add(
            MarketPriceAssessment(
                product_id=product.id,
                status=MarketAssessmentStatus.PROCESSING,
                reference_price=Decimal("4199.99"),
                reference_currency="BRL",
                lease_until=NOW + timedelta(minutes=5),
            )
        )

    result = asyncio.run(
        claim_assessment(
            _session_factory(integration_database),
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert result.status is ClaimStatus.IN_PROGRESS


def test_processing_with_expired_lease_allows_reclaim(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        session.add(
            MarketPriceAssessment(
                product_id=product.id,
                status=MarketAssessmentStatus.PROCESSING,
                reference_price=Decimal("4199.99"),
                reference_currency="BRL",
                lease_until=NOW - timedelta(minutes=1),
            )
        )

    result = asyncio.run(
        claim_assessment(
            _session_factory(integration_database),
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert result.status is ClaimStatus.WON


# ---------------------------------------------------------------------------
# C: READY expirado
# ---------------------------------------------------------------------------


def test_ready_past_expiry_allows_reclaim(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        session.add(
            MarketPriceAssessment(
                product_id=product.id,
                status=MarketAssessmentStatus.READY,
                reference_price=Decimal("4199.99"),
                reference_currency="BRL",
                lease_until=None,
                expires_at=NOW - timedelta(hours=1),
            )
        )

    result = asyncio.run(
        claim_assessment(
            _session_factory(integration_database),
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert result.status is ClaimStatus.WON


# ---------------------------------------------------------------------------
# D: cache hit (READY válido, variação pequena)
# ---------------------------------------------------------------------------


def test_ready_within_ttl_and_small_variation_is_cache_hit(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        session.add(
            MarketPriceAssessment(
                product_id=product.id,
                status=MarketAssessmentStatus.READY,
                reference_price=Decimal("4199.99"),
                reference_currency="BRL",
                lease_until=None,
                expires_at=NOW + timedelta(hours=12),
            )
        )

    result = asyncio.run(
        claim_assessment(
            _session_factory(integration_database),
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("4189.99"),  # variação < 5%
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert result.status is ClaimStatus.CACHE_HIT
    assert result.snapshot is not None
    assert result.snapshot.status is MarketAssessmentStatus.READY


# ---------------------------------------------------------------------------
# E: refresh antecipado (variação >= 5% dentro do TTL)
# ---------------------------------------------------------------------------


def test_ready_within_ttl_but_large_variation_allows_refresh(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        session.add(
            MarketPriceAssessment(
                product_id=product.id,
                status=MarketAssessmentStatus.READY,
                reference_price=Decimal("4199.99"),
                reference_currency="BRL",
                lease_until=None,
                expires_at=NOW + timedelta(hours=12),
            )
        )

    result = asyncio.run(
        claim_assessment(
            _session_factory(integration_database),
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("3800.00"),  # variação > 5%
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert result.status is ClaimStatus.WON


# ---------------------------------------------------------------------------
# F: FAILED + retry_after
# ---------------------------------------------------------------------------


def test_failed_before_retry_after_blocks_reclaim(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        session.add(
            MarketPriceAssessment(
                product_id=product.id,
                status=MarketAssessmentStatus.FAILED,
                reference_price=Decimal("4199.99"),
                reference_currency="BRL",
                retry_after=NOW + timedelta(minutes=10),
                failure_count=1,
            )
        )

    result = asyncio.run(
        claim_assessment(
            _session_factory(integration_database),
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert result.status is ClaimStatus.IN_PROGRESS


def test_failed_after_retry_after_allows_reclaim(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        session.add(
            MarketPriceAssessment(
                product_id=product.id,
                status=MarketAssessmentStatus.FAILED,
                reference_price=Decimal("4199.99"),
                reference_currency="BRL",
                retry_after=NOW - timedelta(minutes=1),
                failure_count=1,
            )
        )

    result = asyncio.run(
        claim_assessment(
            _session_factory(integration_database),
            product_id=product.id,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert result.status is ClaimStatus.WON


# ---------------------------------------------------------------------------
# J: falha do Firecrawl -> FAILED/retry_after, nunca exceção propagada
# ---------------------------------------------------------------------------


def test_firecrawl_failure_marks_assessment_failed_never_raises(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    firecrawl = _FakeFirecrawl(raise_on_search=True)
    ai_manager = _StubAIManager()

    snapshot = asyncio.run(
        run_market_research(
            _session_factory(integration_database),
            ai_manager,
            firecrawl,
            product=product,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            profile=UserRole.ADMIN,
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert snapshot is None

    with integration_database.sessions() as session:
        row = session.get(MarketPriceAssessment, product.id)
        assert row is not None
        assert row.status is MarketAssessmentStatus.FAILED
        assert row.retry_after is not None
        assert row.retry_after > NOW
        assert row.failure_count == 1


# ---------------------------------------------------------------------------
# K: fallback /v2/scrape só quando a busca sozinha não atinge o quórum
# ---------------------------------------------------------------------------


def test_scrape_fallback_only_when_search_below_quorum(integration_database) -> None:
    product = _gpu_product(integration_database.sessions)
    # Busca já traz 2 domínios válidos -- scrape NUNCA deveria ser chamado.
    firecrawl_sufficient = _FakeFirecrawl(
        search_results=(_evidence("lojaa.com.br"), _evidence("lojab.com.br"))
    )
    asyncio.run(
        run_market_research(
            _session_factory(integration_database),
            _StubAIManager(),
            firecrawl_sufficient,
            product=product,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            profile=UserRole.ADMIN,
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert firecrawl_sufficient.scrape_calls == []

    # Busca traz só 1 domínio (abaixo do mínimo=2) -- scrape deve rodar.
    # Produto DIFERENTE (RTX 5080, não 5070 Ti) -- só para não colidir no
    # `identity_key` único do primeiro Product deste teste.
    product2 = _gpu_product(integration_database.sessions, title="NVIDIA GeForce RTX 5080")
    firecrawl_insufficient = _FakeFirecrawl(
        search_results=(_evidence("lojaunica.com.br", title="NVIDIA GeForce RTX 5080 à venda"),),
        scrape_result=FirecrawlScrapeResult(
            url="https://lojaunica.com.br/produto",
            title="NVIDIA GeForce RTX 5080",
            markdown="Preço bom",
        ),
    )
    asyncio.run(
        run_market_research(
            _session_factory(integration_database),
            _StubAIManager(),
            firecrawl_insufficient,
            product=product2,
            store_id=None,
            reference_price=Decimal("4199.99"),
            reference_currency="BRL",
            profile=UserRole.ADMIN,
            now=NOW,
            settings=_SETTINGS,
        )
    )
    assert len(firecrawl_insufficient.scrape_calls) >= 1


# ---------------------------------------------------------------------------
# H: shared/fan-out -- 10 Missions do mesmo Product, 1 pesquisa, 10 decisões
# ---------------------------------------------------------------------------


class _StableGpuProvider:
    source_code = "amazon"

    def __init__(self, *, raw_price: str = "3999.90") -> None:
        self.calls = 0

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.calls += 1
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/shared-gpu-h",
                    title="NVIDIA GeForce RTX 5070 Ti",
                    collected_at=completed,
                    external_id="shared-gpu-h-stable",
                    raw_price="3999.90",
                    raw_currency="BRL",
                    raw_availability="Em estoque",
                ),
            ),
        )


class _MarketAwareAIManager:
    """Sempre MATCH na relevância; `market_price_assessment` conta chamadas
    à parte -- prova de que só 1 pesquisa acontece para as 10 Missions."""

    def __init__(self) -> None:
        self.relevance_calls = 0
        self.market_research_calls = 0

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.purpose == "market_price_assessment":
            self.market_research_calls += 1
            body = json.dumps(
                {
                    "classification": "good_deal",
                    "market_low": "4200.00",
                    "market_high": "4800.00",
                    "confidence": "medium",
                    "historical_low_external": None,
                    "historical_low_source": None,
                    "historical_low_observed_at": None,
                }
            )
        elif request.purpose == "classify_offer_relevance":
            self.relevance_calls += 1
            body = json.dumps({"relevance": "match"})
        else:
            body = json.dumps({"display_title": "Produto"})
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=body,
            finished_at=datetime.now(UTC),
        )


def _seed_user(sessions) -> UUID:
    with sessions.begin() as session:
        user = User(display_name="TASK-113 H", role=UserRole.USER)
        session.add(user)
        session.flush()
        return user.id


def _make_gpu_mission(integration_database, user_id: UUID, *, target_amount: Decimal):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            mission, _codes = await create_mission_from_criteria_async(
                session,
                user_id=user_id,
                search_query="RTX 5070 Ti",
                target_amount=target_amount,
                target_currency="BRL",
                source_codes=("amazon",),
                requested_at=NOW,
                actor_type="test",
            )
            return mission

    return asyncio.run(run())


def test_shared_fan_out_reuses_single_assessment_across_ten_missions(
    integration_database,
) -> None:
    from app.collection.shared_collection import collect_monitoring_item_store

    # 10 usuários distintos (cota de 5 missões ativas por usuário, TASK-107)
    # -- também mais realista: "10 usuários monitoram o mesmo produto"
    # (exemplo do próprio §33.19/§19 da TASK-113), não 1 usuário com 10.
    missions = [
        _make_gpu_mission(
            integration_database,
            _seed_user(integration_database.sessions),
            target_amount=Decimal("4500.00"),
        )
        for _ in range(10)
    ]
    with integration_database.sessions() as session:
        item_id = session.get(MissionMonitoringItem, missions[0].id).monitoring_item_id
        for mission in missions[1:]:
            assert (
                session.get(MissionMonitoringItem, mission.id).monitoring_item_id == item_id
            )
        amazon_id = session.scalar(select(Store.id).where(Store.code == "amazon"))

    provider = _StableGpuProvider()
    ai_manager = _MarketAwareAIManager()
    firecrawl = _FakeFirecrawl(
        search_results=(_evidence("lojaa.com.br"), _evidence("lojab.com.br"))
    )

    result = asyncio.run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            CollectionAdapter(providers=(provider,)),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            firecrawl=firecrawl,
            settings=_SETTINGS,
        )
    )
    assert result.succeeded is True
    assert len(result.fanned_out_mission_ids) == 10

    # 1 pesquisa completa (mercado atual + histórico, §33.18 -- sempre as
    # duas juntas, nunca uma por Mission) + 1 chamada de IA, independente
    # de serem 10 Missions perguntando pelo mesmo Product.
    assert len(firecrawl.search_calls) == 2
    assert ai_manager.market_research_calls == 1

    with integration_database.sessions() as session:
        assessments = session.scalars(select(MarketPriceAssessment)).all()
        assert len(assessments) == 1
        assert assessments[0].status is MarketAssessmentStatus.READY

        from app.alerts.models import MissionProductAlertState
        from app.events import Event, EventType

        checkpoints = session.scalars(
            select(MissionProductAlertState).where(
                MissionProductAlertState.mission_id.in_([m.id for m in missions])
            )
        ).all()
        events = session.scalars(
            select(Event).where(
                Event.mission_id.in_([m.id for m in missions]),
                Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
            )
        ).all()

    # 10 decisões individuais -- cada Mission recebe seu próprio checkpoint
    # e seu próprio evento (o assessment é compartilhado, o ALERTA não).
    assert len(checkpoints) == 10
    assert len(events) == 10
