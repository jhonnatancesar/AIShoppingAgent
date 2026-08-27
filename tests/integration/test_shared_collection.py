"""Coleta compartilhada por `(MonitoringItem, store)` -- TASK-112, fase 3A.

Prova, contra PostgreSQL real: provider chamado uma vez por `(item,
store)`; `Offer`/`PriceObservation` persistidos uma vez (dedupe TASK-093
reaproveitado); fan-out individual por Mission elegível (relevância,
alerta, isolamento de erro); critério de busca nasce da identidade
canônica, nunca do texto cru de nenhuma Mission; claim/lock real via
`CollectionRun` (nunca mutex em memória).

Sem fairness/TASK-108, sem scheduler principal, sem coleta externa de
verdade -- providers/IA fake, como o resto da suíte de integração.
"""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
import app.collection.shared_collection as shared_collection_module
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import CadenceConfig
from app.collection.contracts import CollectionRequest, CollectionResult, RawCollectedOffer
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    SharedCollectionOffer,
    SharedFanOutStatus,
    SharedFanOutTask,
)
from app.collection.normalization import PriceNormalizer
from app.collection.orchestration import recover_stale_runs
from app.collection.shared_collection import (
    collect_monitoring_item_store,
    recover_stale_fan_out_tasks,
    resume_shared_collection_fan_out,
)
from sqlalchemy.exc import IntegrityError
from app.database.session import (
    create_async_session_factory,
    create_collection_async_database_engine,
)
from app.events import ConsumptionOutcome, Event, EventType
from app.events.consumption import claim_unconsumed_events_async, record_consumption_attempt_async
from app.missions.models import MissionCommand, MissionCriteria, MissionMonitoringItem
from app.missions.service import create_mission_from_criteria_async, transition_mission_async
from app.offers.models import Offer
from app.products.identity import canonical_collection_criteria
from app.telegram.notifications import TELEGRAM_NOTIFICATION_CONSUMER
from app.users.models import User, UserRole
from sqlalchemy import func, select
from sqlalchemy import text as sa_text

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

# TASK-112 fase 3B: `_claim_shared_collection`/`collect_monitoring_item_store`
# usam agora a política de cadência (`app.collection.cadence`, faixa
# jitterada 45-75min por padrão) em vez do antigo `interval_minutes` fixo
# -- esta suíte (fase 3A) testa claim/lock/fan-out, não a política de
# cadência em si (isso tem cobertura própria, `tests/integration/
# test_cadence_and_high_activity.py`), então usa uma faixa determinística
# (min == max == 60, mesmo valor do antigo default) para preservar as
# asserções de tempo já existentes sem introduzir flakiness.
_DETERMINISTIC_CADENCE = CadenceConfig(normal_min_minutes=60, normal_max_minutes=60)


class _CountingGpuProvider:
    """Sempre a MESMA oferta real (mesmo `external_id`) -- conta chamadas."""

    source_code = "amazon"

    def __init__(self, *, raw_price: str = "3999.90") -> None:
        self.calls: list[CollectionRequest] = []
        self._raw_price = raw_price

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.calls.append(request)
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/shared-gpu",
                    title="NVIDIA GeForce RTX 5070 Ti",
                    collected_at=completed,
                    external_id="shared-gpu-stable",
                    raw_price=self._raw_price,
                    raw_currency="BRL",
                    raw_availability="Em estoque",
                ),
            ),
        )


class _KeywordAwareAIManager:
    """Fake de IA: relevância decidida por uma palavra-chave presente (ou
    não) no `search_query` da própria Mission enviado no request -- prova
    que o fan-out classifica CADA Mission com o critério REAL dela, não um
    veredito único compartilhado."""

    def __init__(self, *, match_keyword: str | None = None) -> None:
        self._match_keyword = match_keyword
        self.calls: list[str] = []

    async def generate(self, request):
        self.calls.append(request.purpose)
        content = request.messages[-1].content
        if request.purpose == "classify_offer_relevance":
            payload = json.loads(content)
            search_query = payload.get("search_query", "")
            if self._match_keyword is None or self._match_keyword in search_query:
                relevance = "match"
            else:
                relevance = "no_match"
            body = json.dumps({"relevance": relevance})
        else:
            body = json.dumps({"display_title": "Produto"})
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=body,
            finished_at=datetime.now(UTC),
        )


def _adapter(provider) -> CollectionAdapter:
    return CollectionAdapter(providers=(provider,))


def _seed_user(sessions, label: str) -> UUID:
    with sessions.begin() as session:
        user = User(display_name=f"TASK-112-3A {label}", role=UserRole.USER)
        session.add(user)
        session.flush()
        return user.id


def _create_mission(integration_database, **kwargs):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await create_mission_from_criteria_async(session, **kwargs)

    return asyncio.run(run())


def _make_mission(
    integration_database,
    user_id: UUID,
    *,
    search_query: str,
    sources: tuple[str, ...],
    target_amount=None,
):
    mission, _codes = _create_mission(
        integration_database,
        user_id=user_id,
        search_query=search_query,
        target_amount=target_amount,
        target_currency="BRL" if target_amount is not None else None,
        source_codes=sources,
        requested_at=NOW,
        actor_type="test",
    )
    return mission


def _monitoring_item_id_for(sessions, mission_id: UUID) -> UUID | None:
    from app.missions.models import MissionMonitoringItem

    with sessions() as session:
        link = session.get(MissionMonitoringItem, mission_id)
        return link.monitoring_item_id if link is not None else None


def _amazon_store_id(integration_database) -> UUID:
    from app.stores.models import Store

    with integration_database.sessions() as session:
        return session.scalar(select(Store.id).where(Store.code == "amazon"))


def _offer_count(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count(Offer.id)))


def _observation_count(sessions) -> int:
    from app.collection.models import PriceObservation

    with sessions() as session:
        return session.scalar(select(func.count(PriceObservation.id)))


def _relevance_rows(sessions, mission_id: UUID) -> list[MissionOfferRelevance]:
    with sessions() as session:
        return list(
            session.scalars(
                select(MissionOfferRelevance).where(
                    MissionOfferRelevance.mission_id == mission_id
                )
            )
        )


def _run(coro):
    return asyncio.run(coro)


def _transition(integration_database, **kwargs):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await transition_mission_async(session, **kwargs)

    return asyncio.run(run())


def _two_missions_sharing_gpu(integration_database, *, label: str, target_a=None, target_b=None):
    user_a = _seed_user(integration_database.sessions, f"{label}-A")
    user_b = _seed_user(integration_database.sessions, f"{label}-B")
    mission_a = _make_mission(
        integration_database,
        user_a,
        search_query="RTX 5070 Ti",
        sources=("amazon",),
        target_amount=target_a,
    )
    mission_b = _make_mission(
        integration_database,
        user_b,
        search_query="NVIDIA GeForce RTX 5070 Ti",
        sources=("amazon",),
        target_amount=target_b,
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id is not None
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_b.id)
    return mission_a, mission_b, item_id


# ---------------------------------------------------------------------------
# 1: provider chamado uma vez; Offer/PriceObservation persistidos uma vez
# ---------------------------------------------------------------------------


def test_provider_called_once_and_offer_observation_persisted_once(
    integration_database,
) -> None:
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="once"
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)  # tudo MATCH

    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert result.claimed is True
    assert result.provider_called is True
    assert result.succeeded is True
    assert len(provider.calls) == 1
    assert _offer_count(integration_database.sessions) == 1
    assert _observation_count(integration_database.sessions) == 1
    assert set(result.fanned_out_mission_ids) == {mission_a.id, mission_b.id}


# ---------------------------------------------------------------------------
# 2: fan-out chega em A+B; targets diferentes -> decisões diferentes
# ---------------------------------------------------------------------------


def test_fan_out_reaches_both_missions_with_relevance_persisted(
    integration_database,
) -> None:
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="fanout"
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    rows_a = _relevance_rows(integration_database.sessions, mission_a.id)
    rows_b = _relevance_rows(integration_database.sessions, mission_b.id)
    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0].offer_id == rows_b[0].offer_id  # mesma Offer compartilhada


def test_different_targets_produce_different_alert_decisions(
    integration_database,
) -> None:
    """Exemplo do pedido: A e B compartilham 9950X3D/Amazon; A alerta
    (target acima do preço coletado), B não (target abaixo). Mesma
    Offer/PriceObservation, decisão diferente por Mission."""
    from decimal import Decimal

    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database,
        label="targets",
        target_a=Decimal("4500.00"),  # acima de 3999.90 -> alerta
        target_b=Decimal("3500.00"),  # abaixo de 3999.90 -> sem alerta
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider(raw_price="3999.90")
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    with integration_database.sessions() as session:
        events_a = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_a.id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )
        )
        events_b = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_b.id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )
        )
    assert len(events_a) == 1
    assert len(events_b) == 0


# ---------------------------------------------------------------------------
# 3: uma Mission irrelevante não afeta outra
# ---------------------------------------------------------------------------


