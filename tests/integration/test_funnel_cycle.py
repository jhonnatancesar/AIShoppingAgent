"""TASK-136 (passo 3): barreira por ciclo e funil antes da IA, contra PostgreSQL real.

Uma missão com DUAS lojas: a IA só roda depois que as duas coletaram (ou uma
coletou e a outra falhou), e só nas ofertas que o funil escolhe (as 2 mais
baratas entre as mais populares), nunca em todas.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import CadenceConfig
from app.collection.contracts import CollectionResult, RawCollectedOffer
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    SharedFanOutStatus,
    SharedFanOutTask,
)
from app.collection.shared_collection import (
    collect_monitoring_item_store,
    resume_shared_collection_fan_out,
)
from app.missions.models import MissionMonitoringItem, MonitoringItem
from app.missions.service import create_mission_from_criteria_async
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_CADENCE = CadenceConfig(normal_min_minutes=60, normal_max_minutes=60)

# (id, preço, nota, avaliações): 4 ofertas por loja.
_AMAZON = (
    ("a1", "4000.00", "4.9", "900"),
    ("a2", "3900.00", "4.8", "700"),
    ("a3", "3500.00", "5.0", "2"),
    ("a4", "4500.00", "4.5", "100"),
)
_KABUM = (
    ("k1", "4100.00", "4.9", "800"),
    ("k2", "3800.00", "4.7", "600"),
    ("k3", "3000.00", None, None),
    ("k4", "4300.00", "4.0", "50"),
)


class _TwoStoreProvider:
    def __init__(self, source_code: str, rows, *, fail: bool = False) -> None:
        self.source_code = source_code
        self._rows = rows
        self._fail = fail

    async def collect(self, request):
        if self._fail:
            raise RuntimeError("loja indisponivel")
        completed = request.requested_at + timedelta(seconds=1)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            tuple(
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/{external_id}",
                    title=f"NVIDIA GeForce RTX 5070 Ti {external_id}",
                    collected_at=completed,
                    external_id=external_id,
                    raw_price=price,
                    raw_currency="BRL",
                    raw_availability="Em estoque",
                    raw_rating_average=rating,
                    raw_review_count=reviews,
                )
                for external_id, price, rating, reviews in self._rows
            ),
        )


class _CountingAI:
    def __init__(self) -> None:
        self.purposes: list[str] = []

    async def generate(self, request):
        self.purposes.append(request.purpose)
        body = (
            json.dumps({"relevance": "match"})
            if request.purpose == "classify_offer_relevance"
            else json.dumps({"display_title": "Produto"})
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=body,
            finished_at=datetime.now(UTC),
        )


def _setup(integration_database):
    with integration_database.sessions.begin() as session:
        user = User(display_name="TASK-136 ciclo", role=UserRole.USER)
        session.add(user)
        session.flush()
        user_id = user.id

    async def create():
        async with integration_database.async_sessions.begin() as session:
            return await create_mission_from_criteria_async(
                session,
                user_id=user_id,
                search_query="RTX 5070 Ti",
                target_amount=None,
                target_currency=None,
                source_codes=("amazon", "kabum"),
                requested_at=NOW,
                actor_type="test",
            )

    mission, _ = asyncio.run(create())
    with integration_database.sessions() as session:
        item_id = session.get(MissionMonitoringItem, mission.id).monitoring_item_id
        stores = {
            code: session.scalar(select(Store.id).where(Store.code == code))
            for code in ("amazon", "kabum")
        }
    return mission, item_id, stores


def _collect(integration_database, provider, item_id, store_id, ai, now):
    return asyncio.run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            CollectionAdapter(providers=(provider,)),
            ai,
            monitoring_item_id=item_id,
            store_id=store_id,
            now=now,
            cadence_config=_CADENCE,
        )
    )


def _relevance_count(integration_database, mission_id: UUID) -> int:
    with integration_database.sessions() as session:
        return len(
            list(
                session.scalars(
                    select(MissionOfferRelevance).where(
                        MissionOfferRelevance.mission_id == mission_id
                    )
                )
            )
        )


def _task_statuses(integration_database) -> list[SharedFanOutStatus]:
    with integration_database.sessions() as session:
        return [task.status for task in session.scalars(select(SharedFanOutTask))]


def test_first_store_waits_and_last_store_releases_ai_only_for_funnel_choice(
    integration_database,
) -> None:
    mission, item_id, stores = _setup(integration_database)
    ai = _CountingAI()

    first = _collect(
        integration_database,
        _TwoStoreProvider("amazon", _AMAZON),
        item_id,
        stores["amazon"],
        ai,
        NOW,
    )
    assert first.succeeded is True
    # Barreira: a Amazon gravou, mas ninguém chamou IA nem classificou nada.
    assert first.fanned_out_mission_ids == ()
    assert ai.purposes == []
    assert _relevance_count(integration_database, mission.id) == 0
    assert _task_statuses(integration_database) == [SharedFanOutStatus.PENDING]

    second = _collect(
        integration_database,
        _TwoStoreProvider("kabum", _KABUM),
        item_id,
        stores["kabum"],
        ai,
        NOW + timedelta(seconds=5),
    )
    assert second.succeeded is True
    # Ciclo fechou: as duas coletas foram processadas.
    assert len(second.fanned_out_mission_ids) == 2
    assert set(_task_statuses(integration_database)) == {SharedFanOutStatus.DONE}
    # 8 ofertas registradas, mas a IA classificou só as escolhidas pelo funil
    # (as 2 mais baratas do conjunto mais popular), nunca as 8.
    classify_calls = [p for p in ai.purposes if p == "classify_offer_relevance"]
    assert 1 <= len(classify_calls) <= 2
    assert _relevance_count(integration_database, mission.id) == len(classify_calls)


def test_failed_store_still_closes_the_cycle(integration_database) -> None:
    mission, item_id, stores = _setup(integration_database)
    ai = _CountingAI()

    _collect(
        integration_database,
        _TwoStoreProvider("amazon", _AMAZON),
        item_id,
        stores["amazon"],
        ai,
        NOW,
    )
    failed = _collect(
        integration_database,
        _TwoStoreProvider("kabum", _KABUM, fail=True),
        item_id,
        stores["kabum"],
        ai,
        NOW + timedelta(seconds=5),
    )
    assert failed.succeeded is False
    # A loja que falhou conta como rodada: o ciclo fecha e a Amazon é processada.
    assert len(failed.fanned_out_mission_ids) == 1
    assert set(_task_statuses(integration_database)) == {SharedFanOutStatus.DONE}
    with integration_database.sessions() as session:
        item = session.get(MonitoringItem, item_id)
        assert item.funnel_closed_at is not None
        statuses = {run.status for run in session.scalars(select(CollectionRun))}
    assert statuses == {CollectionRunStatus.SUCCEEDED, CollectionRunStatus.FAILED}
    assert 1 <= len([p for p in ai.purposes if p == "classify_offer_relevance"]) <= 2


def test_sweep_closes_cycle_left_open_by_a_crash(integration_database) -> None:
    mission, item_id, stores = _setup(integration_database)
    ai = _CountingAI()
    _collect(
        integration_database,
        _TwoStoreProvider("amazon", _AMAZON),
        item_id,
        stores["amazon"],
        ai,
        NOW,
    )
    # Simula o crash: a Kabum gravou (run SUCCEEDED + tarefa pendente) mas o
    # processo morreu antes de fechar o ciclo. A retomada fecha e processa.
    from app.collection import shared_collection as module

    original = module._fan_out_item_when_cycle_closed

    async def crash(*args, **kwargs):
        raise RuntimeError("processo caiu")

    module._fan_out_item_when_cycle_closed = crash
    try:
        with pytest.raises(RuntimeError):
            _collect(
                integration_database,
                _TwoStoreProvider("kabum", _KABUM),
                item_id,
                stores["kabum"],
                ai,
                NOW + timedelta(seconds=5),
            )
    finally:
        module._fan_out_item_when_cycle_closed = original
    assert ai.purposes == []
    assert set(_task_statuses(integration_database)) == {SharedFanOutStatus.PENDING}

    resumed = asyncio.run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai,
            monitoring_item_id=item_id,
            store_id=stores["amazon"],
            now=NOW + timedelta(minutes=1),
        )
    )
    assert len(resumed.fanned_out_mission_ids) == 1
    resumed_kabum = asyncio.run(
        resume_shared_collection_fan_out(
            integration_database.async_sessions,
            ai,
            monitoring_item_id=item_id,
            store_id=stores["kabum"],
            now=NOW + timedelta(minutes=1),
        )
    )
    assert len(resumed_kabum.fanned_out_mission_ids) == 1
    assert set(_task_statuses(integration_database)) == {SharedFanOutStatus.DONE}
    assert 1 <= len([p for p in ai.purposes if p == "classify_offer_relevance"]) <= 2


def test_mission_groups_highlight_relevant_and_others(integration_database) -> None:
    from app.offers.query import list_mission_offer_groups

    mission, item_id, stores = _setup(integration_database)
    ai = _CountingAI()
    _collect(
        integration_database,
        _TwoStoreProvider("amazon", _AMAZON),
        item_id,
        stores["amazon"],
        ai,
        NOW,
    )
    _collect(
        integration_database,
        _TwoStoreProvider("kabum", _KABUM),
        item_id,
        stores["kabum"],
        ai,
        NOW + timedelta(seconds=5),
    )

    async def groups(user_id):
        async with integration_database.async_sessions() as session:
            return await list_mission_offer_groups(
                session, mission_id=mission.id, user_id=user_id
            )

    with integration_database.sessions() as session:
        from app.missions.models import Mission

        owner_id = session.get(Mission, mission.id).user_id
    result = asyncio.run(groups(owner_id))

    # Destaque = as escolhidas pelo funil (todas classificadas "combina" aqui).
    assert 1 <= len(result.highlights) <= 2
    highlight_ids = {link.offer.id for link in result.highlights}
    # O Destaque não se repete em "relevantes".
    assert not highlight_ids & {link.offer.id for link in result.relevant}
    # Os outros 6 anúncios continuam gravados, sem classificação, do mais barato
    # para o mais caro, e nunca repetem o que já está em cima.
    assert len(result.others) == 8 - len(result.highlights)
    assert not highlight_ids & {link.offer.id for link in result.others}
    amounts = [link.amount for link in result.others]
    assert amounts == sorted(amounts)
    # Outro usuário não vê nada.
    from uuid import uuid4

    empty = asyncio.run(groups(uuid4()))
    assert (empty.highlights, empty.relevant, empty.others) == ((), (), ())
