"""Fluxo real de agenda, coleta, persistência e eventos da TASK-062."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.collection.errors import ProviderBlockedError
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.orchestration import CollectionOrchestrator, claim_due_collections
from app.events import Event, EventType
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.offers.models import Offer
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


class _AlwaysMatchAIManager:
    """AIProviderManager de teste (TASK-063): sempre MATCH + título fixo.

    Fronteira de IA controlada, como toda fronteira externa nos testes de
    integração desta suíte -- não chama nenhum provedor real.
    """

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


class _SuccessfulProvider:
    source_code = "pichau"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/{uuid4().hex}",
                    title="TASK-062 synthetic GPU",
                    collected_at=completed,
                    external_id="task062-stable-offer",
                    raw_price="R$ 1.900,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


class _FailingProvider:
    source_code = "kabum"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        raise ProviderBlockedError(self.source_code, 403)


class _EmptyProvider:
    source_code = "pichau"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        return CollectionResult(
            self.source_code,
            request.requested_at,
            request.requested_at,
        )


def _seed_due_mission(sessions, now: datetime) -> tuple:
    with sessions.begin() as session:
        stores = {
            store.code: store
            for store in session.scalars(
                select(Store).where(Store.code.in_(("pichau", "kabum")))
            )
        }
        user = User(display_name="TASK-062 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-062 orchestration",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission.id,
                    search_query="synthetic GPU",
                    target_amount=Decimal("2000"),
                    target_currency="BRL",
                ),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                *(
                    MissionSource(mission_id=mission.id, store_id=stores[code].id)
                    for code in ("pichau", "kabum")
                ),
            )
        )
        return mission.id, stores["pichau"].id, stores["kabum"].id


def test_orchestrator_isolates_source_failure_and_publishes_real_events(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )

    result = asyncio.run(orchestrator.run_batch(now=now))

    assert result.claimed == 2
    assert result.succeeded == 1
    assert result.failed == 1
    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun)
                .where(CollectionRun.mission_id == mission_id)
                .order_by(CollectionRun.store_id)
            )
        )
        status_by_store = {run.store_id: run.status for run in runs}
        assert status_by_store == {
            pichau_id: CollectionRunStatus.SUCCEEDED,
            kabum_id: CollectionRunStatus.FAILED,
        }
        assert session.scalar(select(func.count(PriceObservation.id))) == 1
        event_types = set(
            session.scalars(
                select(Event.event_type).where(Event.mission_id == mission_id)
            )
        )
        assert event_types == {
            EventType.COLLECTION_COMPLETED_V1.value,
            EventType.COLLECTION_FAILED_V1.value,
            EventType.PRICE_TARGET_REACHED_V1.value,
            # TASK-068: os dois sources ficaram terminais nesta mesma rodada
            # (pichau sucesso, kabum bloqueado) -- a pré-lista dispara junto.
            EventType.MISSION_PRELIST_READY_V1.value,
        }
        failed = session.scalar(
            select(Event).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.COLLECTION_FAILED_V1.value,
            )
        )
        assert failed is not None
        assert failed.payload["failure_code"] == "provider_blocked"

    assert asyncio.run(orchestrator.run_batch(now=now)).claimed == 0


class _FixedOfferProvider:
    """Sempre devolve a mesma oferta real (mesmo `external_id`/URL).

    Simula duas missões diferentes encontrando o mesmo anúncio de verdade
    numa loja -- usado para provar que o estado de "alvo já atingido"
    (TASK-063/DEC-048) não vaza de uma missão para outra só porque as duas
    coletaram a mesma `Offer`.
    """

    source_code = "pichau"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/shared-task-063-offer",
                    title="TASK-063 shared GPU",
                    collected_at=completed,
                    external_id="task063-shared-offer",
                    raw_price="R$ 900,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_target_reached_state_does_not_leak_between_missions_sharing_an_offer(
    integration_database,
) -> None:
    """DEC-048: duas missões que encontram a mesma `Offer` não interferem.

    Antes da correção, `previous` era a última `PriceObservation` daquela
    `Offer` em qualquer missão -- a segunda missão a coletar o mesmo anúncio
    herdava o "alvo já atingido" da primeira e nunca gerava seu próprio
    evento. Cada missão precisa avaliar cruzamento de alvo pela sua própria
    história, mesmo compartilhando a `Offer`.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        assert store is not None
        user = User(display_name="DEC-048 shared offer", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission_a = Mission(
            user_id=user.id, title="mission a shared offer", status=MissionStatus.ACTIVE
        )
        mission_b = Mission(
            user_id=user.id, title="mission b shared offer", status=MissionStatus.ACTIVE
        )
        session.add_all((mission_a, mission_b))
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission_a.id,
                    search_query="TASK-063 shared GPU",
                    target_amount=Decimal("2000"),
                    target_currency="BRL",
                ),
                MissionSource(mission_id=mission_a.id, store_id=store.id),
                # missao B so ganha MissionSource (e agenda) antes do ciclo 2,
                # de proposito: ensure_missing_schedules so cria agenda para
                # missao com fonte selecionada, entao a missao B fica fora do
                # ciclo 1 (nao existe MissionSource dela ainda).
                MissionCriteria(
                    mission_id=mission_b.id,
                    search_query="TASK-063 shared GPU",
                    target_amount=Decimal("2000"),
                    target_currency="BRL",
                ),
            )
        )
        mission_a_id, mission_b_id, store_id = mission_a.id, mission_b.id, store.id

    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_FixedOfferProvider(),)),
        ai_manager=_AlwaysMatchAIManager(),
    )

    def _target_events(mission_id):
        with integration_database.sessions() as session:
            return session.scalar(
                select(func.count(Event.id)).where(
                    Event.mission_id == mission_id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )

    with integration_database.sessions.begin() as session:
        session.add(
            MissionSchedule(
                mission_id=mission_a_id,
                # intervalo bem longo de propósito: a missão A não deve
                # ficar due de novo dentro da janela deste teste, para que
                # o ciclo 2 prove isoladamente o comportamento da missão B.
                interval_minutes=100_000,
                next_run_at=now,
                is_enabled=True,
            )
        )

    # Ciclo 1: só a missão A está due -- primeira vez que a oferta é vista,
    # ela cruza o alvo e gera seu próprio evento.
    result_a = asyncio.run(orchestrator.run_batch(now=now))
    assert (result_a.claimed, result_a.succeeded) == (1, 1)
    assert _target_events(mission_a_id) == 1
    assert _target_events(mission_b_id) == 0

    # Ciclo 2: agora a missão B fica due e coleta a MESMA Offer pela
    # primeira vez -- sem a correção, herdaria o "já atingido" da missão A
    # e não geraria evento nenhum.
    later = now + timedelta(minutes=90)
    with integration_database.sessions.begin() as session:
        session.add(MissionSource(mission_id=mission_b_id, store_id=store_id))
        session.add(
            MissionSchedule(
                mission_id=mission_b_id,
                interval_minutes=60,
                next_run_at=later,
                is_enabled=True,
            )
        )
    result_b = asyncio.run(orchestrator.run_batch(now=later))
    assert (result_b.claimed, result_b.succeeded) == (1, 1)
    assert _target_events(mission_a_id) == 1  # intocado
    assert _target_events(mission_b_id) == 1  # não suprimido pela missão A


def test_concurrent_claimers_never_duplicate_a_source(integration_database) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, _, _ = _seed_due_mission(integration_database.sessions, now)
    barrier = Barrier(2)

    def claim() -> tuple:
        with integration_database.sessions.begin() as session:
            barrier.wait(timeout=10)
            return claim_due_collections(session, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: claim(), range(2)))

    assert sorted(len(result) for result in results) == [0, 2]
    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_id)
            )
        )
        assert len(runs) == 2
        assert len({(run.mission_id, run.store_id) for run in runs}) == 2