def test_one_irrelevant_mission_does_not_affect_the_other(integration_database) -> None:
    """A e B compartilham a mesma monitoring_key mas textos originais
    diferentes o bastante para a IA (por Mission, nunca compartilhada)
    julgar de forma diferente -- 'XPTO' só está no texto de B."""
    user_a = _seed_user(integration_database.sessions, "irrelevant-A")
    user_b = _seed_user(integration_database.sessions, "irrelevant-B")
    mission_a = _make_mission(
        integration_database, user_a, search_query="RTX 5070 Ti", sources=("amazon",)
    )
    mission_b = _make_mission(
        integration_database, user_b, search_query="RTX 5070 Ti XPTO", sources=("amazon",)
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id is not None
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_b.id)
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword="XPTO")

    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert set(result.fanned_out_mission_ids) == {mission_a.id, mission_b.id}
    rows_a = _relevance_rows(integration_database.sessions, mission_a.id)
    rows_b = _relevance_rows(integration_database.sessions, mission_b.id)
    assert rows_a[0].classification.value == "no_match"
    assert rows_b[0].classification.value == "match"
    # a Offer/coleta compartilhada continuam intactas para as duas.
    assert _offer_count(integration_database.sessions) == 1


# ---------------------------------------------------------------------------
# 4: erro no fan-out de A não afeta B nem marca a coleta compartilhada FAILED
# ---------------------------------------------------------------------------


def test_fan_out_deterministic_error_goes_terminal_on_first_attempt(
    integration_database,
) -> None:
    """Erro isolado por Mission (item 4 original), sob a máquina de
    estados final (rodada 4): um erro genuinamente DETERMINÍSTICO
    (`SharedFanOutTerminalError` -- aqui, MissionCriteria sumiu para A)
    NUNCA vai funcionar numa próxima tentativa, então vai direto para
    `terminal_failed` na PRIMEIRA tentativa, sem gastar o orçamento de
    retry -- e sem nunca afetar B nem a coleta compartilhada."""
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="isolate-terminal"
    )
    amazon_id = _amazon_store_id(integration_database)
    # Corrompe deliberadamente o estado de A -- `_build_mission_phase_a_
    # outcome` levanta `SharedFanOutTerminalError` quando a MissionCriteria
    # não existe mais para a Mission reivindicada (caso determinístico,
    # permanente de propósito).
    with integration_database.sessions.begin() as session:
        session.execute(
            MissionCriteria.__table__.delete().where(
                MissionCriteria.mission_id == mission_a.id
            )
        )

    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert result.succeeded is True  # coleta compartilhada nunca falha por causa de A
    assert result.fan_out_failed_mission_ids == (mission_a.id,)  # terminal já na 1ª tentativa
    assert mission_a.id not in result.fan_out_attention_required_mission_ids
    assert mission_b.id in result.fanned_out_mission_ids
    # B recebeu relevância normalmente, apesar do erro em A.
    assert len(_relevance_rows(integration_database.sessions, mission_b.id)) == 1
    with integration_database.sessions() as session:
        shared_run = session.scalar(
            select(CollectionRun).where(
                CollectionRun.monitoring_item_id == item_id,
                CollectionRun.store_id == amazon_id,
            )
        )
        assert shared_run.status is CollectionRunStatus.SUCCEEDED
        task_a = session.get(SharedFanOutTask, (shared_run.id, mission_a.id))
        assert task_a.status == SharedFanOutStatus.TERMINAL_FAILED
        assert task_a.attempt_count == 1  # nenhum retry gasto -- foi direto
        assert task_a.last_error is not None
        assert task_a.next_retry_at is None
        # a run própria de A (mission_id=A) terminou FAILED, não presa em RUNNING.
        mission_a_run = session.scalar(
            select(CollectionRun).where(
                CollectionRun.mission_id == mission_a.id,
                CollectionRun.store_id == amazon_id,
            )
        )
        assert mission_a_run.status is CollectionRunStatus.FAILED


def test_fan_out_retryable_error_retries_then_becomes_attention_required(
    integration_database, monkeypatch
) -> None:
    """Erro RETRYABLE (item 1, correção de consistência): falhas
    transitórias continuam transitórias -- nunca viram `terminal_failed`
    só por `attempt_count` ter batido no limite. Aqui `_run_phase_b`
    (fase de IA do fan-out) é envolvida para levantar um `RuntimeError`
    genérico -- nunca `SharedFanOutTerminalError` -- só para a Mission A,
    tempo suficiente para esgotar `_MAX_FAN_OUT_ATTEMPTS`; a tarefa vai
    para `attention_required` (auditável, reprocessável), nunca
    `terminal_failed`, nunca perdida em silêncio. B nunca é afetado
    (prova que o erro é isolado por Mission mesmo sendo um erro "de
    infraestrutura" e não de dado da própria Mission)."""
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="isolate-retryable"
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    max_attempts = shared_collection_module._MAX_FAN_OUT_ATTEMPTS
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    # Exatamente `max_attempts` falhas -- esgota justo no limite, e a
    # tentativa seguinte (depois do reset manual, mais abaixo) sucede via
    # `_run_phase_b` real, provando que `attention_required` nunca é um
    # estado morto.
    remaining_failures = {"count": max_attempts}
    real_run_phase_b = shared_collection_module._run_phase_b

    async def _flaky_run_phase_b(phase_a, manager, profile, **kwargs):
        if phase_a.mission_id == mission_a.id and remaining_failures["count"] > 0:
            remaining_failures["count"] -= 1
            raise RuntimeError("simulated_transient_infra_failure")
        return await real_run_phase_b(phase_a, manager, profile, **kwargs)

    monkeypatch.setattr(shared_collection_module, "_run_phase_b", _flaky_run_phase_b)

    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert result.succeeded is True  # coleta compartilhada nunca falha por causa de A
    assert result.fan_out_failed_mission_ids == ()  # 1ª tentativa -- transitório, não terminal
    assert mission_b.id in result.fanned_out_mission_ids  # B nunca afetado por A
    assert len(_relevance_rows(integration_database.sessions, mission_b.id)) == 1

    with integration_database.sessions() as session:
        shared_run = session.scalar(
            select(CollectionRun).where(
                CollectionRun.monitoring_item_id == item_id,
                CollectionRun.store_id == amazon_id,
            )
        )
        task_a = session.get(SharedFanOutTask, (shared_run.id, mission_a.id))
        assert task_a.status == SharedFanOutStatus.PENDING
        assert task_a.attempt_count == 1
        assert task_a.next_retry_at is not None  # backoff agendado, não perdido

    # Esgota as tentativas restantes -- a IA continua indisponível para A
    # (fail_times cobre bem mais que max_attempts); cada retomada avança
    # bem além do maior backoff possível para não depender do valor exato.
    became_attention_required = False
    resume_now = NOW
    for _ in range(max_attempts):
        resume_now = resume_now + timedelta(hours=2)
        resume_result = _run(
            resume_shared_collection_fan_out(
                integration_database.async_sessions,
                ai_manager,
                monitoring_item_id=item_id,
                store_id=amazon_id,
                now=resume_now,
            )
        )
        assert resume_result.fan_out_failed_mission_ids == ()  # nunca vira terminal_failed
        if mission_a.id in resume_result.fan_out_attention_required_mission_ids:
            became_attention_required = True
            break
    assert became_attention_required  # esgotou de verdade, nunca ficou tentando pra sempre

    with integration_database.sessions() as session:
        task_a = session.get(SharedFanOutTask, (shared_run.id, mission_a.id))
        assert task_a.status == SharedFanOutStatus.ATTENTION_REQUIRED
        assert task_a.attempt_count == max_attempts
        assert task_a.last_error is not None  # auditável
        assert task_a.next_retry_at is None  # parou de tentar sozinha, não perdida

    # Auditável e reprocessável: nada no schema impede resetar manualmente
    # para `pending` e tentar de novo (ex.: depois de confirmar que a IA
    # voltou) -- não é um estado "morto".
    with integration_database.sessions.begin() as session:
        task_a = session.get(SharedFanOutTask, (shared_run.id, mission_a.id))
        task_a.status = SharedFanOutStatus.PENDING
        task_a.next_retry_at = None
    ai_manager_recovered = _KeywordAwareAIManager(match_keyword=None)
    final_resume = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager_recovered,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=resume_now + timedelta(hours=2),
        )
    )
    assert mission_a.id in final_resume.fanned_out_mission_ids
    with integration_database.sessions() as session:
        task_a = session.get(SharedFanOutTask, (shared_run.id, mission_a.id))
        assert task_a.status == SharedFanOutStatus.DONE
        assert task_a.last_error is not None
        assert task_a.next_retry_at is None
    # B nunca foi afetada por nenhuma das tentativas de A -- continua com
    # exatamente 1 linha de relevância (nunca reprocessada de novo).
    assert len(_relevance_rows(integration_database.sessions, mission_b.id)) == 1


# ---------------------------------------------------------------------------
# 5/6: Mission pausada / sem aquela store não recebe fan-out
# ---------------------------------------------------------------------------


