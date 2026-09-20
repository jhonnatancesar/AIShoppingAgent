"""TASK-098: histórico de preço por Product ancorado em Offer, contra
PostgreSQL real -- prova ownership via EXISTS correlato, agregação
diária por (Store, dia comercial), resolução de `current_amount` sem
ressuscitar preço obsoleto, resolução determinística de moeda e o caso
`comparable=false` (identidade não resolvida). Sem mocks de sessão --
o que `tests/test_webapp_offers_router.py` cobre com SQL compilado, este
arquivo prova executando contra o banco de verdade. Roda só via
`python scripts/run_integration_tests.py`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    OfferCondition,
    RawCollectedOffer,
)
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.collection.orchestration import CollectionOrchestrator
from app.collection.relevance import OfferRelevance
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.offers.models import Offer
from app.offers.query import get_offer_price_history_for_user
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import event, select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


def _user(sessions) -> User:
    with sessions.begin() as session:
        user = User(display_name="TASK-098 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _mission(sessions, *, user_id) -> Mission:
    with sessions.begin() as session:
        mission = Mission(
            user_id=user_id, title="TASK-098 mission", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.expunge(mission)
        return mission


def _store(sessions, *, code: str) -> Store:
    with sessions() as session:
        return session.scalar(select(Store).where(Store.code == code))


def _product(sessions, *, comparable: bool, title: str) -> Product:
    with sessions.begin() as session:
        if comparable:
            variant = resolve_product_variant(title)
            assert variant is not None
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
        else:
            product = Product(name=title)
        session.add(product)
        session.flush()
        session.expunge(product)
        return product


def _offer(sessions, *, product_id, store_id) -> Offer:
    with sessions.begin() as session:
        offer = Offer(
            product_id=product_id,
            store_id=store_id,
            url=f"https://example.invalid/task098-{uuid4().hex[:12]}",
        )
        session.add(offer)
        session.flush()
        session.expunge(offer)
        return offer


def _link(sessions, *, mission_id, offer_id) -> None:
    with sessions.begin() as session:
        session.add(
            MissionOfferRelevance(
                mission_id=mission_id,
                offer_id=offer_id,
                classification=OfferRelevance.MATCH,
                classified_at=NOW,
            )
        )


def _observe(
    sessions,
    *,
    offer_id,
    store_id,
    mission_id,
    amount: str,
    currency: str = "BRL",
    condition: OfferCondition = OfferCondition.NEW,
    availability: Availability = Availability.AVAILABLE,
    observed_at: datetime,
) -> PriceObservation:
    with sessions.begin() as session:
        run = CollectionRun(
            mission_id=mission_id,
            store_id=store_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=observed_at,
            finished_at=observed_at,
        )
        session.add(run)
        session.flush()
        decimal_amount = Decimal(amount)
        observation = PriceObservation(
            offer_id=offer_id,
            collection_run_id=run.id,
            amount=decimal_amount,
            currency=currency,
            total_amount=decimal_amount,
            condition=condition,
            availability=availability,
            observed_at=observed_at,
        )
        session.add(observation)
        # Espelha `orchestration.py`: TODA coleta bem-sucedida avança
        # `Offer.last_seen_at`, redundante ou não -- sem isto, o default
        # da coluna (`utc_now()` no INSERT do fixture `_offer`, ou seja,
        # o relógio REAL da execução do teste) vazaria pra dentro da
        # linha do tempo fictícia deste arquivo (`NOW`, 2026-08-27),
        # como se a oferta tivesse sido vista "hoje" mesmo quando o
        # cenário simula uma confirmação antiga.
        offer = session.get(Offer, offer_id)
        offer.last_seen_at = observed_at
        session.flush()
        session.expunge(observation)
        return observation


def _fetch(
    integration_database, *, offer_id, user_id, period="all", now=NOW, store_ids=None
):
    async def _run():
        async with integration_database.async_sessions() as session:
            return await get_offer_price_history_for_user(
                session,
                offer_id=offer_id,
                user_id=user_id,
                period=period,
                now=now,
                store_ids=store_ids,
            )

    return asyncio.run(_run())


def _setup_single_offer(integration_database, *, comparable: bool = True):
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store = _store(sessions, code="amazon")
    product = _product(
        sessions, comparable=comparable, title="NVIDIA GeForce RTX 5070 Ti"
    )
    offer = _offer(sessions, product_id=product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=offer.id)
    return sessions, user, mission, store, product, offer


# --- A: period=all é genuinamente irrestrito no tempo -----------------------


def test_period_all_returns_observation_older_than_any_finite_window(
    integration_database,
) -> None:
    sessions, user, mission, store, _product, offer = _setup_single_offer(
        integration_database
    )
    old_observed_at = NOW - timedelta(days=900)  # muito além de 1a
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3500.00",
        observed_at=old_observed_at,
    )

    all_result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )
    one_year_result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="1a"
    )

    assert all_result.comparable is True
    assert len(all_result.series) == 1
    assert all_result.series[0].points[0].amount == Decimal("3500.00")
    assert one_year_result.series == ()  # fora da janela de 1 ano


# --- B: mesmo dia comercial, duas observações -- ponto = mínimo, atual = mais recente válida --


def test_same_day_two_observations_daily_point_is_low_current_is_latest(
    integration_database,
) -> None:
    sessions, user, mission, store, _product, offer = _setup_single_offer(
        integration_database
    )
    morning = NOW.replace(hour=9)
    evening = NOW.replace(hour=21)
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        observed_at=morning,
    )
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="4300.00",
        observed_at=evening,
    )

    result = _fetch(
        integration_database,
        offer_id=offer.id,
        user_id=user.id,
        period="7d",
        now=evening,
    )

    assert len(result.series) == 1
    points = result.series[0].points
    assert len(points) == 1
    assert points[0].amount == Decimal("3900.00")  # menor do dia
    assert result.metrics.current_amount == Decimal("4300.00")  # mais recente válida


# --- C: última observação inválida nunca ressuscita preço antigo válido -----


def test_offer_unavailable_today_does_not_resurrect_yesterdays_valid_price(
    integration_database,
) -> None:
    sessions, user, mission, store, _product, offer = _setup_single_offer(
        integration_database
    )
    yesterday = NOW - timedelta(days=1)
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        availability=Availability.AVAILABLE,
        observed_at=yesterday,
    )
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        availability=Availability.UNAVAILABLE,
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )

    assert result.metrics.current_amount is None  # nenhuma Offer válida agora
    assert result.metrics.min_amount == Decimal("3900.00")  # histórico continua real


# --- E: moeda resolvida deterministicamente quando a âncora não tem observação --


def test_currency_resolves_from_another_accessible_offer_when_anchor_has_none(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store_amazon = _store(sessions, code="amazon")
    store_kabum = _store(sessions, code="kabum")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    anchor_offer = _offer(sessions, product_id=product.id, store_id=store_amazon.id)
    other_offer = _offer(sessions, product_id=product.id, store_id=store_kabum.id)
    _link(sessions, mission_id=mission.id, offer_id=anchor_offer.id)
    _link(sessions, mission_id=mission.id, offer_id=other_offer.id)
    _observe(
        sessions,
        offer_id=other_offer.id,
        store_id=store_kabum.id,
        mission_id=mission.id,
        amount="3999.00",
        currency="BRL",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=anchor_offer.id, user_id=user.id, period="all"
    )

    assert result.currency == "BRL"
    assert len(result.series) == 1
    assert result.series[0].store_code == "kabum"


# --- F: zero observações válidas em qualquer Offer acessível ----------------


def test_no_valid_observation_anywhere_returns_empty_without_fallback(
    integration_database,
) -> None:
    sessions, user, _mission, _store, _product, offer = _setup_single_offer(
        integration_database
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )

    assert result.comparable is True
    assert result.currency is None
    assert result.series == ()
    assert result.metrics is None


# --- Identidade não resolvida: comparable=false, nunca None/404 -------------


def test_unresolved_identity_returns_comparable_false_with_reason(
    integration_database,
) -> None:
    sessions, user, _mission, _store, _product, offer = _setup_single_offer(
        integration_database, comparable=False
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )

    assert result.comparable is False
    assert result.reason == "unresolved_product_identity"
    assert result.series == ()
    assert result.metrics is None


# --- Autorização real: oferta sem vínculo de missão do usuário -> None ------


def test_offer_without_user_relevance_link_is_not_accessible(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    owner = _user(sessions)
    stranger = _user(sessions)
    mission = _mission(sessions, user_id=owner.id)
    store = _store(sessions, code="amazon")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    offer = _offer(sessions, product_id=product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=offer.id)
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=stranger.id, period="all"
    )

    assert result is None


# --- Agregação entre lojas: uma linha de série por Store --------------------


def test_all_stores_view_has_one_series_per_store(integration_database) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store_amazon = _store(sessions, code="amazon")
    store_kabum = _store(sessions, code="kabum")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    offer_amazon = _offer(sessions, product_id=product.id, store_id=store_amazon.id)
    offer_kabum = _offer(sessions, product_id=product.id, store_id=store_kabum.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_amazon.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_kabum.id)
    _observe(
        sessions,
        offer_id=offer_amazon.id,
        store_id=store_amazon.id,
        mission_id=mission.id,
        amount="4599.00",
        observed_at=NOW,
    )
    _observe(
        sessions,
        offer_id=offer_kabum.id,
        store_id=store_kabum.id,
        mission_id=mission.id,
        amount="4399.00",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer_amazon.id, user_id=user.id, period="all"
    )

    codes = {series.store_code for series in result.series}
    assert codes == {"amazon", "kabum"}
    assert result.metrics.min_amount == Decimal("4399.00")


# --- Desempate dentro da mesma loja/dia: duas Offers, mesma loja ------------


def test_two_offers_same_store_same_day_collapse_to_one_point_at_the_low(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store = _store(sessions, code="amazon")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    offer_a = _offer(sessions, product_id=product.id, store_id=store.id)
    offer_b = _offer(sessions, product_id=product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_a.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_b.id)
    _observe(
        sessions,
        offer_id=offer_a.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="4599.00",
        observed_at=NOW,
    )
    _observe(
        sessions,
        offer_id=offer_b.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="4199.99",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer_a.id, user_id=user.id, period="all"
    )

    assert len(result.series) == 1
    assert len(result.series[0].points) == 1
    assert result.series[0].points[0].amount == Decimal("4199.99")


# --- Moeda divergente nunca é misturada --------------------------------------


def test_offer_with_different_currency_never_mixes_into_reference_series(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store_amazon = _store(sessions, code="amazon")
    store_kabum = _store(sessions, code="kabum")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    anchor_offer = _offer(sessions, product_id=product.id, store_id=store_amazon.id)
    foreign_offer = _offer(sessions, product_id=product.id, store_id=store_kabum.id)
    _link(sessions, mission_id=mission.id, offer_id=anchor_offer.id)
    _link(sessions, mission_id=mission.id, offer_id=foreign_offer.id)
    _observe(
        sessions,
        offer_id=anchor_offer.id,
        store_id=store_amazon.id,
        mission_id=mission.id,
        amount="4599.00",
        currency="BRL",
        observed_at=NOW,
    )
    _observe(
        sessions,
        offer_id=foreign_offer.id,
        store_id=store_kabum.id,
        mission_id=mission.id,
        amount="999.00",
        currency="USD",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=anchor_offer.id, user_id=user.id, period="all"
    )

    assert result.currency == "BRL"  # âncora tem observação própria -- vence
    assert len(result.series) == 1
    assert result.series[0].store_code == "amazon"


# --- G: rodada de frescor -- "atual" respeita frescor e seleção de loja -----


def test_stale_offer_excluded_from_current_amount_but_kept_in_series(
    integration_database,
) -> None:
    """Pedido explícito do dono do produto: "atual" nunca deriva do
    último ponto do gráfico sem checar frescor. Confirmação com mais de
    150min (2x o máximo de cadência NORMAL, `STALE_GRACE_MULTIPLIER`)
    fica de fora do `current_amount`, mas o ponto histórico continua no
    `series` -- a confirmação em si nunca deixou de ser válida, só
    envelheceu para fins de "preço atual"."""
    sessions, user, mission, store, _product, offer = _setup_single_offer(
        integration_database
    )
    stale_observed_at = NOW - timedelta(minutes=151)
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        observed_at=stale_observed_at,
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all", now=NOW
    )

    assert len(result.series) == 1
    assert result.series[0].points[0].amount == Decimal("3900.00")  # histórico intocado
    assert result.metrics.current_amount is None  # frescor exclui do "atual"


def test_current_amount_respects_selected_store_ids(integration_database) -> None:
    """Indicadores acompanham as lojas selecionadas (pedido explícito):
    com as duas lojas, o menor preço atual é o mais barato entre as
    duas; restringindo a `store_ids` só à loja mais cara, "atual" passa
    a refletir SÓ ela -- nunca a mais barata de uma loja não
    selecionada."""
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store_amazon = _store(sessions, code="amazon")
    store_kabum = _store(sessions, code="kabum")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    amazon_offer = _offer(sessions, product_id=product.id, store_id=store_amazon.id)
    kabum_offer = _offer(sessions, product_id=product.id, store_id=store_kabum.id)
    _link(sessions, mission_id=mission.id, offer_id=amazon_offer.id)
    _link(sessions, mission_id=mission.id, offer_id=kabum_offer.id)
    _observe(
        sessions,
        offer_id=amazon_offer.id,
        store_id=store_amazon.id,
        mission_id=mission.id,
        amount="4599.00",
        observed_at=NOW,
    )
    _observe(
        sessions,
        offer_id=kabum_offer.id,
        store_id=store_kabum.id,
        mission_id=mission.id,
        amount="4199.00",  # mais barata
        observed_at=NOW,
    )

    both = _fetch(
        integration_database, offer_id=amazon_offer.id, user_id=user.id, period="all"
    )
    only_amazon = _fetch(
        integration_database,
        offer_id=amazon_offer.id,
        user_id=user.id,
        period="all",
        store_ids=frozenset({store_amazon.id}),
    )

    assert both.metrics.current_amount == Decimal("4199.00")  # a mais barata das 2
    assert len(both.series) == 2
    assert only_amazon.metrics.current_amount == Decimal("4599.00")  # só amazon
    assert len(only_amazon.series) == 1
    assert only_amazon.series[0].store_code == "amazon"


def test_current_amount_freshness_query_count_does_not_scale_with_offer_count(
    integration_database,
) -> None:
    """Sem N+1 (mesma regra já aplicada em `load_mission_list_extras`,
    `test_load_mission_list_extras_query_count_does_not_scale_with_
    page_size`): o número de consultas SQL para resolver `current_amount`
    precisa ser o MESMO com 1 ou com 5 ofertas concorrentes do mesmo
    Product/loja -- `resolve_offers_freshness_batch` nunca chama
    `resolve_offer_freshness` (ou qualquer consulta de frescor) uma vez
    por oferta."""
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store = _store(sessions, code="amazon")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")

    def _seed_offers(count: int):
        offer_ids = []
        for index in range(count):
            offer = _offer(sessions, product_id=product.id, store_id=store.id)
            _link(sessions, mission_id=mission.id, offer_id=offer.id)
            _observe(
                sessions,
                offer_id=offer.id,
                store_id=store.id,
                mission_id=mission.id,
                amount=f"{4000 + index}.00",
                observed_at=NOW,
            )
            offer_ids.append(offer.id)
        return offer_ids

    def count_queries(offer_ids: list) -> int:
        counter = {"n": 0}

        def before_cursor_execute(*_args, **_kwargs):
            counter["n"] += 1

        engine = integration_database.async_engine.sync_engine
        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            _fetch(
                integration_database,
                offer_id=offer_ids[0],
                user_id=user.id,
                period="all",
            )
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)
        return counter["n"]

    one_offer_ids = _seed_offers(1)
    queries_for_one = count_queries(one_offer_ids)

    five_offer_ids = _seed_offers(4)  # +4 = 5 ofertas concorrentes no total
    queries_for_five = count_queries(one_offer_ids + five_offer_ids)

    assert queries_for_one == queries_for_five, (
        f"esperava o mesmo número de consultas para 1 e para 5 ofertas "
        f"concorrentes -- {queries_for_one} vs {queries_for_five}: "
        "cálculo de frescor em lote parece ter regredido para uma "
        "consulta por oferta"
    )


# --- H: duas coletas reais em dias diferentes, mesmo preço -- confirmação
# em cada data, sem duplicar o estado comercial (pedido explícito do dono
# do produto: prova via o caminho REAL do orquestrador, não inserção
# direta de PriceObservation) ---------------------------------------------


class _StablePriceProvider:
    """Mesmo preço em toda coleta -- dispara o dedupe da TASK-093
    (nenhuma `PriceObservation` nova na 2a rodada), mas cada rodada
    ainda grava seu próprio `SharedCollectionOffer` via
    `orchestration._persist_phase_a` (ver docstring de
    `_fetch_daily_low_points`)."""

    def __init__(self, source_code: str, external_id: str, raw_price: str) -> None:
        self.source_code = source_code
        self.external_id = external_id
        self.raw_price = raw_price

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/price-history-stable-{self.external_id}",
                    title="NVIDIA GeForce RTX 5070 Ti",
                    collected_at=completed,
                    external_id=self.external_id,
                    raw_price=self.raw_price,
                    raw_currency="BRL",
                    raw_availability="Disponível",
                    raw_condition="Novo",
                    evidence={"card_text": "stable price evidence"},
                ),
            ),
        )


class _AlwaysMatchAIManager:
    async def generate(self, request):
        content = (
            '{"relevance": "match"}'
            if request.purpose == "classify_offer_relevance"
            else '{"display_title": "Synthetic GPU"}'
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=datetime.now(UTC),
        )


def test_same_price_two_different_days_appears_as_two_confirmations_without_duplicating_state(
    integration_database,
) -> None:
    """Duas coletas REAIS (via `CollectionOrchestrator`, não `_observe`
    direto) em dias comerciais diferentes, com o MESMO preço: o dedupe da
    TASK-093 continua intacto (nenhuma `PriceObservation` nova na 2a
    rodada, estado comercial nunca duplicado), mas a série do gráfico
    precisa mostrar DUAS confirmações -- uma em cada data real de coleta
    -- porque cada `CollectionRun` grava seu próprio `SharedCollectionOffer`
    independente de ter criado observação nova."""
    day1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    day2 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    sessions = integration_database.sessions
    async_sessions = integration_database.async_sessions

    with sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        user = User(display_name="TASK-098 stable price", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id, title="stable price", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="RTX 5070 Ti"),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=day1,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission.id, store_id=store.id),
            )
        )
        user_id = user.id
        mission_id = mission.id
        store_id = store.id

    provider = _StablePriceProvider("amazon", "price-history-stable-01", "R$ 949,90")
    orchestrator = CollectionOrchestrator(
        async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=day1))

    with sessions.begin() as session:
        offer = session.scalar(
            select(Offer).where(Offer.external_id == "price-history-stable-01")
        )
        assert offer is not None
        offer_id = offer.id
        source = session.get(MissionSource, (mission_id, store_id))
        source.next_eligible_at = day2 - timedelta(seconds=1)
        source.next_run_at = day2 - timedelta(seconds=1)
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = day2

    asyncio.run(orchestrator.run_batch(now=day2))

    with sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).where(PriceObservation.offer_id == offer_id)
            )
        )
        assert len(observations) == 1, (
            "mesmo preço em dias diferentes nunca deve criar uma segunda "
            "PriceObservation -- dedupe da TASK-093 precisa continuar intacto"
        )

    result = _fetch(
        integration_database, offer_id=offer_id, user_id=user_id, period="7d", now=day2
    )
    assert len(result.series) == 1
    points = result.series[0].points
    point_days = {point.day for point in points}
    assert point_days == {day1.date(), day2.date()}, (
        f"esperava confirmações em {day1.date()} e {day2.date()}, "
        f"recebido {sorted(point_days)} -- a segunda coleta (mesmo preço, "
        "sem PriceObservation nova) precisa aparecer como confirmação da "
        "SUA PRÓPRIA data, não desaparecer nem se fundir com a primeira"
    )
    assert all(point.amount == Decimal("949.90") for point in points)


def test_gap_without_any_collection_never_invents_a_confirmation(
    integration_database,
) -> None:
    """3 coletas reais, preço estável, com um dia SEM nenhuma coleta no
    meio (dia2 nunca roda) -- a série precisa ter exatamente os dias
    REALMENTE confirmados (dia1 e dia3), nunca um ponto fabricado para
    o dia sem coleta nenhuma. `connectNulls` no frontend é só desenho de
    linha entre pontos reais -- nunca dado novo; este teste prova que o
    BACKEND não inventa a confirmação em si."""
    day1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    day3 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)  # dia2 (09/02) nunca coletado
    sessions = integration_database.sessions
    async_sessions = integration_database.async_sessions

    with sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        user = User(display_name="TASK-098 gap", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(user_id=user.id, title="gap", status=MissionStatus.ACTIVE)
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="RTX 5070 Ti"),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=day1,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission.id, store_id=store.id),
            )
        )
        user_id = user.id
        mission_id = mission.id
        store_id = store.id

    provider = _StablePriceProvider("amazon", "price-history-gap-01", "R$ 949,90")
    orchestrator = CollectionOrchestrator(
        async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=day1))

    with sessions.begin() as session:
        offer = session.scalar(
            select(Offer).where(Offer.external_id == "price-history-gap-01")
        )
        offer_id = offer.id
        source = session.get(MissionSource, (mission_id, store_id))
        source.next_eligible_at = day3 - timedelta(seconds=1)
        source.next_run_at = day3 - timedelta(seconds=1)
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = day3

    asyncio.run(orchestrator.run_batch(now=day3))

    result = _fetch(
        integration_database, offer_id=offer_id, user_id=user_id, period="7d", now=day3
    )
    points = result.series[0].points
    point_days = {point.day for point in points}
    assert point_days == {day1.date(), day3.date()}, (
        f"esperava só {day1.date()} e {day3.date()} (dia intermediário "
        f"nunca coletado), recebido {sorted(point_days)} -- nenhuma "
        "confirmação pode ser inventada num intervalo sem coleta real"
    )
