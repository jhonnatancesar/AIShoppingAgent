"""Valida a TASK-037 contra PostgreSQL e Telegram reais sem deixar dados."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.events import Event, EventConsumptionAttempt
from app.missions.models import Mission, MissionStatus
from app.telegram.contracts import TelegramChatType
from app.telegram.notifications import process_telegram_notifications
from app.telegram.preferences import handle_preferences_command
from app.telegram.router import (
    TelegramUpdate,
    _TelegramChat,
    _TelegramIncomingMessage,
    _TelegramSender,
    receive_telegram_webhook,
)
from app.users.models import User, UserRole
from sqlalchemy import select
from sqlalchemy.orm import Session


async def validate() -> None:
    settings = Settings()
    if settings.telegram_bot_token is None or settings.telegram_webhook_secret is None:
        raise SystemExit("Telegram token and webhook secret are required")
    raw_telegram_user_id = os.getenv("AISHOPPING_TELEGRAM_VALIDATION_USER_ID")
    if raw_telegram_user_id is None:
        raise SystemExit("AISHOPPING_TELEGRAM_VALIDATION_USER_ID is required")
    telegram_user_id = int(raw_telegram_user_id)

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    session = session_factory()
    transaction = session.begin()
    try:
        user = User(
            display_name="Validação TASK-037",
            role=UserRole.USER,
            is_active=True,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_user_id,
            notify_price_decreases=True,
            notify_target_reached=True,
        )
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="validação temporária TASK-037",
            status=MissionStatus.ACTIVE,
            state_version=1,
        )
        session.add(mission)
        session.flush()

        disabled = await _run_command(
            "/preferencias quedas desativar",
            telegram_user_id=telegram_user_id,
            settings=settings,
            session=session,
        )
        skipped_drop = _price_drop_event(mission)
        session.add(skipped_drop)
        session.flush()
        first_batch = await process_telegram_notifications(
            session, bot_token=settings.telegram_bot_token
        )

        enabled = await _run_command(
            "/preferencias quedas ativar",
            telegram_user_id=telegram_user_id,
            settings=settings,
            session=session,
        )
        handle_preferences_command(user, "/preferencias alvo desativar")
        skipped_target = _target_event(mission)
        session.add(skipped_target)
        session.flush()
        second_batch = await process_telegram_notifications(
            session, bot_token=settings.telegram_bot_token
        )
        handle_preferences_command(user, "/preferencias alvo ativar")

        delivered_drop = _price_drop_event(mission)
        session.add(delivered_drop)
        session.flush()
        third_batch = await process_telegram_notifications(
            session, bot_token=settings.telegram_bot_token
        )
        empty_batch = await process_telegram_notifications(
            session, bot_token=settings.telegram_bot_token
        )

        attempts = list(
            session.scalars(
                select(EventConsumptionAttempt)
                .where(
                    EventConsumptionAttempt.event_id.in_(
                        (skipped_drop.id, skipped_target.id, delivered_drop.id)
                    )
                )
                .order_by(EventConsumptionAttempt.attempted_at)
            )
        )
        outcomes = sorted(attempt.outcome.value for attempt in attempts)
        if disabled.status_code != 204 or enabled.status_code != 204:
            raise RuntimeError("preferences command did not return 204")
        if (first_batch.skipped, second_batch.skipped) != (1, 1):
            raise RuntimeError("disabled preferences were not skipped")
        if third_batch.succeeded != 1 or empty_batch.claimed != 0:
            raise RuntimeError("reactivation did not restrict delivery to new events")
        if outcomes != ["skipped", "skipped", "succeeded"]:
            raise RuntimeError(f"unexpected terminal outcomes: {outcomes}")
        if any(attempt.failure_code is not None for attempt in attempts):
            raise RuntimeError("terminal outcomes unexpectedly contain failure_code")
        print(
            "TASK-037 real validation passed: commands=2, skipped=2, "
            "delivered=1, pending_after_reactivation=0"
        )
    finally:
        transaction.rollback()
        session.close()
        engine.dispose()


async def _run_command(
    text: str,
    *,
    telegram_user_id: int,
    settings: Settings,
    session: Session,
):
    secret = settings.telegram_webhook_secret
    assert secret is not None
    return await receive_telegram_webhook(
        update=TelegramUpdate(
            update_id=int(datetime.now(UTC).timestamp()),
            message=_TelegramIncomingMessage(
                text=text,
                date=int(datetime.now(UTC).timestamp()),
                chat=_TelegramChat(id=telegram_user_id, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=telegram_user_id, first_name="Validação"),
            ),
        ),
        x_telegram_bot_api_secret_token=secret.get_secret_value(),
        adapters={},
        settings=settings,
        session=session,
    )


def _price_drop_event(mission: Mission) -> Event:
    return Event(
        event_type="price.decreased.v1",
        aggregate_type="offer",
        aggregate_id=uuid4(),
        mission_id=mission.id,
        payload={
            "offer_id": str(uuid4()),
            "observation_id": str(uuid4()),
            "previous_observation_id": str(uuid4()),
            "previous_total": "5000.00",
            "current_total": "4499.90",
            "currency": "BRL",
        },
        occurred_at=datetime.now(UTC),
    )


def _target_event(mission: Mission) -> Event:
    return Event(
        event_type="price.target_reached.v1",
        aggregate_type="mission",
        aggregate_id=mission.id,
        mission_id=mission.id,
        payload={
            "mission_id": str(mission.id),
            "offer_id": str(uuid4()),
            "observation_id": str(uuid4()),
            "target_total": "4500.00",
            "current_total": "4499.90",
            "currency": "BRL",
        },
        occurred_at=datetime.now(UTC),
    )


if __name__ == "__main__":
    asyncio.run(validate())