def test_paused_mission_does_not_receive_fan_out(integration_database) -> None:
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="paused"
    )
    amazon_id = _amazon_store_id(integration_database)
    _transition(
        integration_database,
        mission_id=mission_b.id,
        command=MissionCommand.PAUSE,
        expected_state_version=mission_b.state_version,
        actor_type="test",
        transitioned_at=NOW,
    )

    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert result.fanned_out_mission_ids == (mission_a.id,)
    assert _relevance_rows(integration_database.sessions, mission_b.id) == []


def test_mission_without_that_store_does_not_receive_fan_out(integration_database) -> None:
    user_a = _seed_user(integration_database.sessions, "nostore-A")
    user_c = _seed_user(integration_database.sessions, "nostore-C")
    mission_a = _make_mission(
        integration_database, user_a, search_query="RTX 5070 Ti", sources=("amazon",)
    )
    mission_c = _make_mission(
        integration_database, user_c, search_query="RTX 5070 Ti", sources=("kabum",)
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_c.id)
    amazon_id = _amazon_store_id(integration_database)

    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert result.fanned_out_mission_ids == (mission_a.id,)
    assert _relevance_rows(integration_database.sessions, mission_c.id) == []


# ---------------------------------------------------------------------------
# 7: concorrência -- mesma (item, store) nunca executa duas vezes
# ---------------------------------------------------------------------------


def test_concurrent_calls_never_run_the_same_item_store_twice(
    integration_database,
) -> None:
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="race"
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    barrier = Barrier(2)

    def _call():
        async def run():
            engine = create_collection_async_database_engine(integration_database.settings)
            sessions = create_async_session_factory(engine)
            ai_manager = _KeywordAwareAIManager(match_keyword=None)
            try:
                barrier.wait(timeout=10)
                return await collect_monitoring_item_store(
                    sessions,
                    _adapter(provider),
                    ai_manager,
                    monitoring_item_id=item_id,
                    store_id=amazon_id,
                    now=NOW,
                )
            finally:
                await engine.dispose()

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _call(), range(2)))

    claimed = [item for item in results if item.claimed]
    assert len(claimed) == 1
    assert len(provider.calls) == 1
    assert _offer_count(integration_database.sessions) == 1
    assert _observation_count(integration_database.sessions) == 1


# ---------------------------------------------------------------------------
# 8: critério canônico de coleta independe do texto cru de qualquer Mission
# ---------------------------------------------------------------------------


def test_canonical_collection_criteria_is_independent_of_raw_mission_text(
    integration_database,
) -> None:
    user_a = _seed_user(integration_database.sessions, "canonical-A")
    user_b = _seed_user(integration_database.sessions, "canonical-B")
    mission_a = _make_mission(
        integration_database, user_a, search_query="5070 ti", sources=("amazon",)
    )
    mission_b = _make_mission(
        integration_database,
        user_b,
        search_query="Placa de video NVIDIA GeForce RTX 5070 Ti",
        sources=("amazon",),
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_b.id)
    amazon_id = _amazon_store_id(integration_database)

    with integration_database.sessions() as session:
        from app.missions.models import MonitoringItem

        item = session.get(MonitoringItem, item_id)
        expected = canonical_collection_criteria(item.canonical_identity)

    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert len(provider.calls) == 1
    sent_query = provider.calls[0].search_query
    assert sent_query == expected.search_query
    # nunca o texto cru de nenhuma das duas Missions.
    with integration_database.sessions() as session:
        texts = set(
            session.scalars(
                select(MissionCriteria.search_query).where(
                    MissionCriteria.mission_id.in_((mission_a.id, mission_b.id))
                )
            )
        )
    assert sent_query not in texts


def test_canonical_collection_criteria_pure_function_ignores_source() -> None:
    """Nível de motor (sem DB): a mesma identidade canônica sempre produz
    o mesmo CollectionCriteria -- função pura, sem IA, sem Mission."""
    payload = {
        "scope": "specific",
        "category": "gpu",
        "brand": "nvidia",
        "family": "geforce-rtx",
        "model": "5070-ti",
        "variant": "ANY",
        "attributes": {"board_brand": "ANY", "vram": "ANY"},
    }
    first = canonical_collection_criteria(payload)
    second = canonical_collection_criteria(dict(payload))
    assert first == second
    assert first.search_query == "nvidia geforce rtx 5070 ti"
    assert first.model == "5070-ti"


def test_canonical_collection_criteria_includes_explicit_restrictions() -> None:
    payload = {
        "scope": "specific",
        "category": "gpu",
        "brand": "nvidia",
        "family": "geforce-rtx",
        "model": "5070-ti",
        "variant": "ANY",
        "attributes": {"board_brand": "asus", "vram": "ANY"},
    }
    criteria = canonical_collection_criteria(payload)
    assert "asus" in criteria.search_query


# ---------------------------------------------------------------------------
# Correção rodada 2 (fase 3A): item 2 -- persistência comercial roda
# EXATAMENTE UMA VEZ, nunca uma vez por Mission do fan-out.
# ---------------------------------------------------------------------------


def test_shared_persistence_runs_exactly_once_for_three_missions(
    integration_database, monkeypatch
) -> None:
    """Item 2 do pedido: provider = 1 chamada; persistência comercial = 1
    execução (instrumentada diretamente, não só inferida por contagem de
    linhas); fan-out = 3."""
    user_a = _seed_user(integration_database.sessions, "abc-A")
    user_b = _seed_user(integration_database.sessions, "abc-B")
    user_c = _seed_user(integration_database.sessions, "abc-C")
    mission_a = _make_mission(
        integration_database, user_a, search_query="RTX 5070 Ti", sources=("amazon",)
    )
    mission_b = _make_mission(
        integration_database,
        user_b,
        search_query="NVIDIA GeForce RTX 5070 Ti",
        sources=("amazon",),
    )
    mission_c = _make_mission(
        integration_database, user_c, search_query="5070 Ti", sources=("amazon",)
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id is not None
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_b.id)
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_c.id)
    amazon_id = _amazon_store_id(integration_database)

    persist_calls = {"n": 0}
    original_persist = shared_collection_module._persist_shared_offers_and_finish

    async def _counting_persist(*args, **kwargs):
        persist_calls["n"] += 1
        return await original_persist(*args, **kwargs)

    monkeypatch.setattr(
        shared_collection_module, "_persist_shared_offers_and_finish", _counting_persist
    )

    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    assert len(provider.calls) == 1
    assert persist_calls["n"] == 1
    assert set(result.fanned_out_mission_ids) == {mission_a.id, mission_b.id, mission_c.id}
    assert _offer_count(integration_database.sessions) == 1
    assert _observation_count(integration_database.sessions) == 1


# ---------------------------------------------------------------------------
# Item 3 -- claim protege o due slot inteiro (prova determinística,
# complementar ao teste de concorrência real já existente acima).
# ---------------------------------------------------------------------------


def test_claim_rejects_a_second_attempt_at_the_same_due_slot(
    integration_database,
) -> None:
    """Depois de um claim bem-sucedido, o MESMO `now` nunca permite um
    segundo claim -- prova direta de que o recheck de `next_run_at` após
    o lock (`_claim_shared_collection`) é o que protege o due slot, não
    só a UNIQUE de RUNNING."""
    _mission_a, _mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="dueslot"
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    first = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )
    assert first.claimed is True

    second = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )
    assert second.claimed is False
    assert len(provider.calls) == 1  # o segundo nunca chegou a chamar o provider


# ---------------------------------------------------------------------------
# Item 4 -- CollectionRun.mission_id XOR monitoring_item_id garantido pelo
# banco (CHECK constraint), não só por convenção do código Python.
# ---------------------------------------------------------------------------


