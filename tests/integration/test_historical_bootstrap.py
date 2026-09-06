"""FASE F1: persistência e idempotência contra PostgreSQL real."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.ai_provider.contracts import AIResponse
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    OfferCondition,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.historical_bootstrap.models import (
    ExternalPriceReference,
    HistoricalBootstrap,
    HistoricalBootstrapStatus,
)
from app.historical_bootstrap.service import (
    internal_history_is_sufficient,
    run_historical_bootstrap,
)
from app.missions.models import Mission, MissionStatus
from app.offers.models import Offer
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.search.cesar_core_fetch import CesarCoreFetchError, CesarCoreFetchResult
from app.search.contracts import WebSearchResponse, WebSearchResult
from app.search.manager import WebSearchManager
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


class Search:
    def __init__(self, results=()):
        self.results, self.calls = results, 0

    async def search(self, query, *, limit, correlation_id):
        self.calls += 1
        return WebSearchResponse(
            tuple(self.results[:limit]), "cesar_core", "searxng-search", correlation_id
        )


class Fetch:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def scrape_basic(self, url):
        self.calls += 1
        return self.result


class NoAI:
    def __init__(self):
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        raise AssertionError("AI não deveria ser usada")


class AI:
    """Stub que devolve exatamente o JSON fechado que
    `_interpret_ambiguous` espera -- nunca a IA de verdade."""

    def __init__(self, *, identity: str, price_brl: str, date_iso: str):
        self._identity, self._price, self._date = identity, price_brl, date_iso
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        content = json.dumps(
            {
                "product_identity_text": self._identity,
                "price_brl": self._price,
                "date": self._date,
            }
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=NOW,
        )


def product(database):
    variant = resolve_product_variant("NVIDIA GeForce RTX 5070 Ti")
    assert variant is not None
    with database.sessions.begin() as session:
        item = Product(
            name="NVIDIA GeForce RTX 5070 Ti",
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
        session.add(item)
        session.flush()
        identity = item.id
    return identity


def test_valid_reference_is_separate_and_second_run_has_zero_upstream(
    integration_database,
):
    product_id = product(integration_database)
    result = WebSearchResult(
        "NVIDIA GeForce RTX 5070 Ti histórico",
        "https://www.hardwarebarato.com/produtos/placas-de-video/rtx-5070-ti",
        "Histórico",
        1,
    )
    search, fetch, ai = (
        Search((result,)),
        Fetch(
            CesarCoreFetchResult(
                result.url,
                result.title,
                "NVIDIA GeForce RTX 5070 Ti 01/08/2026 R$ 4.999,90",
            )
        ),
        NoAI(),
    )
    first = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert first is HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
    calls = (search.calls, fetch.calls, ai.calls)
    second = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert second is HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
    assert (search.calls, fetch.calls, ai.calls) == calls
    with integration_database.sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(ExternalPriceReference))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(HistoricalBootstrap)) == 1
        )
        ref = session.scalar(select(ExternalPriceReference))
        assert ref.source == "hardware_barato" and ref.amount == Decimal("4999.9000")
        assert "fragment" not in ref.safe_url and "access_token" not in ref.safe_url


def test_empty_bootstrap_is_terminal(integration_database):
    product_id = product(integration_database)
    search, fetch, ai = Search(), Fetch(None), NoAI()
    first = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert first is HistoricalBootstrapStatus.COMPLETED_WITHOUT_REFERENCES
    calls = search.calls
    second = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert second is HistoricalBootstrapStatus.COMPLETED_WITHOUT_REFERENCES
    assert search.calls == calls and fetch.calls == 0 and ai.calls == 0


def _store(sessions, *, code: str) -> Store:
    with sessions() as session:
        return session.scalar(select(Store).where(Store.code == code))


def _seed_sufficient_internal_history(sessions, *, product_id) -> None:
    """Mesmo Product/BRL/new, 2 lojas, 30+ dias de cobertura -- critério
    exato de `internal_history_is_sufficient` (FASE F1, seção 6)."""
    with sessions.begin() as session:
        user = User(display_name="F1 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id, title="F1 mission", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        mission_id = mission.id
    pichau, terabyte = (
        _store(sessions, code="pichau"),
        _store(sessions, code="terabyte"),
    )
    for store, offset_days in ((pichau, 31), (terabyte, 0)):
        with sessions.begin() as session:
            offer = Offer(
                product_id=product_id,
                store_id=store.id,
                url=f"https://example.invalid/f1-{uuid4().hex[:12]}",
            )
            session.add(offer)
            session.flush()
            observed_at = NOW - timedelta(days=offset_days)
            run = CollectionRun(
                mission_id=mission_id,
                store_id=store.id,
                status=CollectionRunStatus.SUCCEEDED,
                started_at=observed_at,
                finished_at=observed_at,
            )
            session.add(run)
            session.flush()
            session.add(
                PriceObservation(
                    offer_id=offer.id,
                    collection_run_id=run.id,
                    amount=Decimal("4999.90"),
                    currency="BRL",
                    total_amount=Decimal("4999.90"),
                    condition=OfferCondition.NEW,
                    availability=Availability.AVAILABLE,
                    observed_at=observed_at,
                )
            )


def test_sufficient_internal_history_skips_bootstrap_entirely(integration_database):
    """(A) histórico próprio suficiente -- ZERO Search/Fetch/IA, nenhum
    HistoricalBootstrap é sequer criado."""
    product_id = product(integration_database)
    _seed_sufficient_internal_history(
        integration_database.sessions, product_id=product_id
    )

    async def _check_sufficient() -> bool:
        async with integration_database.async_sessions() as session:
            return await internal_history_is_sufficient(
                session, product_id=product_id, now=NOW
            )

    assert asyncio.run(_check_sufficient())
    search, fetch, ai = Search(), Fetch(None), NoAI()
    result = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert result is None
    assert search.calls == 0 and fetch.calls == 0 and ai.calls == 0
    with integration_database.sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(HistoricalBootstrap)) == 0
        )


def test_search_snippet_alone_is_sufficient_zero_fetch_zero_ai(integration_database):
    """(C) Search sozinho já basta (preço bem formado + identidade batem no
    título/snippet) -- persiste com ZERO Fetch e ZERO IA para essa URL."""
    product_id = product(integration_database)
    result = WebSearchResult(
        "NVIDIA GeForce RTX 5070 Ti histórico de preço",
        "https://www.example-comparador.com.br/rtx-5070-ti-historico",
        "NVIDIA GeForce RTX 5070 Ti 01/08/2026 R$ 4.999,90 no menor preço já registrado.",
        1,
    )
    search, fetch, ai = Search((result,)), Fetch(None), NoAI()
    status = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert status is HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
    assert fetch.calls == 0 and ai.calls == 0
    with integration_database.sessions() as session:
        ref = session.scalar(select(ExternalPriceReference))
        assert ref is not None and ref.amount == Decimal("4999.9000")


def test_different_manufacturer_matches_generic_mission_and_is_preserved(
    integration_database,
):
    """Missão genérica ("NVIDIA GeForce RTX 5070 Ti", sem fabricante
    pedido) aceita evidência de QUALQUER fabricante/AIB (Palit, Gigabyte,
    ASUS etc.) -- mesma regra já usada em produção por
    `MissionCriteria.requested_family_key`/`requested_variant`
    (`app/missions/query.py`), nunca um conceito novo de "modelo-base vs
    SKU". O fabricante da evidência é preservado em `match_evidence`
    (nunca descartado, nunca misturado sem metadata) e a IA NUNCA é
    chamada -- o Fetch já trouxe preço+data suficientes."""
    product_id = product(integration_database)
    result = WebSearchResult(
        "Palit RTX 5070 Ti GamingPro-S à venda",
        "https://www.example-loja.com.br/palit-rtx-5070-ti-gamingpro-s",
        "Confira o histórico",
        1,
    )
    search = Search((result,))
    fetch = Fetch(
        CesarCoreFetchResult(
            result.url,
            "Palit NVIDIA GeForce RTX 5070 Ti GamingPro-S 16GB",
            "Histórico de preço da Palit RTX 5070 Ti GamingPro-S: 15/07/2026 R$ 5.199,00.",
        )
    )
    ai = NoAI()
    status = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert status is HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
    assert fetch.calls == 1 and ai.calls == 0
    with integration_database.sessions() as session:
        ref = session.scalar(select(ExternalPriceReference))
        assert ref is not None and ref.amount == Decimal("5199.0000")
        assert ref.quality == "family_match"
        assert ref.match_evidence["method"] == "family_key_market"
        assert ref.match_evidence["evidence_attributes"]["board_brand"] == "palit"


def test_ambiguous_identity_after_fetch_uses_ai_as_last_layer(integration_database):
    """(F) Fetch devolve preço+data reais, mas o texto menciona dois
    modelos de GPU (combo) -- o parser determinístico pega o número
    ERRADO (o primeiro que aparece, "5060", não "5070 Ti") e por isso
    `family_key` diverge do Product; matching determinístico não resolve
    sozinho. Só então a IA entra, como última camada, interpreta o texto
    corretamente e só persiste o que a própria evidência sustenta."""
    product_id = product(integration_database)
    result = WebSearchResult(
        "Combo de placas à venda",
        "https://www.example-loja.com.br/combo-rtx-5060-5070-ti",
        "Confira o combo",
        1,
    )
    search = Search((result,))
    fetch = Fetch(
        CesarCoreFetchResult(
            result.url,
            "Combo RTX 5060 + RTX 5070 Ti Palit GamingPro-S",
            "Combo com duas placas: RTX 5060 e RTX 5070 Ti Palit GamingPro-S. "
            "Preço do combo: 15/07/2026 R$ 5.199,00.",
        )
    )
    ai = AI(
        identity="NVIDIA GeForce RTX 5070 Ti",
        price_brl="5199.00",
        date_iso="2026-07-15",
    )
    status = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert status is HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
    assert fetch.calls == 1 and ai.calls == 1
    with integration_database.sessions() as session:
        ref = session.scalar(select(ExternalPriceReference))
        assert ref is not None and ref.amount == Decimal("5199.0000")
        assert ref.quality == "verified"
        assert ref.match_evidence["method"] == "identity_key_exact"


def test_fetch_unavailable_never_invents_a_fact(integration_database):
    """(G) Search não basta e o Fetch falha (o mesmo formato de falha do
    `503 fetch_upstream_unavailable` real, `CesarCoreFetchError`) -- nunca
    inventa fato, nunca chama IA sem evidência bruta, bootstrap conclui sem
    referências em vez de propagar exceção."""
    product_id = product(integration_database)
    result = WebSearchResult(
        "RTX 5070 Ti: Preço Hoje - Hardware Barato",
        "https://www.hardwarebarato.com/produtos/placas-de-video/rtx-5070-ti",
        "Veja onde comprar RTX 5070 Ti com o menor preço.",
        1,
    )

    class FailingFetch:
        def __init__(self):
            self.calls = 0

        async def scrape_basic(self, url):
            self.calls += 1
            raise CesarCoreFetchError("core_fetch_request_failed", status_code=503)

    search, fetch, ai = Search((result,)), FailingFetch(), NoAI()
    status = asyncio.run(
        run_historical_bootstrap(
            integration_database.async_sessions,
            product_id=product_id,
            search=WebSearchManager(search),
            fetch=fetch,
            ai=ai,
            profile=UserRole.ADMIN,
            now=NOW,
        )
    )
    assert status is HistoricalBootstrapStatus.COMPLETED_WITHOUT_REFERENCES
    assert fetch.calls == 1 and ai.calls == 0
    with integration_database.sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(ExternalPriceReference))
            == 0
        )
