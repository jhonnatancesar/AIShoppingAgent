"""Validações permanentes dos serviços que exigem PostgreSQL real."""

import asyncio
import selectors
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.collection.contracts import RawCollectedOffer
from app.collection.normalization import PriceNormalizer
from app.collection.orchestration import _resolve_offer
from app.events.models import Event, EventDeliveryCheckpoint
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionSchedule,
    MissionStatus,
)
from app.missions.query import list_visible_missions_for_user
from app.missions.service import transition_mission
from app.offers.models import Offer, OfferShortLink
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy.exc import IntegrityError

from backend.scripts.validate_authorization import validate as validate_authorization
from backend.scripts.validate_limits_resilience import main as validate_resilience
from backend.scripts.validate_password_authentication import (
    main as validate_authentication,
)
from backend.scripts.validate_privacy import main as validate_privacy
from backend.scripts.validate_purchase_trail import validate as validate_purchase_trail
from backend.scripts.validate_recommendation_flow import (
    validate as validate_recommendation_flow,
)

pytestmark = pytest.mark.integration


def test_task084_schema_keeps_links_unique_and_checkpoints_event_scoped(
    integration_database,
) -> None:
    """A mesma Offer pode ser entregue em eventos distintos, mas não duplicada no mesmo."""
    with integration_database.sessions.begin() as session:
        product = Product(name="Produto TASK-084")
        store = Store(
            code=f"task084_{uuid4().hex[:8]}",
            name="Loja TASK-084",
            base_url="https://shop.example.test",
        )
        session.add_all([product, store])
        session.flush()
        offer = Offer(
            product_id=product.id,
            store_id=store.id,
            url="https://shop.example.test/item",
            image_url="https://cdn.example.test/item.jpg",
        )
        session.add(offer)
        session.flush()
        first_event = Event(
            event_type="mission.prelist_ready.v1",
            aggregate_type="offer",
            aggregate_id=offer.id,
            payload={},
            occurred_at=datetime.now(UTC),
        )
        second_event = Event(
            event_type="price.decreased.v1",
            aggregate_type="offer",
            aggregate_id=offer.id,
            payload={},
            occurred_at=datetime.now(UTC),
        )
        session.add_all([first_event, second_event])
        session.flush()
        session.add_all(
            [
                OfferShortLink(token="task084-opaque-token", offer_id=offer.id),
                EventDeliveryCheckpoint(
                    consumer_name="telegram_prelist_v1",
                    event_id=first_event.id,
                    offer_id=offer.id,
                    message_part=0,
                ),
                EventDeliveryCheckpoint(
                    consumer_name="telegram_price_alerts_v1",
                    event_id=second_event.id,
                    offer_id=offer.id,
                    message_part=0,
                ),
            ]
        )
        offer_id = offer.id
        first_event_id = first_event.id

    with integration_database.sessions.begin() as session:
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(OfferShortLink(token="another-token", offer_id=offer_id))
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                EventDeliveryCheckpoint(
                    consumer_name="telegram_prelist_v1",
                    event_id=first_event_id,
                    offer_id=offer_id,
                    message_part=0,
                )
            )
            session.flush()


def test_task084_offer_image_is_updated_only_by_valid_non_null_collection(
    integration_database,
) -> None:
    with integration_database.sessions.begin() as session:
        store = Store(
            code=f"task084_image_{uuid4().hex[:8]}",
            name="Loja imagem TASK-084",
            base_url="https://images.example.test",
        )
        session.add(store)
        session.flush()
        store_id = store.id

    normalizer = PriceNormalizer()

    async def _persist(image_url: str | None) -> tuple[UUID, str | None]:
        raw = RawCollectedOffer(
            source_code="task084",
            url="https://images.example.test/item",
            title="Produto com imagem",
            collected_at=datetime.now(UTC),
            external_id="task084-item",
            raw_price="R$ 100,00",
            image_url=image_url,
        )
        item = normalizer.normalize_offer(raw)
        async with integration_database.async_sessions() as session, session.begin():
            offer = await _resolve_offer(session, store_id, item)
            await session.flush()
            return offer.id, offer.image_url

    def loop_factory() -> asyncio.SelectorEventLoop:
        return asyncio.SelectorEventLoop(selectors.SelectSelector())

    offer_id, image = asyncio.run(
        _persist("https://cdn.example.test/first.jpg"), loop_factory=loop_factory
    )
    assert image == "https://cdn.example.test/first.jpg"
    same_id, retained = asyncio.run(_persist(None), loop_factory=loop_factory)
    assert same_id == offer_id
    assert retained == "https://cdn.example.test/first.jpg"
    same_id, updated = asyncio.run(
        _persist("https://cdn.example.test/second.jpg"), loop_factory=loop_factory
    )
    assert same_id == offer_id
    assert updated == "https://cdn.example.test/second.jpg"