def test_collection_run_ownership_xor_enforced_by_database(
    integration_database,
) -> None:
    mission_a, _mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="xor"
    )
    amazon_id = _amazon_store_id(integration_database)

    with integration_database.sessions() as session:
        session.add(
            CollectionRun(
                store_id=amazon_id, status=CollectionRunStatus.RUNNING, started_at=NOW
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with integration_database.sessions() as session:
        session.add(
            CollectionRun(
                mission_id=mission_a.id,
                monitoring_item_id=item_id,
                store_id=amazon_id,
                status=CollectionRunStatus.RUNNING,
                started_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    # exatamente um dos dois -- sempre aceito, das duas formas.
    with integration_database.sessions() as session:
        session.add(
            CollectionRun(
                mission_id=mission_a.id,
                store_id=amazon_id,
                status=CollectionRunStatus.RUNNING,
                started_at=NOW,
            )
        )
        session.commit()
    with integration_database.sessions() as session:
        session.add(
            CollectionRun(
                monitoring_item_id=item_id,
                store_id=amazon_id,
                status=CollectionRunStatus.RUNNING,
                started_at=NOW,
            )
        )
        session.commit()


# ---------------------------------------------------------------------------
# Item 6 -- pré-lista/checkpoint no fan-out: individual por Mission, nunca
# duplicado, nunca conta a run compartilhada como run de Mission.
# ---------------------------------------------------------------------------


def test_prelist_fires_individually_and_never_duplicates_across_cycles(
    integration_database,
) -> None:
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="prelist"
    )
    amazon_id = _amazon_store_id(integration_database)
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(_CountingGpuProvider()),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )

    def _prelist_events(mission_id):
        with integration_database.sessions() as session:
            return list(
                session.scalars(
                    select(Event).where(
                        Event.mission_id == mission_id,
                        Event.event_type == EventType.MISSION_PRELIST_READY_V2.value,
                    )
                )
            )

    events_a = _prelist_events(mission_a.id)
    events_b = _prelist_events(mission_b.id)
    assert len(events_a) == 1
    assert len(events_b) == 1

    # próximo ciclo (devido de novo) não duplica a pré-lista de nenhuma
    # das duas Missions -- já foi enviada uma vez, cada uma por si.
    _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(_CountingGpuProvider()),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW + timedelta(hours=2),
        )
    )
    assert len(_prelist_events(mission_a.id)) == 1
    assert len(_prelist_events(mission_b.id)) == 1

    # a run compartilhada (mission_id NULL) nunca é contada como run de
    # nenhuma Mission -- confirmado estruturalmente (CHECK XOR + query de
    # round-complete sempre filtra por `CollectionRun.mission_id ==
    # <mission real>`, que uma run compartilhada nunca satisfaz).
    with integration_database.sessions() as session:
        shared_run = session.scalar(
            select(CollectionRun).where(
                CollectionRun.monitoring_item_id == item_id,
                CollectionRun.store_id == amazon_id,
            )
        )
        assert shared_run.mission_id is None
        mission_a_runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_a.id)
            )
        )
    assert shared_run.id not in {run.id for run in mission_a_runs}
    assert len(mission_a_runs) == 2  # uma por ciclo, nunca a compartilhada


# ---------------------------------------------------------------------------
# Item 7 -- teste sintético de escala local (sem provider externo real).
# ---------------------------------------------------------------------------


def test_synthetic_scale_fan_out_to_many_missions(integration_database) -> None:
    """60 Missions compartilhando o mesmo MonitoringItemStore -- prova
    checkpoints individuais, nenhuma duplicação e ausência de crescimento
    O(N²) óbvio (instrumentação simples, não benchmark)."""
    total = 60
    item_id: UUID | None = None
    missions = []
    for index in range(total):
        user_id = _seed_user(integration_database.sessions, f"scale-{index}")
        mission = _make_mission(
            integration_database, user_id, search_query="RTX 5070 Ti", sources=("amazon",)
        )
        missions.append(mission)
        current_item = _monitoring_item_id_for(integration_database.sessions, mission.id)
        assert current_item is not None
        item_id = item_id or current_item
        assert current_item == item_id

    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    started = time.monotonic()
    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )
    elapsed = time.monotonic() - started

    assert len(provider.calls) == 1
    assert _offer_count(integration_database.sessions) == 1
    assert _observation_count(integration_database.sessions) == 1
    assert len(result.fanned_out_mission_ids) == total
    assert result.fan_out_failed_mission_ids == ()

    with integration_database.sessions() as session:
        relevance_count = session.scalar(
            select(func.count(MissionOfferRelevance.mission_id)).where(
                MissionOfferRelevance.mission_id.in_(m.id for m in missions)
            )
        )
        mission_run_count = session.scalar(
            select(func.count(CollectionRun.id)).where(
                CollectionRun.mission_id.in_(m.id for m in missions)
            )
        )
    assert relevance_count == total  # checkpoint individual, um por Mission
    assert mission_run_count == total  # uma run própria por Mission, nunca a compartilhada

    # Instrumentação simples anti-O(N²) -- não é benchmark, só sinal de
    # desenho ruim: trabalho inteiramente local (sem rede/IA real) para
    # 60 Missions não deveria se aproximar de dezenas de segundos.
    assert elapsed < 30.0, (
        f"fan-out para {total} Missions levou {elapsed:.1f}s -- "
        "investigar crescimento não-linear"
    )


# ---------------------------------------------------------------------------
# Correção rodada 3 (fase 3A) -- item 1: fan-out durável e retomável.
# ---------------------------------------------------------------------------


def test_fan_out_survives_crash_and_resumes_without_recalling_the_provider(
    integration_database,
) -> None:
    """A+B+C compartilham a coleta. Persistência comercial termina. A é
    processada. Simula-se um crash antes de B/C (nunca chamados).
    Retomar processa só B/C -- provider total = 1, persistência comercial
    = 1, A não repete, checkpoints finais corretos para as 3."""
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="crash"
    )
    user_c = _seed_user(integration_database.sessions, "crash-C")
    mission_c = _make_mission(
        integration_database, user_c, search_query="5070 Ti", sources=("amazon",)
    )
    assert _monitoring_item_id_for(integration_database.sessions, mission_c.id) == item_id
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    all_mission_ids = {mission_a.id, mission_b.id, mission_c.id}

    # Fase "provider + persistência comercial" -- as mesmas peças internas
    # que `collect_monitoring_item_store` usa, chamadas direto aqui para
    # poder interromper o fan-out no meio (simular o crash).
    claim = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert claim is not None
    result = _run(
        _adapter(provider).collect(
            CollectionRequest(
                source_code=claim.store_code,
                search_query=claim.criteria.search_query,
                requested_at=NOW,
                monitoring_item_id=item_id,
            )
        )
    )
    normalized = PriceNormalizer().normalize_result(result)
    finished_at = normalized.raw_result.completed_at
    shared_results = _run(
        shared_collection_module._persist_shared_offers_and_finish(
            integration_database.async_sessions,
            run_id=claim.run_id,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            normalized=normalized,
            finished_at=finished_at,
        )
    )

    with integration_database.sessions() as session:
        shared_run = session.get(CollectionRun, claim.run_id)
        assert shared_run.status is CollectionRunStatus.SUCCEEDED  # já durável

    # "A é processada" -- processa só 1 tarefa pendente (limit=1).
    outcome_first = _run(
        shared_collection_module._process_pending_fan_out(
            integration_database.async_sessions,
            claim.store_code,
            ai_manager,
            UserRole.ADMIN,
            run_id=claim.run_id,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            shared_results=shared_results,
            finished_at=finished_at,
            limit=1,
        )
    )
    assert len(outcome_first.done) == 1
    assert outcome_first.terminally_failed == ()
    processed_first = outcome_first.done[0]

    # "Simular crash antes de B/C" -- literalmente para aqui; nenhuma
    # chamada a mais acontece. As outras 2 tarefas continuam `pending` no
    # banco, exatamente como ficariam depois de um crash real.
    with integration_database.sessions() as session:
        tasks = list(
            session.scalars(
                select(SharedFanOutTask).where(
                    SharedFanOutTask.collection_run_id == claim.run_id
                )
            )
        )
    assert len(tasks) == 3
    pending_ids = {
        t.mission_id for t in tasks if t.status == SharedFanOutStatus.PENDING
    }
    assert pending_ids == all_mission_ids - {processed_first}

    # "Retomar" -- não passa `provider`/persistência nenhuma: nunca pode
    # rechamar o provider nem repetir a persistência comercial.
    resume_result = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
        )
    )

    assert len(provider.calls) == 1  # nunca rechamado durante a retomada
    assert _offer_count(integration_database.sessions) == 1
    assert _observation_count(integration_database.sessions) == 1
    assert set(resume_result.fanned_out_mission_ids) == all_mission_ids - {processed_first}
    assert processed_first not in resume_result.fanned_out_mission_ids  # A não repete

    # checkpoints finais corretos -- as 3 têm relevância persistida, cada
    # uma com exatamente 1 CollectionRun própria (nunca 2 para a mesma).
    with integration_database.sessions() as session:
        all_tasks = list(
            session.scalars(
                select(SharedFanOutTask).where(
                    SharedFanOutTask.collection_run_id == claim.run_id
                )
            )
        )
        assert {t.status for t in all_tasks} == {SharedFanOutStatus.DONE}
        for mission_id in all_mission_ids:
            own_runs = list(
                session.scalars(
                    select(CollectionRun).where(CollectionRun.mission_id == mission_id)
                )
            )
            assert len(own_runs) == 1
            assert own_runs[0].status is CollectionRunStatus.SUCCEEDED
    for mission_id in all_mission_ids:
        assert len(_relevance_rows(integration_database.sessions, mission_id)) == 1


# ---------------------------------------------------------------------------
# Correção rodada 3 -- item 3: crash durante o claim/shared run em si
# (antes de chegar a SUCCEEDED) -- a UNIQUE parcial de RUNNING não pode
# travar (monitoring_item_id, store_id) para sempre.
# ---------------------------------------------------------------------------


