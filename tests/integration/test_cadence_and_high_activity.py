"""Política de cadência (NORMAL/PROMO_CALENDAR/HIGH_ACTIVITY) -- TASK-112,
fase 3B.

Prova, contra PostgreSQL real: NORMAL nunca agenda abaixo do piso
configurado; PROMO_CALENDAR nunca agenda abaixo de 30min; backoff por
bloqueio confirmado sempre vence sobre promoção; atividade alta é
detectada a partir de `PriceObservation` já persistida (sinal durável,
sem schema novo para a contagem em si); atividade alta expira e volta a
NORMAL depois da duração configurada.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import CadenceConfig, resolve_collection_cadence
from app.collection.contracts import CollectionRequest, CollectionResult, RawCollectedOffer
from app.collection.models import PromotionalWindow, StoreActivityState
from app.collection.orchestration import claim_due_collections
from app.collection.shared_claim import _apply_shared_backoff
from app.collection.shared_collection import collect_monitoring_item_store
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionMonitoringItem,
    MissionSchedule,
    MissionSource,
    MissionStatus,
    MonitoringItemStore,
)
from app.missions.service import create_mission_from_criteria_async
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


class _StubAIManager:
    async def generate(self, request):
        from app.ai_provider import AIResponse

        content = (
            '{"relevance": "match"}'
            if request.purpose == "classify_offer_relevance"
            else '{"display_title": "Produto"}'
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=datetime.now(UTC),
        )


class _VaryingPriceProvider:
    """Preço muda a cada chamada -- cada uma gera uma nova PriceObservation
    real (TASK-093/DEC-097: estado comercial diferente nunca é
    deduplicado)."""

    source_code = "amazon"

    def __init__(self) -> None:
        self.call_count = 0

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.call_count += 1
        completed = request.requested_at.replace(microsecond=500000)
        price = f"{3999 + self.call_count}.90"
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/cadence-gpu",
                    title="NVIDIA GeForce RTX 5070 Ti",
                    collected_at=completed,
                    external_id="cadence-gpu-stable",
                    raw_price=price,
                    raw_currency="BRL",
                    raw_availability="Em estoque",
                ),
            ),
        )


class _NewOfferEachCallProvider:
    """Cada chamada "descobre" uma Offer NOVA (external_id nunca repete)
    -- sempre FIRST_OBSERVATION (`latest is None`), nunca CHANGED. Simula
    "começamos a monitorar mais produtos desta loja", que não deveria
    contar como atividade comercial alta."""

    source_code = "amazon"

    def __init__(self) -> None:
        self.call_count = 0

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.call_count += 1
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/new-offer-{self.call_count}",
                    title="NVIDIA GeForce RTX 5070 Ti",
                    collected_at=completed,
                    external_id=f"new-offer-{self.call_count}",
                    raw_price="3999.90",
                    raw_currency="BRL",
                    raw_availability="Em estoque",
                ),
            ),
        )


def _adapter(provider) -> CollectionAdapter:
    return CollectionAdapter(providers=(provider,))


def _run(coro):
    return asyncio.run(coro)


def _seed_user(sessions, label: str) -> UUID:
    with sessions.begin() as session:
        user = User(display_name=f"TASK-112-cadence {label}", role=UserRole.USER)
        session.add(user)
        session.flush()
        return user.id


def _store_id(integration_database, code: str) -> UUID:
    with integration_database.sessions() as session:
        return session.scalar(select(Store.id).where(Store.code == code))


def _make_shared_mission(integration_database, user_id: UUID, *, search_query: str):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            mission, _codes = await create_mission_from_criteria_async(
                session,
                user_id=user_id,
                search_query=search_query,
                target_amount=None,
                target_currency=None,
                source_codes=("amazon",),
                requested_at=NOW,
                actor_type="test",
            )
            return mission

    return _run(run())


def _monitoring_item_id_for(sessions, mission_id: UUID) -> UUID | None:
    with sessions() as session:
        link = session.get(MissionMonitoringItem, mission_id)
        return link.monitoring_item_id if link is not None else None


def _monitoring_item_store(sessions, item_id: UUID, store_id: UUID) -> MonitoringItemStore:
    with sessions() as session:
        return session.get(MonitoringItemStore, (item_id, store_id))


# ---------------------------------------------------------------------------
# 1: NORMAL nunca agenda abaixo do piso configurado.
# ---------------------------------------------------------------------------


def test_normal_mode_never_schedules_below_configured_floor(integration_database) -> None:
    user_id = _seed_user(integration_database.sessions, "normal")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    amazon_id = _store_id(integration_database, "amazon")
    config = CadenceConfig(normal_min_minutes=45, normal_max_minutes=75)

    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(_VaryingPriceProvider()),
            _StubAIManager(),
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=config,
        )
    )
    assert result.claimed is True

    item_store = _monitoring_item_store(integration_database.sessions, item_id, amazon_id)
    delta_minutes = (item_store.next_run_at - NOW).total_seconds() / 60
    assert 45 <= delta_minutes <= 75


# ---------------------------------------------------------------------------
# 2: PROMO_CALENDAR ativo -- 30-45min, nunca abaixo do piso absoluto.
# ---------------------------------------------------------------------------


def test_promo_calendar_shortens_cadence_within_floor(integration_database) -> None:
    with integration_database.sessions.begin() as session:
        session.add(
            PromotionalWindow(
                label="Black Friday sintética",
                starts_at=NOW - timedelta(hours=1),
                ends_at=NOW + timedelta(days=1),
            )
        )

    async def check():
        async with integration_database.async_sessions() as session:
            return await resolve_collection_cadence(
                session,
                store_id=await _any_store_id(session),
                scope_id=uuid4(),
                mission_ids=(),
                now=NOW,
                config=CadenceConfig(),
            )

    decision = _run(check())
    assert decision.mode == "promo_calendar"
    assert decision.min_minutes == 30
    assert decision.max_minutes == 45


async def _any_store_id(session) -> UUID:
    return await session.scalar(select(Store.id).where(Store.code == "amazon"))


def test_cadence_config_rejects_promo_floor_below_30_minutes() -> None:
    with pytest.raises(ValueError, match="piso"):
        CadenceConfig(promo_min_minutes=29, promo_max_minutes=45)


# ---------------------------------------------------------------------------
# 3: backoff por bloqueio confirmado vence sobre promoção -- mesmo com
# janela promocional ativa, o item continua indisponível até o backoff
# expirar.
# ---------------------------------------------------------------------------


def test_backoff_wins_over_active_promo_calendar(integration_database) -> None:
    user_id = _seed_user(integration_database.sessions, "backoff")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    amazon_id = _store_id(integration_database, "amazon")

    with integration_database.sessions.begin() as session:
        session.add(
            PromotionalWindow(
                label="Cyber Monday sintética",
                starts_at=NOW - timedelta(hours=1),
                ends_at=NOW + timedelta(days=1),
            )
        )

    async def apply_backoff():
        async with integration_database.async_sessions() as session, session.begin():
            await _apply_shared_backoff(
                session,
                monitoring_item_id=item_id,
                store_id=amazon_id,
                base_interval_minutes=45,
                failed_at=NOW,
            )

    _run(apply_backoff())

    item_store = _monitoring_item_store(integration_database.sessions, item_id, amazon_id)
    assert item_store.next_eligible_at is not None
    assert item_store.next_eligible_at > NOW  # ainda bloqueado, mesmo com promo ativa

    # Tentar coletar durante o backoff (mesmo com promo ligada) não claima.
    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(_VaryingPriceProvider()),
            _StubAIManager(),
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW + timedelta(minutes=1),
        )
    )
    assert result.claimed is False


# ---------------------------------------------------------------------------
# 4: atividade alta detectada a partir de PriceObservation real; histerese
# mantém HIGH_ACTIVITY pela duração configurada mesmo após as mudanças
# saírem da janela de observação.
# ---------------------------------------------------------------------------


def test_high_activity_detected_from_persisted_price_observations(integration_database) -> None:
    user_id = _seed_user(integration_database.sessions, "highactivity")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    amazon_id = _store_id(integration_database, "amazon")
    config = CadenceConfig(
        high_activity_window_minutes=30,
        high_activity_change_threshold=3,
        high_activity_duration_minutes=60,
    )

    # 4 coletas com preço diferente a cada vez -- a 1a é FIRST_OBSERVATION
    # (não conta, achado real corrigido nesta rodada), as 3 seguintes são
    # CHANGED de verdade (TASK-093/DEC-097: estado comercial mudou),
    # dentro da janela de 30min -- `_force_due` é um atalho só de teste: a
    # cadência real já empurraria o próximo `next_run_at` para 45min+ na
    # primeira coleta, o que afastaria naturalmente as chamadas seguintes
    # da janela que este teste precisa exercitar (a cadência em si já tem
    # cobertura própria acima).
    provider = _VaryingPriceProvider()
    for minute in (0, 5, 10, 15):
        due_at = NOW + timedelta(minutes=minute)
        _force_due(integration_database, item_id, amazon_id, due_at)
        result = _run(
            collect_monitoring_item_store(
                integration_database.async_sessions,
                _adapter(provider),
                _StubAIManager(),
                monitoring_item_id=item_id,
                store_id=amazon_id,
                now=due_at,
                cadence_config=config,
            )
        )
        assert result.claimed is True

    async def check():
        async with integration_database.async_sessions() as session, session.begin():
            return await resolve_collection_cadence(
                session,
                store_id=amazon_id,
                scope_id=item_id,
                mission_ids=(mission.id,),
                now=NOW + timedelta(minutes=25),
                config=config,
            )

    decision = _run(check())
    assert decision.mode == "high_activity"
    assert decision.min_minutes == 30
    assert decision.max_minutes == 45

    with integration_database.sessions() as session:
        state = session.get(StoreActivityState, (amazon_id, item_id))
        assert state is not None
        assert state.high_activity_until is not None


# ---------------------------------------------------------------------------
# 4b (achado real corrigido nesta rodada): muitas FIRST_OBSERVATION (Offer
# nova a cada chamada -- "começamos a monitorar mais produtos") NUNCA
# disparam HIGH_ACTIVITY sozinhas, mesmo em volume bem acima do limiar.
# ---------------------------------------------------------------------------


def test_many_first_observations_never_trigger_high_activity(integration_database) -> None:
    user_id = _seed_user(integration_database.sessions, "firstobs")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    amazon_id = _store_id(integration_database, "amazon")
    config = CadenceConfig(
        high_activity_window_minutes=30,
        high_activity_change_threshold=3,
        high_activity_duration_minutes=60,
    )

    provider = _NewOfferEachCallProvider()
    for minute in range(10):  # 10 FIRST_OBSERVATION, bem acima do limiar=3
        due_at = NOW + timedelta(minutes=minute)
        _force_due(integration_database, item_id, amazon_id, due_at)
        result = _run(
            collect_monitoring_item_store(
                integration_database.async_sessions,
                _adapter(provider),
                _StubAIManager(),
                monitoring_item_id=item_id,
                store_id=amazon_id,
                now=due_at,
                cadence_config=config,
            )
        )
        assert result.claimed is True
    assert provider.call_count == 10

    async def check():
        async with integration_database.async_sessions() as session, session.begin():
            return await resolve_collection_cadence(
                session,
                store_id=amazon_id,
                scope_id=item_id,
                mission_ids=(mission.id,),
                now=NOW + timedelta(minutes=15),
                config=config,
            )

    decision = _run(check())
    assert decision.mode == "normal"

    with integration_database.sessions() as session:
        state = session.get(StoreActivityState, (amazon_id, item_id))
        assert state is None  # nunca sequer criado


def _force_due(integration_database, item_id: UUID, store_id: UUID, due_at: datetime) -> None:
    """Atalho só de teste: força o item a ficar due num instante escolhido
    -- necessário para gerar várias mudanças comerciais dentro da MESMA
    janela de observação de atividade alta sem depender da cadência real
    (que já empurra o próximo `next_run_at` para 45min+ logo na primeira
    coleta, o que naturalmente afastaria as chamadas seguintes da janela
    de 30min que este teste precisa exercitar)."""
    with integration_database.sessions.begin() as session:
        item_store = session.get(MonitoringItemStore, (item_id, store_id))
        item_store.next_run_at = due_at
        item_store.next_eligible_at = None


def test_high_activity_reverts_to_normal_after_duration_expires(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    scope_id = uuid4()
    config = CadenceConfig(high_activity_duration_minutes=60)

    with integration_database.sessions.begin() as session:
        session.add(
            StoreActivityState(
                store_id=amazon_id,
                scope_id=scope_id,
                high_activity_until=NOW + timedelta(minutes=10),
            )
        )

    async def check(now):
        async with integration_database.async_sessions() as session:
            return await resolve_collection_cadence(
                session,
                store_id=amazon_id,
                scope_id=scope_id,
                mission_ids=(),
                now=now,
                config=config,
            )

    still_active = _run(check(NOW + timedelta(minutes=5)))
    assert still_active.mode == "high_activity"

    expired = _run(check(NOW + timedelta(minutes=15)))
    assert expired.mode == "normal"


# ---------------------------------------------------------------------------
# 7/8: a política de cadência vale para os DOIS caminhos -- coexistência
# permanente não pode significar dois níveis de proteção diferentes.
# ---------------------------------------------------------------------------


def _make_legacy_mission(integration_database, user_id, *, store_id, due_at):
    with integration_database.sessions.begin() as session:
        mission = Mission(user_id=user_id, title="legacy cadence", status=MissionStatus.ACTIVE)
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="item genérico sem identidade"),
                MissionSchedule(
                    mission_id=mission.id, interval_minutes=15, next_run_at=due_at, is_enabled=True
                ),
                MissionSource(mission_id=mission.id, store_id=store_id),
            )
        )
        return mission.id


def test_legacy_schedule_normal_cadence_never_below_floor(integration_database) -> None:
    """Achado real corrigido nesta rodada: `claim_due_collections` avançava
    a agenda pelo `MissionSchedule.interval_minutes` fixo (aqui 15min de
    propósito, simulando configuração antiga) -- a MESMA política de
    cadência do caminho compartilhado agora vale aqui, ignorando o
    intervalo antigo. `claim_due_collections` só claima/avança a agenda
    (Fase A) -- a chamada de rede real não é o objeto deste teste."""
    with integration_database.sessions.begin() as session:
        pichau_id = session.scalar(select(Store.id).where(Store.code == "pichau"))
        user = User(display_name="legacy-cadence-normal", role=UserRole.USER)
        session.add(user)
        session.flush()
        user_id = user.id
    mission_id = _make_legacy_mission(integration_database, user_id, store_id=pichau_id, due_at=NOW)

    async def run():
        async with integration_database.async_sessions() as session, session.begin():
            claims = await claim_due_collections(
                session, now=NOW, limit=25, max_users=5,
                cadence_config=CadenceConfig(normal_min_minutes=45, normal_max_minutes=75),
            )
            return claims

    claims = _run(run())
    assert len(claims) == 1

    with integration_database.sessions() as session:
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        delta_minutes = (schedule.next_run_at - NOW).total_seconds() / 60
        assert 45 <= delta_minutes <= 75


def test_legacy_schedule_promo_cadence_applies(integration_database) -> None:
    with integration_database.sessions.begin() as session:
        pichau_id = session.scalar(select(Store.id).where(Store.code == "pichau"))
        user = User(display_name="legacy-cadence-promo", role=UserRole.USER)
        session.add(user)
        session.flush()
        user_id = user.id
        session.add(
            PromotionalWindow(
                label="Promo sintética legado",
                starts_at=NOW - timedelta(hours=1),
                ends_at=NOW + timedelta(days=1),
            )
        )
    mission_id = _make_legacy_mission(integration_database, user_id, store_id=pichau_id, due_at=NOW)

    async def run():
        async with integration_database.async_sessions() as session, session.begin():
            return await claim_due_collections(session, now=NOW, limit=25, max_users=5)

    claims = _run(run())
    assert len(claims) == 1

    with integration_database.sessions() as session:
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        delta_minutes = (schedule.next_run_at - NOW).total_seconds() / 60
        assert 30 <= delta_minutes <= 45


# ---------------------------------------------------------------------------
# TASK-116: HIGH_ACTIVITY escopado por unidade de monitoramento, nunca por
# loja inteira -- achado real em PROD (mudança de preço em qualquer produto
# acelerava TODOS os itens monitorados na mesma loja).
# ---------------------------------------------------------------------------


def test_unrelated_monitoring_item_same_store_stays_normal(integration_database) -> None:
    """B: item A entra em HIGH_ACTIVITY; item B, mesma loja, produto
    totalmente alheio, permanece NORMAL."""
    user_a = _seed_user(integration_database.sessions, "scope-a")
    user_b = _seed_user(integration_database.sessions, "scope-b")
    mission_a = _make_shared_mission(integration_database, user_a, search_query="RTX 5070 Ti")
    mission_b = _make_shared_mission(
        integration_database, user_b, search_query="AMD Ryzen 9 9950X"
    )
    item_a = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    item_b = _monitoring_item_id_for(integration_database.sessions, mission_b.id)
    assert item_a != item_b
    amazon_id = _store_id(integration_database, "amazon")
    config = CadenceConfig(
        high_activity_window_minutes=30,
        high_activity_change_threshold=3,
        high_activity_duration_minutes=60,
    )

    provider = _VaryingPriceProvider()
    for minute in (0, 5, 10, 15):
        due_at = NOW + timedelta(minutes=minute)
        _force_due(integration_database, item_a, amazon_id, due_at)
        result = _run(
            collect_monitoring_item_store(
                integration_database.async_sessions,
                _adapter(provider),
                _StubAIManager(),
                monitoring_item_id=item_a,
                store_id=amazon_id,
                now=due_at,
                cadence_config=config,
            )
        )
        assert result.claimed is True

    async def check(item_id, mission_id):
        async with integration_database.async_sessions() as session, session.begin():
            return await resolve_collection_cadence(
                session,
                store_id=amazon_id,
                scope_id=item_id,
                mission_ids=(mission_id,),
                now=NOW + timedelta(minutes=25),
                config=config,
            )

    assert _run(check(item_a, mission_a.id)).mode == "high_activity"
    assert _run(check(item_b, mission_b.id)).mode == "normal"


def test_two_missions_sharing_monitoring_item_share_high_activity(
    integration_database,
) -> None:
    """C: duas Missions de usuários diferentes, mesma identidade resolvida
    -> mesmo MonitoringItem -> compartilham o mesmo estado de atividade."""
    user_1 = _seed_user(integration_database.sessions, "shared-1")
    user_2 = _seed_user(integration_database.sessions, "shared-2")
    mission_1 = _make_shared_mission(integration_database, user_1, search_query="RTX 5070 Ti")
    mission_2 = _make_shared_mission(integration_database, user_2, search_query="RTX 5070 Ti")
    item_1 = _monitoring_item_id_for(integration_database.sessions, mission_1.id)
    item_2 = _monitoring_item_id_for(integration_database.sessions, mission_2.id)
    assert item_1 == item_2  # mesma unidade compartilhada (TASK-112)
    amazon_id = _store_id(integration_database, "amazon")
    config = CadenceConfig(
        high_activity_window_minutes=30,
        high_activity_change_threshold=3,
        high_activity_duration_minutes=60,
    )

    provider = _VaryingPriceProvider()
    for minute in (0, 5, 10, 15):
        due_at = NOW + timedelta(minutes=minute)
        _force_due(integration_database, item_1, amazon_id, due_at)
        result = _run(
            collect_monitoring_item_store(
                integration_database.async_sessions,
                _adapter(provider),
                _StubAIManager(),
                monitoring_item_id=item_1,
                store_id=amazon_id,
                now=due_at,
                cadence_config=config,
            )
        )
        assert result.claimed is True

    async def check(mission_id):
        async with integration_database.async_sessions() as session, session.begin():
            return await resolve_collection_cadence(
                session,
                store_id=amazon_id,
                scope_id=item_1,
                mission_ids=(mission_id,),
                now=NOW + timedelta(minutes=25),
                config=config,
            )

    # Mesmo detectado via a Offer/relevance da Mission 1, o estado (histerese
    # em StoreActivityState) é chaveado por scope_id=item_id -- Mission 2
    # também enxerga HIGH_ACTIVITY, mesmo sem gerar nenhuma observação
    # própria (mission_ids=(mission_2.id,) sozinho não bateria o limiar).
    assert _run(check(mission_2.id)).mode == "high_activity"


def test_never_schedules_below_absolute_floor_even_in_high_activity(
    integration_database,
) -> None:
    """I: piso absoluto de 30min -- HIGH_ACTIVITY nunca agenda antes disso,
    mesmo em múltiplas coletas sucessivas."""
    user_id = _seed_user(integration_database.sessions, "floor")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    amazon_id = _store_id(integration_database, "amazon")
    config = CadenceConfig(
        high_activity_window_minutes=30,
        high_activity_change_threshold=3,
        high_activity_duration_minutes=60,
    )
    provider = _VaryingPriceProvider()
    for minute in (0, 5, 10, 15, 20):
        due_at = NOW + timedelta(minutes=minute)
        _force_due(integration_database, item_id, amazon_id, due_at)
        _run(
            collect_monitoring_item_store(
                integration_database.async_sessions,
                _adapter(provider),
                _StubAIManager(),
                monitoring_item_id=item_id,
                store_id=amazon_id,
                now=due_at,
                cadence_config=config,
            )
        )
    item_store = _monitoring_item_store(integration_database.sessions, item_id, amazon_id)
    delta_minutes = (item_store.next_run_at - (NOW + timedelta(minutes=20))).total_seconds() / 60
    assert delta_minutes >= 30