def test_source_backoff_lifecycle_across_batches(integration_database) -> None:
    """Ciclo completo do backoff por fonte (DEC-046) num intervalo de 30 min.

    Usa sempre `now=datetime.now(UTC)` real em cada `run_batch`: quando uma
    fonte falha, `finished_at` vem de `utc_now()` real, então `started_at`
    nunca pode estar no futuro em relação a isso. Simular "o tempo passou"
    é feito escrevendo direto no banco entre os ciclos (agenda/backoff),
    não avançando `now` para o futuro. Cada chamada abre sessão/transação
    novas e só lê o estado já persistido -- equivalente a um restart do
    worker entre os ciclos, então este teste também comprova que o backoff
    sobrevive a isso.
    """
    # microsecond=0: _SuccessfulProvider faz completed_at = requested_at com
    # microsegundo fixo em 500000, e completed_at >= started_at é exigido;
    # sem truncar, um requested_at com microsegundo > 500000 quebraria isso.
    now0 = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        stores = {
            store.code: store
            for store in session.scalars(
                select(Store).where(Store.code.in_(("pichau", "kabum")))
            )
        }
        user = User(display_name="DEC-046 lifecycle", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id, title="backoff lifecycle", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="synthetic GPU"),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=30,
                    next_run_at=now0,
                    is_enabled=True,
                ),
                *(
                    MissionSource(mission_id=mission.id, store_id=stores[code].id)
                    for code in ("pichau", "kabum")
                ),
            )
        )
        mission_id = mission.id
        pichau_id, kabum_id = stores["pichau"].id, stores["kabum"].id

    def _source_state(store_id):
        with integration_database.sessions() as session:
            source = session.get(MissionSource, (mission_id, store_id))
            return source.consecutive_blocks, source.next_eligible_at

    def _run_count_for(store_id):
        with integration_database.sessions() as session:
            return session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == mission_id,
                    CollectionRun.store_id == store_id,
                )
            )

    def _make_due_again(due_at):
        with integration_database.sessions.begin() as session:
            schedule = session.scalar(
                select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
            )
            schedule.next_run_at = due_at

    def _expire_backoff(store_id, due_at):
        with integration_database.sessions.begin() as session:
            source = session.get(MissionSource, (mission_id, store_id))
            source.next_eligible_at = due_at - timedelta(seconds=1)

    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )

    # Ciclo 1: pichau sucede, kabum leva 403 confirmado -> 1o bloqueio.
    before = datetime.now(UTC)
    result = asyncio.run(orchestrator.run_batch(now=now0))
    after = datetime.now(UTC)
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)
    assert _source_state(pichau_id) == (0, None)
    kabum_blocks, kabum_eligible = _source_state(kabum_id)
    assert kabum_blocks == 1
    assert (
        before + timedelta(minutes=60)
        <= kabum_eligible
        <= after + timedelta(minutes=60)
    )  # 30 * 2**1

    # Ciclo 2: agenda due de novo, kabum ainda em backoff real (só
    # elegível daqui a ~60 min) -> só pichau é reivindicada; kabum intocado.
    # `due_at` único evita corrida entre a escrita da agenda e o `now`
    # passado ao run_batch (ambos precisam concordar exatamente).
    due_at = datetime.now(UTC).replace(microsecond=0)
    _make_due_again(due_at)
    result = asyncio.run(orchestrator.run_batch(now=due_at))
    assert (result.claimed, result.succeeded, result.failed) == (1, 1, 0)
    assert _run_count_for(kabum_id) == 1  # não ganhou run novo neste ciclo
    assert _run_count_for(pichau_id) == 2
    assert _source_state(kabum_id) == (kabum_blocks, kabum_eligible)  # intocado

    # Ciclo 3: backoff do kabum expira (simulado) e a agenda fica due de
    # novo -> kabum volta a ser reivindicada, falha de novo (2o bloqueio).
    due_at = datetime.now(UTC).replace(microsecond=0)
    _expire_backoff(kabum_id, due_at)
    _make_due_again(due_at)
    before = datetime.now(UTC)
    result = asyncio.run(orchestrator.run_batch(now=due_at))
    after = datetime.now(UTC)
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)
    assert _run_count_for(kabum_id) == 2
    kabum_blocks, kabum_eligible = _source_state(kabum_id)
    assert kabum_blocks == 2
    assert (
        before + timedelta(minutes=120)
        <= kabum_eligible
        <= after + timedelta(minutes=120)
    )  # 30 * 2**2
    assert _source_state(pichau_id) == (0, None)  # sucesso continua resetado