def test_stale_shared_run_is_recovered_and_slot_becomes_eligible_again(
    integration_database,
) -> None:
    _mission_a, _mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="stale"
    )
    amazon_id = _amazon_store_id(integration_database)

    # Claim "abandonado" -- reserva a execução (mesmo mecanismo real) e
    # nunca termina (simula o processo morrendo entre o claim e o fim da
    # coleta comercial).
    claim = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert claim is not None
    with integration_database.sessions() as session:
        abandoned = session.get(CollectionRun, claim.run_id)
        assert abandoned.status is CollectionRunStatus.RUNNING
        assert abandoned.monitoring_item_id == item_id

    # Uma segunda tentativa, ainda dentro da janela de stale, encontra o
    # slot ocupado -- não executa duas simultaneamente.
    second_attempt = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW + timedelta(minutes=5),
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert second_attempt is None

    # "Simular expiração/stale" -- mesmo mecanismo já existente e
    # genérico (nunca precisou de mudança: não filtra por mission_id,
    # já reconhecia runs compartilhadas desde que a coluna existe).
    async def _recover():
        async with integration_database.async_sessions.begin() as session:
            return await recover_stale_runs(
                session,
                now=NOW + timedelta(minutes=20),
                stale_after=timedelta(minutes=10),
            )

    recovered = _run(_recover())
    assert recovered == 1
    with integration_database.sessions() as session:
        recovered_run = session.get(CollectionRun, claim.run_id)
        assert recovered_run.status is CollectionRunStatus.FAILED  # nunca preso em RUNNING

    # "Combinação volta a ficar elegível" -- o slot nunca perde o
    # MonitoringItemStore permanentemente; a recuperação em si não
    # antecipa o próximo ciclo natural (mesmo comportamento já existente
    # para missão única: `recover_stale_runs` nunca mexe em agenda,
    # nenhuma política nova de "tentar de novo mais cedo" foi inventada)
    # -- assim que o `next_run_at` já avançado pelo claim original chega
    # (aqui, 60min depois do claim original em `NOW`), uma nova execução
    # pode ocorrer exatamente uma vez.
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW + timedelta(minutes=65),
        )
    )
    assert result.claimed is True
    assert result.succeeded is True
    assert len(provider.calls) == 1

    # nunca duas execuções simultâneas -- só 1 run SUCCEEDED nova, além da
    # antiga já FAILED.
    with integration_database.sessions() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(
                    CollectionRun.monitoring_item_id == item_id,
                    CollectionRun.store_id == amazon_id,
                )
            )
        )
    assert len(runs) == 2
    assert {r.status for r in runs} == {CollectionRunStatus.FAILED, CollectionRunStatus.SUCCEEDED}


# ---------------------------------------------------------------------------
# Correção rodada 3 -- item 2: backfill de `last_observation_id` para
# linhas pré-existentes de `mission_offer_relevance` (mesma lógica SQL da
# migration `20260825_0003`).
# ---------------------------------------------------------------------------

_BACKFILL_SQL = """
    UPDATE mission_offer_relevance AS mor
    SET last_observation_id = latest.observation_id
    FROM (
        SELECT DISTINCT ON (cr.mission_id, po.offer_id)
            cr.mission_id AS mission_id,
            po.offer_id AS offer_id,
            po.id AS observation_id
        FROM price_observations AS po
        JOIN collection_runs AS cr ON cr.id = po.collection_run_id
        WHERE cr.mission_id IS NOT NULL
        ORDER BY cr.mission_id, po.offer_id, po.observed_at DESC, po.id DESC
    ) AS latest
    WHERE mor.mission_id = latest.mission_id
      AND mor.offer_id = latest.offer_id
      AND mor.last_observation_id IS NULL
"""


def test_last_observation_id_backfill_prevents_duplicate_alert_on_first_new_cycle(
    integration_database,
) -> None:
    """Item 2 da correção: simula estado antigo representativo -- Mission
    já tinha cruzado o alvo antes (via o mecanismo ANTIGO de "previous",
    `CollectionRun.mission_id`) e sua `MissionOfferRelevance` ficou com
    `last_observation_id=NULL` (como qualquer linha criada antes desta
    coluna existir). Aplica a MESMA lógica SQL do backfill da migration
    `20260825_0003` e confirma que o primeiro ciclo novo pelo caminho
    compartilhado NÃO redispara `PRICE_TARGET_REACHED` -- o checkpoint
    não foi perdido."""
    from decimal import Decimal

    from app.collection.models import PriceObservation
    from app.collection.normalization import Availability
    from app.products.identity import resolve_product_variant
    from app.products.models import Product

    user_a = _seed_user(integration_database.sessions, "backfill-A")
    mission_a = _make_mission(
        integration_database,
        user_a,
        search_query="RTX 5070 Ti",
        sources=("amazon",),
        target_amount=Decimal("4500.00"),  # alvo já cruzado no passado
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id is not None
    amazon_id = _amazon_store_id(integration_database)

    # Estado "antigo" (pré-migration): Offer/Product com identidade REAL
    # (mesmo `resolve_product_variant` que a coleta de verdade usa --
    # precisa bater com `criteria.requested_identity_key` da própria
    # Mission para a relevância determinística reconhecer MATCH), mesmo
    # `external_id`/loja que o provider fake do próximo ciclo vai
    # devolver (para `_resolve_offer` reaproveitar a MESMA Offer), uma
    # PriceObservation JÁ abaixo do alvo presa a uma CollectionRun DESTA
    # Mission (mecanismo antigo de "previous"), e a classificação já
    # existente com `last_observation_id=NULL`.
    resolved = resolve_product_variant("RTX 5070 Ti")
    assert resolved is not None
    with integration_database.sessions.begin() as session:
        product = Product(
            name=resolved.label,
            brand=resolved.brand,
            model=resolved.model,
            display_name=resolved.label,
            category=resolved.category,
            family=resolved.family,
            variant=resolved.variant,
            attributes=dict(resolved.attributes),
            family_key=resolved.family_key,
            identity_key=resolved.identity_key,
            identity_version=1,
        )
        session.add(product)
        session.flush()
        offer = Offer(
            product_id=product.id,
            store_id=amazon_id,
            external_id="shared-gpu-stable",  # mesmo external_id de _CountingGpuProvider
            url="https://example.invalid/shared-gpu",
        )
        session.add(offer)
        session.flush()
        old_run = CollectionRun(
            mission_id=mission_a.id,
            store_id=amazon_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=NOW - timedelta(days=1),
            finished_at=NOW - timedelta(days=1),
        )
        session.add(old_run)
        session.flush()
        old_observation = PriceObservation(
            offer_id=offer.id,
            collection_run_id=old_run.id,
            amount=Decimal("4000.00"),  # já abaixo do alvo (4500) -- já tinha alertado
            currency="BRL",
            total_amount=Decimal("4000.00"),
            availability=Availability.AVAILABLE,
            observed_at=NOW - timedelta(days=1),
        )
        session.add(old_observation)
        session.flush()
        session.add(
            MissionOfferRelevance(
                mission_id=mission_a.id,
                offer_id=offer.id,
                classification="match",
                classified_at=NOW - timedelta(days=1),
                last_observation_id=None,  # estado pré-migration
            )
        )

    # Aplica a MESMA lógica SQL do backfill da migration 20260825_0003.
    with integration_database.sessions.begin() as session:
        session.execute(sa_text(_BACKFILL_SQL))

    with integration_database.sessions() as session:
        relevance = session.get(MissionOfferRelevance, (mission_a.id, offer.id))
        assert relevance.last_observation_id == old_observation.id  # backfill funcionou

    # Primeiro ciclo novo, caminho compartilhado -- mesmo preço (abaixo do
    # alvo, já "visto" antes pelo backfill) via o provider fake padrão.
    provider = _CountingGpuProvider(raw_price="3999.90")
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
        )
    )
    assert mission_a.id in result.fanned_out_mission_ids
    # sanity check -- a relevância PRECISA ter resolvido MATCH aqui, senão
    # o teste passaria trivialmente por a avaliação de alerta nunca ter
    # sido alcançada, e não por causa do backfill.
    rows_a = _relevance_rows(integration_database.sessions, mission_a.id)
    assert len(rows_a) == 1
    assert rows_a[0].classification.value == "match"

    with integration_database.sessions() as session:
        target_events = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_a.id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )
        )
    assert target_events == []  # checkpoint preservado -- nenhum alerta duplicado


# ---------------------------------------------------------------------------
# Correção rodada 4 -- item 2: duas workers retomando fan-out ao mesmo
# tempo nunca processam a mesma (collection_run_id, mission_id).
# ---------------------------------------------------------------------------


