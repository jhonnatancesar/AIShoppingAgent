"""TASK-113: `MissionProductAlertState` -- decisão sob lock e bootstrap.

G: duas stores da MESMA Mission/Product decidindo "ao mesmo tempo" (duas
conexões `asyncpg` reais, `asyncio.gather`) nunca produzem dois alertas
para a mesma oportunidade -- prova, não comentário, de que o `SELECT
missions ... FOR UPDATE` já existente em `_persist_phase_c` (TASK-079)
serializa a decisão.

I: o backfill determinístico da migration `20260827_0001` (reconstrução
de `best_notified_amount`/`last_notified_amount`/`last_notified_at`/
`last_alert_event_id` a partir de `Event`s reais já existentes) é
re-executado aqui como a MESMA query (comentário cruza para o arquivo da
migration) contra dados sintéticos, para provar o resultado
determinístico sem depender de rodar a migration inteira de novo.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.alerts.models import MissionProductAlertState
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability
from app.collection.orchestration import (
    PriceObservationComparison,
    _PendingOffer,
    _persist_phase_c,
    _PhaseAOutcome,
)
from app.collection.relevance import OfferRelevance
from app.core.config import Settings
from app.events import Event, EventType
from app.events.service import publish_event
from app.missions.models import Mission, MissionCriteria, MissionStatus
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select, text

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
_SETTINGS = Settings(_env_file=None)


# ---------------------------------------------------------------------------
# G: decisão final sob o lock da Mission -- sem duplicar alerta
# ---------------------------------------------------------------------------


def _store_ids(sessions, codes: tuple[str, str]) -> tuple:
    with sessions() as session:
        rows = {
            row.code: row.id
            for row in session.execute(
                select(Store).where(Store.code.in_(codes))
            ).scalars()
        }
    return tuple(rows[code] for code in codes)


def test_two_stores_same_mission_product_never_double_alert(integration_database) -> None:
    store_a, store_b = _store_ids(integration_database.sessions, ("amazon", "kabum"))

    with integration_database.sessions.begin() as session:
        user = User(display_name="TASK-113 G", role=UserRole.USER)
        product = Product(name="TASK-113 G synthetic product")
        session.add_all((user, product))
        session.flush()
        mission = Mission(user_id=user.id, title="TASK-113 G mission", status=MissionStatus.ACTIVE)
        offer_a = Offer(
            product_id=product.id, store_id=store_a,
            external_id=f"g-a-{uuid4().hex}", url=f"https://example.invalid/{uuid4().hex}",
        )
        offer_b = Offer(
            product_id=product.id, store_id=store_b,
            external_id=f"g-b-{uuid4().hex}", url=f"https://example.invalid/{uuid4().hex}",
        )
        session.add_all((mission, offer_a, offer_b))
        session.flush()
        criteria = MissionCriteria(mission_id=mission.id, search_query="synthetic")
        # Runs "anteriores" (já SUCCEEDED, produziram a observação de
        # referência 5000) + runs "atuais" (RUNNING, produzirão 4000 --
        # exatamente as que `_persist_phase_c` vai travar/finalizar).
        prev_run_a = CollectionRun(
            mission_id=mission.id, store_id=store_a,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=NOW - timedelta(hours=2), finished_at=NOW - timedelta(hours=1),
        )
        prev_run_b = CollectionRun(
            mission_id=mission.id, store_id=store_b,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=NOW - timedelta(hours=2), finished_at=NOW - timedelta(hours=1),
        )
        run_a = CollectionRun(
            mission_id=mission.id, store_id=store_a,
            status=CollectionRunStatus.RUNNING, started_at=NOW,
        )
        run_b = CollectionRun(
            mission_id=mission.id, store_id=store_b,
            status=CollectionRunStatus.RUNNING, started_at=NOW,
        )
        session.add_all((criteria, prev_run_a, prev_run_b, run_a, run_b))
        session.flush()

        def _observation(offer_id, run_id, amount, observed_at) -> PriceObservation:
            observation = PriceObservation(
                offer_id=offer_id, collection_run_id=run_id,
                amount=Decimal(amount), currency="BRL", total_amount=Decimal(amount),
                availability=Availability.AVAILABLE, observed_at=observed_at,
            )
            session.add(observation)
            session.flush()
            return observation

        previous_a = _observation(offer_a.id, prev_run_a.id, "5000.00", NOW - timedelta(hours=1))
        previous_b = _observation(offer_b.id, prev_run_b.id, "5000.00", NOW - timedelta(hours=1))
        current_a = _observation(offer_a.id, run_a.id, "4000.00", NOW)
        current_b = _observation(offer_b.id, run_b.id, "4000.00", NOW)

        mission_id = mission.id
        product_id = product.id
        offer_a_id, offer_b_id = offer_a.id, offer_b.id
        run_a_id, run_b_id = run_a.id, run_b.id
        obs_a_id, obs_b_id = current_a.id, current_b.id
        prev_obs_a_id, prev_obs_b_id = previous_a.id, previous_b.id

    # Mesma oportunidade comercial (5000 -> 4000), descoberta por DUAS
    # lojas "ao mesmo tempo" -- se o lock não serializasse, as duas
    # decidiriam com checkpoint=None (nunca alertado) e as duas alertariam.
    def _outcome(run_id, store_id, offer_id, observation_id, previous_observation_id) -> _PhaseAOutcome:
        pending = _PendingOffer(
            offer_id=offer_id,
            product_id=product_id,
            observation_id=observation_id,
            amount=Decimal("4000.00"),
            currency="BRL",
            availability=Availability.AVAILABLE,
            observed_at=NOW,
            raw_title="synthetic",
            needs_relevance=False,
            needs_display_name=False,
            observation_created=True,
            alert_comparison=PriceObservationComparison.CHANGED,
            previous_observation_id=previous_observation_id,
            previous_amount=Decimal("5000.00"),
            previous_currency="BRL",
            previous_availability=Availability.AVAILABLE,
            previous_observed_at=NOW - timedelta(hours=1),
            forced_relevance=OfferRelevance.MATCH,
        )
        return _PhaseAOutcome(
            run_id=run_id,
            mission_id=mission_id,
            store_id=store_id,
            mission_search_query="synthetic",
            target_amount=None,
            target_currency=None,
            completed_at=NOW,
            offers=(pending,),
        )

    async def _run_both():
        return await asyncio.gather(
            _persist_phase_c(
                integration_database.async_sessions,
                _outcome(run_a_id, store_a, offer_a_id, obs_a_id, prev_obs_a_id),
                (),
                settings=_SETTINGS,
            ),
            _persist_phase_c(
                integration_database.async_sessions,
                _outcome(run_b_id, store_b, offer_b_id, obs_b_id, prev_obs_b_id),
                (),
                settings=_SETTINGS,
            ),
        )

    results = asyncio.run(_run_both())
    assert list(results) == [True, True]  # as duas runs terminam com sucesso

    with integration_database.sessions() as session:
        events = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_id,
                    Event.event_type == EventType.PRICE_DECREASED_V1.value,
                )
            )
        )
        checkpoint = session.get(MissionProductAlertState, (mission_id, product_id))

    assert len(events) == 1  # nunca dois alertas para a mesma oportunidade
    assert checkpoint is not None
    assert checkpoint.best_notified_amount == Decimal("4000.0000")
    assert checkpoint.last_notified_amount == Decimal("4000.0000")


# ---------------------------------------------------------------------------
# I: bootstrap determinístico a partir de Events reais (migration 20260827_0001)
# ---------------------------------------------------------------------------

_BACKFILL_SQL = text(
    """
    WITH alert_events AS (
        SELECT
            e.mission_id AS mission_id,
            o.product_id AS product_id,
            e.id AS event_id,
            (e.payload ->> 'current_total')::numeric(19, 4) AS amount,
            e.payload ->> 'currency' AS currency,
            e.occurred_at AS occurred_at
        FROM events e
        JOIN offers o ON o.id = (e.payload ->> 'offer_id')::uuid
        WHERE e.event_type IN ('price.decreased.v1', 'price.target_reached.v1')
          AND e.mission_id IS NOT NULL
          AND e.id = ANY(:event_ids)
    ),
    best AS (
        SELECT DISTINCT ON (mission_id, product_id)
            mission_id, product_id, amount AS best_notified_amount,
            currency AS best_notified_currency
        FROM alert_events
        ORDER BY mission_id, product_id, amount ASC, occurred_at ASC
    ),
    last_alert AS (
        SELECT DISTINCT ON (mission_id, product_id)
            mission_id, product_id, amount AS last_notified_amount,
            occurred_at AS last_notified_at, event_id AS last_alert_event_id
        FROM alert_events
        ORDER BY mission_id, product_id, occurred_at DESC, amount ASC
    )
    INSERT INTO mission_product_alert_state (
        mission_id, product_id, best_notified_amount, best_notified_currency,
        last_notified_amount, last_notified_at, rearmed_at,
        last_alert_event_id, updated_at
    )
    SELECT
        b.mission_id, b.product_id, b.best_notified_amount,
        b.best_notified_currency, l.last_notified_amount,
        l.last_notified_at, NULL, l.last_alert_event_id, now()
    FROM best b
    JOIN last_alert l ON l.mission_id = b.mission_id AND l.product_id = b.product_id
    """
)
"""Idêntica à query de `backend/migrations/versions/
20260827_0001_add_market_price_assessment.py` -- só com o filtro extra
`e.id = ANY(:event_ids)` para restringir ao conjunto sintético deste
teste (o backfill real da migration já rodou, sem filtro, na criação do
banco template desta suíte)."""


def test_bootstrap_backfill_reconstructs_best_and_last_notified_deterministically(
    integration_database,
) -> None:
    with integration_database.sessions.begin() as session:
        user = User(display_name="TASK-113 I", role=UserRole.USER)
        product = Product(name="TASK-113 I synthetic product")
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        session.add_all((user, product))
        session.flush()
        mission = Mission(user_id=user.id, title="TASK-113 I mission", status=MissionStatus.ACTIVE)
        offer = Offer(
            product_id=product.id, store_id=store.id,
            external_id=f"i-{uuid4().hex}", url=f"https://example.invalid/{uuid4().hex}",
        )
        session.add_all((mission, offer))
        session.flush()

        # Nunca teve nenhum Event -- não deve ganhar linha (não inventar).
        never_alerted_mission = Mission(
            user_id=user.id, title="TASK-113 I never alerted", status=MissionStatus.ACTIVE
        )
        session.add(never_alerted_mission)
        session.flush()

        t0 = NOW - timedelta(days=200)
        t1 = NOW - timedelta(days=100)
        t2 = NOW  # mais recente -- deve virar last_notified

        from app.events.catalog import (
            AggregateType,
            PriceDecreasedPayload,
            PriceTargetReachedPayload,
        )

        e1 = publish_event(
            session, event_type=EventType.PRICE_DECREASED_V1,
            aggregate_type=AggregateType.OFFER, aggregate_id=offer.id,
            payload=PriceDecreasedPayload(
                offer_id=offer.id, observation_id=uuid4(), previous_observation_id=uuid4(),
                previous_total=Decimal("4500.00"), current_total=Decimal("3900.00"), currency="BRL",
            ),
            occurred_at=t0, mission_id=mission.id,
        )
        e2 = publish_event(
            session, event_type=EventType.PRICE_TARGET_REACHED_V1,
            aggregate_type=AggregateType.MISSION, aggregate_id=mission.id,
            payload=PriceTargetReachedPayload(
                mission_id=mission.id, offer_id=offer.id, observation_id=uuid4(),
                target_total=Decimal("4300.00"), current_total=Decimal("4199.99"), currency="BRL",
            ),
            occurred_at=t1, mission_id=mission.id,
        )
        e3 = publish_event(
            session, event_type=EventType.PRICE_DECREASED_V1,
            aggregate_type=AggregateType.OFFER, aggregate_id=offer.id,
            payload=PriceDecreasedPayload(
                offer_id=offer.id, observation_id=uuid4(), previous_observation_id=uuid4(),
                previous_total=Decimal("4600.00"), current_total=Decimal("4300.00"), currency="BRL",
            ),
            occurred_at=t2, mission_id=mission.id,
        )
        event_ids = [e1.id, e2.id, e3.id]
        mission_id, product_id = mission.id, product.id
        never_alerted_id = never_alerted_mission.id

        session.execute(_BACKFILL_SQL, {"event_ids": event_ids})

    with integration_database.sessions() as session:
        row = session.get(MissionProductAlertState, (mission_id, product_id))
        never_alerted_row = session.get(
            MissionProductAlertState, (never_alerted_id, product_id)
        )

    assert row is not None
    assert row.best_notified_amount == Decimal("3900.0000")  # MIN(3900, 4199.99, 4300)
    assert row.last_notified_amount == Decimal("4300.0000")  # evento mais recente (t2)
    assert row.last_notified_at == t2
    assert row.last_alert_event_id == e3.id
    assert row.rearmed_at is None  # bootstrap nunca inventa rearm
    assert never_alerted_row is None  # nunca alertado -- sem linha, nunca inventado
