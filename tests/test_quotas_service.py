"""Testes da cota de capacidade por usuário (TASK-107)."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.quotas.service import (
    QuotaExceededError,
    QuotaKind,
    QuotaUsage,
    check_and_reserve_search_quota_async,
    check_mission_activation_quota,
    check_mission_activation_quota_async,
    day_start_utc,
    get_quota_usage,
    next_daily_reset_at,
    resolve_quota_limits,
)
from app.users.models import User, UserRole

NOW = datetime(2026, 8, 22, 15, 30, tzinfo=UTC)


def _user(
    *,
    max_active_missions_override: int | None = None,
    max_store_slots_override: int | None = None,
    max_daily_searches_override: int | None = None,
) -> User:
    return User(
        id=uuid4(),
        display_name="Teste",
        role=UserRole.USER,
        max_active_missions_override=max_active_missions_override,
        max_store_slots_override=max_store_slots_override,
        max_daily_searches_override=max_daily_searches_override,
    )


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_resolve_quota_limits_uses_system_defaults_without_override() -> None:
    limits = resolve_quota_limits(_user(), _settings())

    assert limits.max_active_missions == 5
    assert limits.max_store_slots == 18
    assert limits.max_daily_searches == 30


def test_resolve_quota_limits_prefers_user_override() -> None:
    user = _user(
        max_active_missions_override=10,
        max_store_slots_override=40,
        max_daily_searches_override=100,
    )

    limits = resolve_quota_limits(user, _settings())

    assert limits.max_active_missions == 10
    assert limits.max_store_slots == 40
    assert limits.max_daily_searches == 100


def test_day_start_utc_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="fuso horário"):
        day_start_utc(datetime(2026, 8, 22, 12, 0))


def test_next_daily_reset_at_is_next_utc_midnight() -> None:
    reset_at = next_daily_reset_at(NOW)

    assert reset_at == datetime(2026, 8, 23, 0, 0, tzinfo=UTC)


def test_get_quota_usage_counts_via_session_scalar() -> None:
    session = MagicMock()
    session.scalar.side_effect = [3, 9, 12]

    usage = get_quota_usage(session, uuid4(), now=NOW)

    assert usage == QuotaUsage(active_missions=3, store_slots=9, daily_searches=12)


def test_check_mission_activation_quota_passes_under_limit() -> None:
    session = MagicMock()
    # active_missions=2, store_slots=5, daily_searches=0 (não usado aqui), this_mission_sources=2
    session.scalar.side_effect = [2, 5, 0, 2]

    check_mission_activation_quota(
        session,
        user=_user(),
        mission_id=uuid4(),
        settings=_settings(),
        now=NOW,
    )  # não levanta


def test_check_mission_activation_quota_rejects_at_active_missions_limit() -> None:
    session = MagicMock()
    session.scalar.side_effect = [5, 0, 0]  # já em 5/5, nem chega a contar slots

    with pytest.raises(QuotaExceededError) as excinfo:
        check_mission_activation_quota(
            session,
            user=_user(),
            mission_id=uuid4(),
            settings=_settings(),
            now=NOW,
        )

    assert excinfo.value.kind is QuotaKind.ACTIVE_MISSIONS
    assert excinfo.value.limit == 5
    assert excinfo.value.current == 5
    assert "pause_mission" in excinfo.value.actions


def test_check_mission_activation_quota_rejects_at_store_slots_limit() -> None:
    session = MagicMock()
    # active_missions=1 (ok), store_slots=17, daily_searches=0, this_mission_sources=3 -> 17+3=20 > 18
    session.scalar.side_effect = [1, 17, 0, 3]

    with pytest.raises(QuotaExceededError) as excinfo:
        check_mission_activation_quota(
            session,
            user=_user(),
            mission_id=uuid4(),
            settings=_settings(),
            now=NOW,
        )

    assert excinfo.value.kind is QuotaKind.STORE_SLOTS
    assert excinfo.value.limit == 18
    assert excinfo.value.current == 17
    assert "reduce_mission_stores" in excinfo.value.actions


def test_check_mission_activation_quota_respects_user_override() -> None:
    session = MagicMock()
    session.scalar.side_effect = [1, 0, 0, 1]  # 1 missão ativa já bate o override=1

    with pytest.raises(QuotaExceededError) as excinfo:
        check_mission_activation_quota(
            session,
            user=_user(max_active_missions_override=1),
            mission_id=uuid4(),
            settings=_settings(),
            now=NOW,
        )

    assert excinfo.value.kind is QuotaKind.ACTIVE_MISSIONS
    assert excinfo.value.limit == 1


def test_check_mission_activation_quota_async_mirrors_sync_behavior() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[5, 0, 0])

    with pytest.raises(QuotaExceededError) as excinfo:
        asyncio.run(
            check_mission_activation_quota_async(
                session,
                user=_user(),
                mission_id=uuid4(),
                settings=_settings(),
                now=NOW,
            )
        )

    assert excinfo.value.kind is QuotaKind.ACTIVE_MISSIONS


def test_check_and_reserve_search_quota_async_reserves_when_under_limit() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[0, 0, 10])  # 10/30 pesquisas hoje
    session.add = MagicMock()

    asyncio.run(
        check_and_reserve_search_quota_async(
            session, user=_user(), settings=_settings(), now=NOW
        )
    )

    session.add.assert_called_once()


def test_check_and_reserve_search_quota_async_rejects_and_does_not_reserve() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[0, 0, 30])  # já em 30/30
    session.add = MagicMock()

    with pytest.raises(QuotaExceededError) as excinfo:
        asyncio.run(
            check_and_reserve_search_quota_async(
                session, user=_user(), settings=_settings(), now=NOW
            )
        )

    assert excinfo.value.kind is QuotaKind.DAILY_SEARCHES
    session.add.assert_not_called()