def test_two_workers_resuming_concurrently_never_process_the_same_task_twice(
    integration_database,
) -> None:
    """A+B+C pendentes; duas workers retomam simultaneamente -- cada
    Mission processada exatamente uma vez (soma das duas = 3), nenhuma
    tarefa executada em paralelo pelas duas."""
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="concurrent-resume"
    )
    user_c = _seed_user(integration_database.sessions, "concurrent-resume-C")
    mission_c = _make_mission(
        integration_database, user_c, search_query="5070 Ti", sources=("amazon",)
    )
    assert _monitoring_item_id_for(integration_database.sessions, mission_c.id) == item_id
    amazon_id = _amazon_store_id(integration_database)
    all_mission_ids = {mission_a.id, mission_b.id, mission_c.id}
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    # Deixa as 3 tarefas `pending` sem processar nenhuma -- só a
    # persistência comercial (mesmas peças internas de
    # `collect_monitoring_item_store`, chamadas direto para parar antes
    # do fan-out).
    claim = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert claim is not None
    result = _run(
        _adapter(provider).collect(
            CollectionRequest(
                source_code=claim.store_code,
                search_query=claim.criteria.search_query,
                requested_at=NOW,
                monitoring_item_id=item_id,
            )
        )
    )
    normalized = PriceNormalizer().normalize_result(result)
    finished_at = normalized.raw_result.completed_at
    _run(
        shared_collection_module._persist_shared_offers_and_finish(
            integration_database.async_sessions,
            run_id=claim.run_id,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            normalized=normalized,
            finished_at=finished_at,
        )
    )
    with integration_database.sessions() as session:
        tasks = list(
            session.scalars(
                select(SharedFanOutTask).where(
                    SharedFanOutTask.collection_run_id == claim.run_id
                )
            )
        )
    assert len(tasks) == 3
    assert {t.status for t in tasks} == {SharedFanOutStatus.PENDING}

    barrier = Barrier(2)

    def _resume_worker(_index: int):
        async def run():
            engine = create_collection_async_database_engine(integration_database.settings)
            sessions = create_async_session_factory(engine)
            worker_ai_manager = _KeywordAwareAIManager(match_keyword=None)
            try:
                barrier.wait(timeout=10)
                return await resume_shared_collection_fan_out(
                    sessions,
                    worker_ai_manager,
                    monitoring_item_id=item_id,
                    store_id=amazon_id,
                    now=finished_at,
                )
            finally:
                await engine.dispose()

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(_resume_worker, range(2)))

    done_by_worker = [set(r.fanned_out_mission_ids) for r in results]
    combined_done = done_by_worker[0] | done_by_worker[1]
    overlap = done_by_worker[0] & done_by_worker[1]

    assert overlap == set()  # nenhuma Mission processada pelas duas workers
    assert combined_done == all_mission_ids  # as 3 foram concluídas, juntando as duas

    # checkpoints finais corretos -- cada Mission com exatamente 1 linha
    # de relevância e exatamente 1 CollectionRun própria (nunca 2, nunca
    # processada em paralelo pelas duas workers).
    with integration_database.sessions() as session:
        all_tasks = list(
            session.scalars(
                select(SharedFanOutTask).where(
                    SharedFanOutTask.collection_run_id == claim.run_id
                )
            )
        )
        assert {t.status for t in all_tasks} == {SharedFanOutStatus.DONE}
        for mission_id in all_mission_ids:
            own_runs = list(
                session.scalars(
                    select(CollectionRun).where(CollectionRun.mission_id == mission_id)
                )
            )
            assert len(own_runs) == 1
    for mission_id in all_mission_ids:
        assert len(_relevance_rows(integration_database.sessions, mission_id)) == 1


# ---------------------------------------------------------------------------
# Correção rodada 4 -- item 3: crash NO MEIO do processamento de uma
# Mission (efeito já persistido, tarefa ainda não virou done) -- o retry
# não pode duplicar nenhum efeito individual.
# ---------------------------------------------------------------------------


def test_crash_mid_mission_processing_never_duplicates_individual_effects(
    integration_database,
) -> None:
    """B começa, `_persist_phase_c` já commitou (MissionOfferRelevance +
    PRICE_TARGET_REACHED já persistidos/publicados), processo 'morre'
    antes de marcar a tarefa `done`. Retry: B termina, nenhum efeito
    duplicado -- prova a idempotência real via `MissionOfferRelevance.
    last_observation_id` (não só "provavelmente dedupe")."""
    from decimal import Decimal

    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database,
        label="crash-mid",
        target_a=None,
        target_b=Decimal("4500.00"),  # B tem alvo -- efeito checável (alerta)
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider(raw_price="3999.90")
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    claim = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert claim is not None
    result = _run(
        _adapter(provider).collect(
            CollectionRequest(
                source_code=claim.store_code,
                search_query=claim.criteria.search_query,
                requested_at=NOW,
                monitoring_item_id=item_id,
            )
        )
    )
    normalized = PriceNormalizer().normalize_result(result)
    finished_at = normalized.raw_result.completed_at
    shared_results = _run(
        shared_collection_module._persist_shared_offers_and_finish(
            integration_database.async_sessions,
            run_id=claim.run_id,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            normalized=normalized,
            finished_at=finished_at,
        )
    )

    # Processa B manualmente até _persist_phase_c COMMITAR (efeitos reais
    # persistidos) -- mas NUNCA chama _complete_fan_out_task. Isso é
    # exatamente "crash depois de pelo menos um efeito persistido, antes
    # de marcar task done".
    claimed = _run(
        shared_collection_module._claim_fan_out_task(
            integration_database.async_sessions,
            run_id=claim.run_id,
            mission_id=mission_b.id,
            now=finished_at,
        )
    )
    assert claimed is True
    mission_run_id = _run(
        shared_collection_module._start_mission_fan_out_run(
            integration_database.async_sessions,
            mission_id=mission_b.id,
            store_id=amazon_id,
            started_at=finished_at,
        )
    )
    assert mission_run_id is not None
    phase_a = _run(
        shared_collection_module._build_mission_phase_a_outcome(
            integration_database.async_sessions,
            run_id=mission_run_id,
            mission_id=mission_b.id,
            store_id=amazon_id,
            shared_results=shared_results,
            completed_at=finished_at,
        )
    )
    assert phase_a is not None
    ai_outcomes = _run(
        shared_collection_module._run_phase_b(phase_a, ai_manager, UserRole.ADMIN)
    )
    ok = _run(shared_collection_module._persist_phase_c(integration_database.async_sessions, phase_a, ai_outcomes))
    assert ok is True  # efeitos REALMENTE commitados

    # "Simular crash" -- para aqui. Nunca chama _complete_fan_out_task.
    with integration_database.sessions() as session:
        task_b = session.get(SharedFanOutTask, (claim.run_id, mission_b.id))
        assert task_b.status == SharedFanOutStatus.PROCESSING  # ainda não done

    rows_b_before = _relevance_rows(integration_database.sessions, mission_b.id)
    assert len(rows_b_before) == 1
    with integration_database.sessions() as session:
        events_before = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_b.id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )
        )
    assert len(events_before) == 1  # o alerta JÁ disparou antes do "crash"

    # Recupera a tarefa travada (lease expirado) e reprocessa.
    async def _recover():
        async with integration_database.async_sessions.begin() as session:
            return await recover_stale_fan_out_tasks(
                session, now=finished_at + timedelta(minutes=20), stale_after=timedelta(minutes=10)
            )

    recovered = _run(_recover())
    assert recovered == 1

    resume_result = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at + timedelta(minutes=20),
        )
    )
    assert mission_b.id in resume_result.fanned_out_mission_ids

    with integration_database.sessions() as session:
        task_b = session.get(SharedFanOutTask, (claim.run_id, mission_b.id))
        assert task_b.status == SharedFanOutStatus.DONE

    # Nenhum efeito duplicado -- checkpoint continua 1 linha, alerta
    # continua 1 evento (não 2), mesmo depois do retry ter rodado o
    # pipeline inteiro de novo.
    rows_b_after = _relevance_rows(integration_database.sessions, mission_b.id)
    assert len(rows_b_after) == 1
    # mesma linha (chave natural mission_id+offer_id, sem id próprio) --
    # `created_at` imutável prova que nunca foi apagada/recriada.
    assert rows_b_after[0].created_at == rows_b_before[0].created_at
    with integration_database.sessions() as session:
        events_after = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_b.id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )
        )
    assert len(events_after) == 1  # nunca 2 -- nenhum alerta duplicado


# ---------------------------------------------------------------------------
# Correção rodada 4 -- item 4: SharedFanOutTask presa em `processing`
# (lease expirado) é recuperável, sem intervenção manual.
# ---------------------------------------------------------------------------