def test_task096_offer_rating_snapshot_is_source_bound_and_complete(
    integration_database,
) -> None:
    with integration_database.sessions.begin() as session:
        store = Store(
            code=f"task096_rating_{uuid4().hex[:8]}",
            name="Loja avaliação TASK-096",
            base_url="https://ratings.example.test",
        )
        session.add(store)
        session.flush()
        store_id = store.id

    normalizer = PriceNormalizer()

    async def _persist(
        average: str | None, count: str | None
    ) -> tuple[UUID, Decimal | None, int | None]:
        raw = RawCollectedOffer(
            source_code="task096",
            url="https://ratings.example.test/item",
            title="Produto avaliado",
            collected_at=datetime.now(UTC),
            external_id="task096-item",
            raw_price="R$ 100,00",
            raw_currency="BRL",
            raw_rating_average=average,
            raw_review_count=count,
        )
        item = normalizer.normalize_offer(raw)
        async with integration_database.async_sessions() as session, session.begin():
            offer = await _resolve_offer(session, store_id, item)
            await session.flush()
            return offer.id, offer.rating_average, offer.review_count

    def loop_factory() -> asyncio.SelectorEventLoop:
        return asyncio.SelectorEventLoop(selectors.SelectSelector())

    offer_id, average, count = asyncio.run(
        _persist("4.8", "2256"), loop_factory=loop_factory
    )
    assert average == Decimal("4.8")
    assert count == 2256
    same_id, retained_average, retained_count = asyncio.run(
        _persist(None, None), loop_factory=loop_factory
    )
    assert same_id == offer_id
    assert retained_average == Decimal("4.8")
    assert retained_count == 2256

    with integration_database.sessions.begin() as session:
        invalid_product = Product(name="Snapshot parcial inválido")
        session.add(invalid_product)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                Offer(
                    product_id=invalid_product.id,
                    store_id=store_id,
                    url="https://ratings.example.test/invalid",
                    rating_average=Decimal("4.5"),
                )
            )
            session.flush()


def test_visible_mission_list_filters_owner_and_status_in_postgresql(
    integration_database,
) -> None:
    """TASK-088: ownership e estados são filtrados pelo PostgreSQL real."""
    now = datetime.now(UTC)
    with integration_database.sessions.begin() as session:
        owner = User(
            display_name="Proprietário da listagem",
            role=UserRole.USER,
            is_active=True,
            telegram_user_id=8_150_000_088,
        )
        other = User(
            display_name="Outro proprietário",
            role=UserRole.USER,
            is_active=True,
            telegram_user_id=8_150_000_089,
        )
        session.add_all([owner, other])
        session.flush()
        session.add_all(
            [
                Mission(user_id=owner.id, title="Ativa", status=MissionStatus.ACTIVE),
                Mission(user_id=owner.id, title="Pausada", status=MissionStatus.PAUSED),
                Mission(
                    user_id=owner.id,
                    title="Cancelada",
                    status=MissionStatus.CANCELLED,
                ),
                Mission(
                    user_id=owner.id,
                    title="Concluída",
                    status=MissionStatus.COMPLETED,
                ),
                Mission(
                    user_id=owner.id,
                    title="Expirada",
                    status=MissionStatus.EXPIRED,
                    expires_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=2),
                    updated_at=now - timedelta(minutes=1),
                ),
                Mission(
                    user_id=other.id,
                    title="Missão alheia",
                    status=MissionStatus.ACTIVE,
                ),
            ]
        )
        owner_id = owner.id

    async def _query() -> list[Mission]:
        async with integration_database.async_sessions() as session:
            return await list_visible_missions_for_user(
                session, user_id=owner_id, limit=16
            )

    missions = asyncio.run(
        _query(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
    assert [(mission.title, mission.status) for mission in missions] == [
        ("Ativa", MissionStatus.ACTIVE),
        ("Pausada", MissionStatus.PAUSED),
        ("Cancelada", MissionStatus.CANCELLED),
    ]


def test_recommendation_comparison_and_confirmation(integration_database) -> None:
    validate_recommendation_flow()


def test_purchase_trail_and_concurrent_resolution(integration_database) -> None:
    validate_purchase_trail()


def test_password_authentication_and_single_use_concurrency(
    integration_database,
) -> None:
    validate_authentication()


def test_authorization_hierarchy_and_ownership(integration_database) -> None:
    validate_authorization()


def test_persistent_replay_rate_limit_and_event_resilience(
    integration_database,
) -> None:
    validate_resilience()


def test_privacy_cleanup_and_fail_closed_deidentification(
    integration_database,
) -> None:
    validate_privacy()


def test_cancel_mission_transition_disables_schedule_in_same_transaction(
    integration_database,
) -> None:
    now = datetime.now(UTC)
    with integration_database.sessions.begin() as session:
        user = User(
            id=uuid4(),
            display_name="Integração cancelamento determinístico",
            role=UserRole.USER,
            is_active=True,
            telegram_user_id=8_150_000_001,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
        mission = Mission(
            id=uuid4(),
            user_id=user.id,
            title="RTX 5070",
            status=MissionStatus.ACTIVE,
            state_version=3,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        schedule = MissionSchedule(
            mission_id=mission.id,
            interval_minutes=60,
            next_run_at=now,
            is_enabled=True,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        session.add_all([mission, schedule])
        session.flush()

        transition = transition_mission(
            session,
            mission_id=mission.id,
            command=MissionCommand.CANCEL,
            expected_state_version=3,
            actor_type="telegram",
            actor_id=user.id,
            transitioned_at=now,
        )

        assert transition.to_status is MissionStatus.CANCELLED
        assert mission.status is MissionStatus.CANCELLED
        assert mission.state_version == 4
        assert schedule.is_enabled is False
        assert schedule.updated_at == now
