"""Cotas de capacidade por usuário (TASK-107, `DEC-094`).

Cada missão `ACTIVE` e cada loja monitorada por ela consomem cota
(`max_active_missions`/`max_store_slots`); cada pesquisa aceita consome
`max_daily_searches`. `PAUSED`/`CANCELLED`/`EXPIRED`/`COMPLETED` nunca
consomem. Este módulo nunca pausa, cancela nem descarta nada sozinho --
só recusa a operação que excederia o limite, com `QuotaExceededError`
carregando dado estruturado (`kind`/`limit`/`current`/`actions`) para o
chamador (router) decidir como comunicar ao usuário."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.missions.models import Mission, MissionSource, MissionStatus
from app.quotas.models import SearchReceipt
from app.users.models import User


class QuotaKind(StrEnum):
    ACTIVE_MISSIONS = "active_missions"
    STORE_SLOTS = "store_slots"
    DAILY_SEARCHES = "daily_searches"


class QuotaExceededError(RuntimeError):
    """Erro estável de negócio -- nunca pausa/cancela nada sozinho; o
    chamador decide como comunicar `actions` ao usuário."""

    def __init__(
        self,
        kind: QuotaKind,
        *,
        limit: int,
        current: int,
        message: str,
        actions: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.limit = limit
        self.current = current
        self.actions = actions


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    max_active_missions: int
    max_store_slots: int
    max_daily_searches: int


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    active_missions: int
    store_slots: int
    daily_searches: int


def resolve_quota_limits(user: User, settings: Settings) -> QuotaLimits:
    """`NULL` no override do usuário usa o default do sistema -- nenhum
    plano/tier novo, só um valor pontual por usuário (`DEC-094`)."""
    return QuotaLimits(
        max_active_missions=(
            user.max_active_missions_override
            if user.max_active_missions_override is not None
            else settings.default_max_active_missions
        ),
        max_store_slots=(
            user.max_store_slots_override
            if user.max_store_slots_override is not None
            else settings.default_max_store_slots
        ),
        max_daily_searches=(
            user.max_daily_searches_override
            if user.max_daily_searches_override is not None
            else settings.default_max_daily_searches
        ),
    )


def day_start_utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now deve possuir fuso horário.")
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def next_daily_reset_at(now: datetime) -> datetime:
    """Próxima meia-noite UTC -- quando `max_daily_searches` volta a zero."""
    return day_start_utc(now) + timedelta(days=1)


def get_quota_usage(session: Session, user_id: UUID, *, now: datetime) -> QuotaUsage:
    active_missions = (
        session.scalar(
            select(func.count())
            .select_from(Mission)
            .where(Mission.user_id == user_id, Mission.status == MissionStatus.ACTIVE)
        )
        or 0
    )
    store_slots = (
        session.scalar(
            select(func.count())
            .select_from(MissionSource)
            .join(Mission, Mission.id == MissionSource.mission_id)
            .where(Mission.user_id == user_id, Mission.status == MissionStatus.ACTIVE)
        )
        or 0
    )
    daily_searches = (
        session.scalar(
            select(func.count())
            .select_from(SearchReceipt)
            .where(
                SearchReceipt.user_id == user_id,
                SearchReceipt.created_at >= day_start_utc(now),
            )
        )
        or 0
    )
    return QuotaUsage(
        active_missions=active_missions,
        store_slots=store_slots,
        daily_searches=daily_searches,
    )


async def get_quota_usage_async(
    session: AsyncSession, user_id: UUID, *, now: datetime
) -> QuotaUsage:
    active_missions = (
        await session.scalar(
            select(func.count())
            .select_from(Mission)
            .where(Mission.user_id == user_id, Mission.status == MissionStatus.ACTIVE)
        )
        or 0
    )
    store_slots = (
        await session.scalar(
            select(func.count())
            .select_from(MissionSource)
            .join(Mission, Mission.id == MissionSource.mission_id)
            .where(Mission.user_id == user_id, Mission.status == MissionStatus.ACTIVE)
        )
        or 0
    )
    daily_searches = (
        await session.scalar(
            select(func.count())
            .select_from(SearchReceipt)
            .where(
                SearchReceipt.user_id == user_id,
                SearchReceipt.created_at >= day_start_utc(now),
            )
        )
        or 0
    )
    return QuotaUsage(
        active_missions=active_missions,
        store_slots=store_slots,
        daily_searches=daily_searches,
    )


def _active_missions_error(
    usage: QuotaUsage, limits: QuotaLimits
) -> QuotaExceededError:
    return QuotaExceededError(
        QuotaKind.ACTIVE_MISSIONS,
        limit=limits.max_active_missions,
        current=usage.active_missions,
        message=(
            f"Você já tem {usage.active_missions}/{limits.max_active_missions} "
            "missões ativas."
        ),
        actions=("pause_mission", "cancel_mission", "manage_missions"),
    )


def _store_slots_error(usage: QuotaUsage, limits: QuotaLimits) -> QuotaExceededError:
    return QuotaExceededError(
        QuotaKind.STORE_SLOTS,
        limit=limits.max_store_slots,
        current=usage.store_slots,
        message=(
            f"Você usa {usage.store_slots}/{limits.max_store_slots} lojas de "
            "monitoramento."
        ),
        actions=(
            "reduce_mission_stores",
            "pause_mission",
            "cancel_mission",
            "manage_missions",
        ),
    )


def _daily_searches_error(usage: QuotaUsage, limits: QuotaLimits) -> QuotaExceededError:
    return QuotaExceededError(
        QuotaKind.DAILY_SEARCHES,
        limit=limits.max_daily_searches,
        current=usage.daily_searches,
        message=(
            f"Você já fez {usage.daily_searches}/{limits.max_daily_searches} "
            "pesquisas hoje."
        ),
        actions=("wait_for_daily_reset",),
    )


def check_mission_activation_quota(
    session: Session,
    *,
    user: User,
    mission_id: UUID,
    settings: Settings,
    now: datetime,
) -> None:
    """Chamado só para `ACTIVATE`/`RESUME`, antes de aplicar o novo status
    -- a missão sendo ativada ainda não está `ACTIVE` no banco neste
    ponto, então `get_quota_usage` naturalmente não a conta (evita contar
    a própria transição duas vezes)."""
    limits = resolve_quota_limits(user, settings)
    usage = get_quota_usage(session, user.id, now=now)
    if usage.active_missions + 1 > limits.max_active_missions:
        raise _active_missions_error(usage, limits)
    this_mission_sources = (
        session.scalar(
            select(func.count())
            .select_from(MissionSource)
            .where(MissionSource.mission_id == mission_id)
        )
        or 0
    )
    if usage.store_slots + this_mission_sources > limits.max_store_slots:
        raise _store_slots_error(usage, limits)


async def check_mission_activation_quota_async(
    session: AsyncSession,
    *,
    user: User,
    mission_id: UUID,
    settings: Settings,
    now: datetime,
) -> None:
    limits = resolve_quota_limits(user, settings)
    usage = await get_quota_usage_async(session, user.id, now=now)
    if usage.active_missions + 1 > limits.max_active_missions:
        raise _active_missions_error(usage, limits)
    this_mission_sources = (
        await session.scalar(
            select(func.count())
            .select_from(MissionSource)
            .where(MissionSource.mission_id == mission_id)
        )
        or 0
    )
    if usage.store_slots + this_mission_sources > limits.max_store_slots:
        raise _store_slots_error(usage, limits)


async def check_and_reserve_search_quota_async(
    session: AsyncSession, *, user: User, settings: Settings, now: datetime
) -> None:
    """Só reserva (grava `SearchReceipt`) depois de confirmar que a
    pesquisa cabe na cota do dia -- pesquisa recusada nunca é contada."""
    limits = resolve_quota_limits(user, settings)
    usage = await get_quota_usage_async(session, user.id, now=now)
    if usage.daily_searches + 1 > limits.max_daily_searches:
        raise _daily_searches_error(usage, limits)
    session.add(SearchReceipt(user_id=user.id))