def test_stale_processing_fan_out_task_is_recovered_and_processed_once(
    integration_database,
) -> None:
    mission_a, _mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="stale-processing"
    )
    amazon_id = _amazon_store_id(integration_database)
    provider = _CountingGpuProvider()
    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    claim = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert claim is not None
    result = _run(
        _adapter(provider).collect(
            CollectionRequest(
                source_code=claim.store_code,
                search_query=claim.criteria.search_query,
                requested_at=NOW,
                monitoring_item_id=item_id,
            )
        )
    )
    normalized = PriceNormalizer().normalize_result(result)
    finished_at = normalized.raw_result.completed_at
    _run(
        shared_collection_module._persist_shared_offers_and_finish(
            integration_database.async_sessions,
            run_id=claim.run_id,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            normalized=normalized,
            finished_at=finished_at,
        )
    )

    # Claim da tarefa (processing) -- e "crash" imediatamente depois,
    # antes de qualquer efeito (nem `_start_mission_fan_out_run` chega a
    # rodar). Cenário mais simples de lease abandonado.
    claimed = _run(
        shared_collection_module._claim_fan_out_task(
            integration_database.async_sessions,
            run_id=claim.run_id,
            mission_id=mission_a.id,
            now=finished_at,
        )
    )
    assert claimed is True
    with integration_database.sessions() as session:
        task = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task.status == SharedFanOutStatus.PROCESSING

    # Ainda dentro da janela de lease -- retomar não enxerga a tarefa
    # (continua `processing`, nunca `pending`), nada é processado.
    early_resume = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at + timedelta(minutes=2),
        )
    )
    assert mission_a.id not in early_resume.fanned_out_mission_ids
    with integration_database.sessions() as session:
        task = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task.status == SharedFanOutStatus.PROCESSING  # continua travada

    # "Expira" -- recovery automático, sem admin manual.
    async def _recover():
        async with integration_database.async_sessions.begin() as session:
            return await recover_stale_fan_out_tasks(
                session,
                now=finished_at + timedelta(minutes=20),
                stale_after=timedelta(minutes=10),
            )

    recovered = _run(_recover())
    assert recovered == 1
    with integration_database.sessions() as session:
        task = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task.status == SharedFanOutStatus.PENDING  # volta a ficar elegível

    # Processa uma única vez.
    final_resume = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at + timedelta(minutes=21),
        )
    )
    assert mission_a.id in final_resume.fanned_out_mission_ids
    with integration_database.sessions() as session:
        task = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task.status == SharedFanOutStatus.DONE
    assert len(_relevance_rows(integration_database.sessions, mission_a.id)) == 1
    with integration_database.sessions() as session:
        own_runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_a.id)
            )
        )
    assert len(own_runs) == 1  # processada de fato só uma vez


# ---------------------------------------------------------------------------
# Correção rodada 4 -- item 2: revalidação de elegibilidade antes do fan-out
# ---------------------------------------------------------------------------


def _create_shared_pending_tasks(integration_database, *, item_id, store_id, now=NOW):
    """Executa só a fase 'provider + persistência comercial' (idêntica à
    usada internamente por `collect_monitoring_item_store`), deixando as
    `SharedFanOutTask` de cada Mission elegível como `pending`, sem
    processar nenhuma -- ponto de partida comum para simular "o mundo
    mudou entre a coleta comercial e o fan-out realmente rodar"."""
    provider = _CountingGpuProvider()
    claim = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=store_id,
            now=now,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert claim is not None
    result = _run(
        _adapter(provider).collect(
            CollectionRequest(
                source_code=claim.store_code,
                search_query=claim.criteria.search_query,
                requested_at=now,
                monitoring_item_id=item_id,
            )
        )
    )
    normalized = PriceNormalizer().normalize_result(result)
    finished_at = normalized.raw_result.completed_at
    shared_results = _run(
        shared_collection_module._persist_shared_offers_and_finish(
            integration_database.async_sessions,
            run_id=claim.run_id,
            monitoring_item_id=item_id,
            store_id=store_id,
            normalized=normalized,
            finished_at=finished_at,
        )
    )
    return claim, shared_results, finished_at


def test_pending_fan_out_skipped_when_mission_paused_before_processing(
    integration_database,
) -> None:
    """A coleta compartilhada termina, A e B ficam com `SharedFanOutTask`
    `pending`. Antes de qualquer uma ser processada, o usuário pausa A
    (ex.: crash real teria deixado a tarefa pending por um tempo, e nesse
    intervalo o usuário agiu). A retomada NUNCA trata isso como erro nem
    gera alerta para A -- marca `skipped`. B (continua ACTIVE, controle
    desta mesma correção) processa normalmente, prova que a revalidação é
    por Mission, nunca global."""
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="revalidate-paused"
    )
    amazon_id = _amazon_store_id(integration_database)
    claim, _shared_results, finished_at = _create_shared_pending_tasks(
        integration_database, item_id=item_id, store_id=amazon_id
    )

    _transition(
        integration_database,
        mission_id=mission_a.id,
        command=MissionCommand.PAUSE,
        expected_state_version=mission_a.state_version,
        actor_type="test",
        transitioned_at=finished_at,
    )

    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    resume_result = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at,
        )
    )

    assert mission_a.id in resume_result.fan_out_skipped_mission_ids
    assert mission_a.id not in resume_result.fanned_out_mission_ids
    assert mission_a.id not in resume_result.fan_out_failed_mission_ids
    assert mission_a.id not in resume_result.fan_out_attention_required_mission_ids
    assert mission_b.id in resume_result.fanned_out_mission_ids  # controle: B segue normal

    with integration_database.sessions() as session:
        task_a = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task_a.status == SharedFanOutStatus.SKIPPED
        own_run_a = session.scalar(
            select(CollectionRun).where(CollectionRun.mission_id == mission_a.id)
        )
        assert own_run_a is None  # zero efeito -- nem a CollectionRun própria chegou a existir
    assert _relevance_rows(integration_database.sessions, mission_a.id) == []
    with integration_database.sessions() as session:
        events_a = list(
            session.scalars(select(Event).where(Event.mission_id == mission_a.id))
        )
    assert events_a == []


def test_pending_fan_out_skipped_when_mission_cancelled_before_processing(
    integration_database,
) -> None:
    """Mesmo cenário do teste anterior, mas com CANCEL em vez de PAUSE --
    outro estado não-ACTIVE, mesma revalidação, mesmo resultado: `skipped`,
    zero efeito, B não afetado."""
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="revalidate-cancelled"
    )
    amazon_id = _amazon_store_id(integration_database)
    claim, _shared_results, finished_at = _create_shared_pending_tasks(
        integration_database, item_id=item_id, store_id=amazon_id
    )

    _transition(
        integration_database,
        mission_id=mission_a.id,
        command=MissionCommand.CANCEL,
        expected_state_version=mission_a.state_version,
        actor_type="test",
        transitioned_at=finished_at,
    )

    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    resume_result = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at,
        )
    )

    assert mission_a.id in resume_result.fan_out_skipped_mission_ids
    assert mission_b.id in resume_result.fanned_out_mission_ids  # controle: B segue normal
    with integration_database.sessions() as session:
        task_a = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task_a.status == SharedFanOutStatus.SKIPPED
    assert _relevance_rows(integration_database.sessions, mission_a.id) == []


def test_pending_fan_out_skipped_when_mission_relinked_to_another_monitoring_item(
    integration_database,
) -> None:
    """Mesmo cenário, mas simula um reconcile (fase 2, TASK-112) já ter
    religado A para OUTRO `MonitoringItem` entre a coleta compartilhada e
    o fan-out -- este fan-out antigo (calculado com o item ANTERIOR) não
    pode mais valer para A. `skipped`, zero efeito, B não afetado."""
    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="revalidate-relink"
    )
    amazon_id = _amazon_store_id(integration_database)
    claim, _shared_results, finished_at = _create_shared_pending_tasks(
        integration_database, item_id=item_id, store_id=amazon_id
    )

    user_other = _seed_user(integration_database.sessions, "revalidate-relink-other")
    mission_other = _make_mission(
        integration_database, user_other, search_query="RTX 4090", sources=("amazon",)
    )
    other_item_id = _monitoring_item_id_for(integration_database.sessions, mission_other.id)
    assert other_item_id is not None
    assert other_item_id != item_id

    with integration_database.sessions.begin() as session:
        link = session.get(MissionMonitoringItem, mission_a.id)
        link.monitoring_item_id = other_item_id

    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    resume_result = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at,
        )
    )

    assert mission_a.id in resume_result.fan_out_skipped_mission_ids
    assert mission_b.id in resume_result.fanned_out_mission_ids  # controle: B segue normal
    with integration_database.sessions() as session:
        task_a = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task_a.status == SharedFanOutStatus.SKIPPED
    assert _relevance_rows(integration_database.sessions, mission_a.id) == []


# ---------------------------------------------------------------------------
# Correção rodada 4 -- item 3: idempotência real do outbox de notificação
# ---------------------------------------------------------------------------