def test_all_sources_in_backoff_creates_no_run_and_schedule_stays_due(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    future = now + timedelta(minutes=90)
    with integration_database.sessions.begin() as session:
        for store_id in (pichau_id, kabum_id):
            source = session.get(MissionSource, (mission_id, store_id))
            source.next_eligible_at = future
            source.consecutive_blocks = 3

    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )

    result = asyncio.run(orchestrator.run_batch(now=now))

    assert (result.claimed, result.succeeded, result.failed) == (0, 0, 0)
    with integration_database.sessions.begin() as session:
        assert (
            session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == mission_id
                )
            )
            == 0
        )
        # a missao continua due (nao avancou): sera reexaminada no proximo
        # poll, sem esperar um intervalo inteiro quando uma fonte liberar.
        next_run_at = session.scalar(
            select(MissionSchedule.next_run_at).where(
                MissionSchedule.mission_id == mission_id
            )
        )
        assert next_run_at == now


class _KabumOfferProvider:
    """Sempre `kabum`; preço configurável por instância (TASK-068)."""

    source_code = "kabum"

    def __init__(self, raw_price: str) -> None:
        self._raw_price = raw_price

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/task068-kabum-offer",
                    title="TASK-068 synthetic GPU (kabum)",
                    collected_at=completed,
                    external_id="task068-kabum-offer",
                    raw_price=self._raw_price,
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_prelist_ready_fires_once_then_errata_corrects_a_cheaper_late_offer(
    integration_database,
) -> None:
    """TASK-068: pré-lista dispara uma vez após a 1a rodada completa; uma
    coleta posterior mais barata que a base já enviada gera uma única
    correção -- nunca duas, nunca antes da rodada completar.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )

    # Rodada 1: pichau sucede (R$ 1.900,00), kabum leva bloqueio confirmado
    # (403) -- a rodada conta como completa mesmo assim (falha é terminal).
    orchestrator_round1 = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result1 = asyncio.run(orchestrator_round1.run_batch(now=now))
    assert (result1.claimed, result1.succeeded, result1.failed) == (2, 1, 1)

    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        assert mission.prelist_sent is True
        assert mission.prelist_errata_sent is False
        assert mission.prelist_lowest_amount == Decimal("1900.0000")
        assert mission.prelist_lowest_currency == "BRL"
        ready_event = session.scalar(
            select(Event).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_READY_V1.value,
            )
        )
        assert ready_event is not None
        assert Decimal(ready_event.payload["first_amount"]) == Decimal("1900.00")
        # kabum falhou -- nenhuma segunda oferta para mostrar ainda.
        assert ready_event.payload["second_offer_id"] is None
        errata_before = session.scalar(
            select(func.count(Event.id)).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_ERRATA_V1.value,
            )
        )
        assert errata_before == 0

    # Libera kabum do backoff e deixa a agenda due de novo, simulando a
    # rodada seguinte -- desta vez kabum responde mais barato (R$ 1.500,00)
    # que a base já enviada (R$ 1.900,00): deve gerar a única correção.
    due_at = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        source = session.get(MissionSource, (mission_id, kabum_id))
        source.next_eligible_at = due_at - timedelta(seconds=1)
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = due_at

    orchestrator_round2 = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _KabumOfferProvider("R$ 1.500,00"))),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result2 = asyncio.run(orchestrator_round2.run_batch(now=due_at))
    assert (result2.claimed, result2.succeeded, result2.failed) == (2, 2, 0)

    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        assert mission.prelist_sent is True
        assert mission.prelist_errata_sent is True
        # a base de comparação (prelist_lowest_amount) não é reescrita pela
        # correção -- continua registrando o valor original enviado.
        assert mission.prelist_lowest_amount == Decimal("1900.0000")
        errata_events = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_id,
                    Event.event_type == EventType.MISSION_PRELIST_ERRATA_V1.value,
                )
            )
        )
        assert len(errata_events) == 1
        assert Decimal(errata_events[0].payload["current_amount"]) == Decimal("1500.00")
        assert Decimal(errata_events[0].payload["previous_lowest_amount"]) == Decimal(
            "1900.00"
        )
        assert errata_events[0].aggregate_id == mission_id

    # Rodada 3: kabum encontra um preço ainda mais barato (R$ 1.000,00) --
    # a correção já foi usada; nenhuma segunda correção deve ser publicada.
    due_at_3 = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        source = session.get(MissionSource, (mission_id, kabum_id))
        source.next_eligible_at = due_at_3 - timedelta(seconds=1)
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = due_at_3

    orchestrator_round3 = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _KabumOfferProvider("R$ 1.000,00"))),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result3 = asyncio.run(orchestrator_round3.run_batch(now=due_at_3))
    assert (result3.claimed, result3.succeeded, result3.failed) == (2, 2, 0)

    with integration_database.sessions.begin() as session:
        errata_count = session.scalar(
            select(func.count(Event.id)).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_ERRATA_V1.value,
            )
        )
        assert errata_count == 1  # continua uma única correção, nunca duas


class _PricedProvider:
    """Loja/preço/frete configuráveis -- usado para provar que a pré-lista
    ranqueia por `PriceObservation.amount` (preço do produto), nunca por
    `total_amount` (preço+frete), já que o frete não é confiável/
    comparável entre lojas nesta TASK."""

    def __init__(self, source_code: str, raw_price: str, raw_shipping: str) -> None:
        self.source_code = source_code
        self._raw_price = raw_price
        self._raw_shipping = raw_shipping

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/task068-{self.source_code}-shipping",
                    title=f"TASK-068 synthetic GPU ({self.source_code})",
                    collected_at=completed,
                    external_id=f"task068-{self.source_code}-shipping-offer",
                    raw_price=self._raw_price,
                    raw_currency="BRL",
                    raw_shipping=self._raw_shipping,
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_prelist_ranks_by_product_amount_ignoring_shipping(
    integration_database,
) -> None:
    """TASK-068 (correção pós-revisão do usuário): a base de comparação é
    sempre `amount` (preço anunciado do produto) -- nunca `total_amount`
    (preço+frete). Cenário desenhado para que as duas bases discordem: se
    o código usasse `total_amount` por engano, a loja com frete pago
    venceria; usando `amount`, ela perde porque o preço do produto sozinho
    é mais barato.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    # pichau: amount=1000,00, frete=500,00 -> total=1500,00 (mais barato em
    # amount, mais caro em total).
    # kabum: amount=1100,00, frete grátis -> total=1100,00 (mais barato em
    # total, mais caro em amount).
    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter(
            (
                _PricedProvider("pichau", "R$ 1.000,00", "R$ 500,00"),
                _PricedProvider("kabum", "R$ 1.100,00", "Frete grátis"),
            )
        ),
        ai_manager=_AlwaysMatchAIManager(),
    )

    result = asyncio.run(orchestrator.run_batch(now=now))
    assert (result.claimed, result.succeeded, result.failed) == (2, 2, 0)

    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        # amount (sem frete) é a base -- pichau (1000) vence kabum (1100),
        # mesmo pichau custando mais no total (1500 > 1100).
        assert mission.prelist_lowest_amount == Decimal("1000.0000")
        ready_event = session.scalar(
            select(Event).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_READY_V1.value,
            )
        )
        assert ready_event is not None
        assert Decimal(ready_event.payload["first_amount"]) == Decimal("1000.00")
        assert Decimal(ready_event.payload["second_amount"]) == Decimal("1100.00")
        pichau_offer_id = session.scalar(
            select(Offer.id).where(Offer.store_id == pichau_id)
        )
        assert ready_event.payload["first_offer_id"] == str(pichau_offer_id)