def test_notification_outbox_never_double_delivers_after_fan_out_crash_and_retry(
    integration_database,
) -> None:
    """Prova a idempotência REAL do caminho de notificação (não só que
    `MissionOfferRelevance`/`Event` não duplicam) -- usa o MESMO mecanismo
    de outbox que `app.telegram.notifications` usa em produção
    (`claim_unconsumed_events_async`/`record_consumption_attempt_async`,
    TASK-080), sem mock nenhum dele. Cenário: efeito persistido (Event de
    alerta) → 'crash' antes da SharedFanOutTask virar `done` → retomada.
    Resultado: 1 `Event`, 1 notificação reivindicável (nunca 2), e depois
    de consumida, zero reivindicáveis de novo."""
    from decimal import Decimal

    mission_a, mission_b, item_id = _two_missions_sharing_gpu(
        integration_database,
        label="outbox-idempotent",
        target_a=None,
        target_b=Decimal("4500.00"),  # B tem alvo -- efeito checável (alerta)
    )
    amazon_id = _amazon_store_id(integration_database)
    ai_manager = _KeywordAwareAIManager(match_keyword=None)
    provider = _CountingGpuProvider(raw_price="3999.90")

    claim = _run(
        shared_collection_module._claim_shared_collection(
            integration_database.async_sessions,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
    )
    assert claim is not None
    result = _run(
        _adapter(provider).collect(
            CollectionRequest(
                source_code=claim.store_code,
                search_query=claim.criteria.search_query,
                requested_at=NOW,
                monitoring_item_id=item_id,
            )
        )
    )
    normalized = PriceNormalizer().normalize_result(result)
    finished_at = normalized.raw_result.completed_at
    shared_results = _run(
        shared_collection_module._persist_shared_offers_and_finish(
            integration_database.async_sessions,
            run_id=claim.run_id,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            normalized=normalized,
            finished_at=finished_at,
        )
    )

    # Processa B manualmente até _persist_phase_c COMMITAR (Event do
    # alerta já persistido) -- mas NUNCA chama _complete_fan_out_task.
    # "Crash" depois de pelo menos um efeito persistido, antes de marcar
    # a tarefa done -- mesma técnica das rodadas 3/4 já provadas.
    claimed = _run(
        shared_collection_module._claim_fan_out_task(
            integration_database.async_sessions,
            run_id=claim.run_id,
            mission_id=mission_b.id,
            now=finished_at,
        )
    )
    assert claimed is True
    mission_run_id = _run(
        shared_collection_module._start_mission_fan_out_run(
            integration_database.async_sessions,
            mission_id=mission_b.id,
            store_id=amazon_id,
            started_at=finished_at,
        )
    )
    assert mission_run_id is not None
    phase_a = _run(
        shared_collection_module._build_mission_phase_a_outcome(
            integration_database.async_sessions,
            run_id=mission_run_id,
            mission_id=mission_b.id,
            store_id=amazon_id,
            shared_results=shared_results,
            completed_at=finished_at,
        )
    )
    assert phase_a is not None
    ai_outcomes = _run(
        shared_collection_module._run_phase_b(phase_a, ai_manager, UserRole.ADMIN)
    )
    ok = _run(shared_collection_module._persist_phase_c(
        integration_database.async_sessions, phase_a, ai_outcomes
    ))
    assert ok is True

    with integration_database.sessions() as session:
        events_before = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_b.id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )
        )
    assert len(events_before) == 1  # o alerta já disparou antes do "crash"
    event_id = events_before[0].id

    # Recupera a tarefa travada (lease expirado) e retoma -- roda o
    # pipeline inteiro de novo para B.
    async def _recover():
        async with integration_database.async_sessions.begin() as session:
            return await shared_collection_module.recover_stale_fan_out_tasks(
                session, now=finished_at + timedelta(minutes=20), stale_after=timedelta(minutes=10)
            )

    recovered = _run(_recover())
    assert recovered == 1
    resume_result = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at + timedelta(minutes=20),
        )
    )
    assert mission_b.id in resume_result.fanned_out_mission_ids

    with integration_database.sessions() as session:
        events_after = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_b.id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )
        )
    assert len(events_after) == 1  # nunca 2 -- mesmo Event, nunca recriado
    assert events_after[0].id == event_id

    # Prova via o mecanismo REAL de outbox (TASK-080, o mesmo usado pelo
    # dispatcher de Telegram em produção): exatamente 1 notificação
    # reivindicável para este evento -- nunca 2, mesmo depois do crash +
    # retry do fan-out inteiro.
    async def _claim_notifications():
        async with integration_database.async_sessions() as session:
            return await claim_unconsumed_events_async(
                session,
                consumer_name=TELEGRAM_NOTIFICATION_CONSUMER,
                event_types=(EventType.PRICE_TARGET_REACHED_V1.value,),
                now=finished_at + timedelta(minutes=21),
            )

    claimed_events = _run(_claim_notifications())
    claimed_for_b = [e for e in claimed_events if e.mission_id == mission_b.id]
    assert len(claimed_for_b) == 1
    assert claimed_for_b[0].id == event_id

    async def _consume():
        async with integration_database.async_sessions() as session, session.begin():
            event = await session.get(Event, event_id)
            await record_consumption_attempt_async(
                session,
                event=event,
                consumer_name=TELEGRAM_NOTIFICATION_CONSUMER,
                outcome=ConsumptionOutcome.SUCCEEDED,
                attempted_at=finished_at + timedelta(minutes=21),
            )

    _run(_consume())

    # Consumido -- nenhuma reivindicação futura enxerga este evento de
    # novo -- nunca um segundo envio lógico.
    reclaimed = _run(_claim_notifications())
    assert [e for e in reclaimed if e.mission_id == mission_b.id] == []


# ---------------------------------------------------------------------------
# Correção rodada 4 -- item 4: integridade das tabelas novas no banco
# ---------------------------------------------------------------------------


def test_shared_fan_out_task_unique_constraint_enforced_by_database(
    integration_database,
) -> None:
    """`SharedFanOutTask` tem PRIMARY KEY `(collection_run_id, mission_id)`
    -- confirma no banco (não só lendo o model) que uma segunda linha com a
    MESMA combinação é rejeitada."""
    mission_a, _mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="unique-fan-out-task"
    )
    amazon_id = _amazon_store_id(integration_database)
    claim, _shared_results, finished_at = _create_shared_pending_tasks(
        integration_database, item_id=item_id, store_id=amazon_id
    )

    with pytest.raises(IntegrityError):
        with integration_database.sessions.begin() as session:
            session.add(
                SharedFanOutTask(
                    collection_run_id=claim.run_id,
                    mission_id=mission_a.id,
                    status=SharedFanOutStatus.PENDING,
                )
            )


def test_shared_collection_offer_unique_constraint_enforced_by_database(
    integration_database,
) -> None:
    """`SharedCollectionOffer` tem PRIMARY KEY `(collection_run_id,
    offer_id)` -- confirma no banco que uma segunda linha com a MESMA
    combinação é rejeitada, mesmo com `observation_id` diferente."""
    _mission_a, _mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="unique-shared-offer"
    )
    amazon_id = _amazon_store_id(integration_database)
    claim, shared_results, _finished_at = _create_shared_pending_tasks(
        integration_database, item_id=item_id, store_id=amazon_id
    )
    assert len(shared_results) == 1
    existing = shared_results[0]

    with pytest.raises(IntegrityError):
        with integration_database.sessions.begin() as session:
            session.add(
                SharedCollectionOffer(
                    collection_run_id=claim.run_id,
                    offer_id=existing.offer_id,
                    observation_id=existing.observation_id,
                )
            )


# ---------------------------------------------------------------------------
# Correção rodada 4 -- item 5: recuperação automática de fan-out travado
# ---------------------------------------------------------------------------


def test_resume_shared_collection_fan_out_recovers_stale_tasks_automatically(
    integration_database,
) -> None:
    """Igual ao cenário de `test_stale_processing_fan_out_task_is_
    recovered_and_processed_once`, mas SEM nenhuma chamada separada a
    `recover_stale_fan_out_tasks` -- só `resume_shared_collection_fan_out`,
    do jeito que um chamador real chamaria. Prova que a recuperação
    automática (item 5) realmente dispara sozinha, sem intervenção manual
    nem uma segunda chamada explícita."""
    mission_a, _mission_b, item_id = _two_missions_sharing_gpu(
        integration_database, label="auto-recover"
    )
    amazon_id = _amazon_store_id(integration_database)
    claim, _shared_results, finished_at = _create_shared_pending_tasks(
        integration_database, item_id=item_id, store_id=amazon_id
    )

    # Claim da tarefa (processing) -- e "crash" imediatamente depois.
    claimed = _run(
        shared_collection_module._claim_fan_out_task(
            integration_database.async_sessions,
            run_id=claim.run_id,
            mission_id=mission_a.id,
            now=finished_at,
        )
    )
    assert claimed is True
    with integration_database.sessions() as session:
        task = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task.status == SharedFanOutStatus.PROCESSING

    ai_manager = _KeywordAwareAIManager(match_keyword=None)

    # Nenhuma chamada a `recover_stale_fan_out_tasks` aqui -- só
    # `resume_shared_collection_fan_out`, bem depois do lease expirar.
    resume_result = _run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai_manager,
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=finished_at + timedelta(minutes=20),
        )
    )

    assert mission_a.id in resume_result.fanned_out_mission_ids
    with integration_database.sessions() as session:
        task = session.get(SharedFanOutTask, (claim.run_id, mission_a.id))
        assert task.status == SharedFanOutStatus.DONE
    assert len(_relevance_rows(integration_database.sessions, mission_a.id)) == 1