def test_backfill_runs_old_active_mission_but_ignores_paused(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        assert store is not None
        user = User(display_name="TASK-062 backfill", role=UserRole.USER)
        session.add(user)
        session.flush()
        active = Mission(
            user_id=user.id, title="old active mission", status=MissionStatus.ACTIVE
        )
        paused = Mission(
            user_id=user.id, title="paused mission", status=MissionStatus.PAUSED
        )
        session.add_all((active, paused))
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=active.id, search_query="GPU"),
                MissionSource(mission_id=active.id, store_id=store.id),
                MissionCriteria(mission_id=paused.id, search_query="GPU"),
                MissionSource(mission_id=paused.id, store_id=store.id),
                MissionSchedule(
                    mission_id=paused.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
            )
        )
        active_id, paused_id = active.id, paused.id

    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_EmptyProvider(),)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result = asyncio.run(orchestrator.run_batch(now=now))

    assert result.claimed == 1
    assert result.succeeded == 1
    with integration_database.sessions.begin() as session:
        assert (
            session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == active_id
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == paused_id
                )
            )
            == 0
        )
        completed = session.scalar(
            select(Event).where(
                Event.mission_id == active_id,
                Event.event_type == EventType.COLLECTION_COMPLETED_V1.value,
            )
        )
        assert completed is not None
        assert completed.payload["observation_count"] == 0
