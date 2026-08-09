"""Retenção e desidentificação controladas sem reescrever fatos históricos."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.collection.models import CollectionRun, PriceObservation
from app.events.models import Event
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionTransition,
)
from app.purchase.models import PurchaseConfirmation
from app.users.models import User, UserRole

ACTION_TOKEN_RETENTION = timedelta(hours=24)
AUTH_SESSION_RETENTION = timedelta(days=30)
DEIDENTIFIED_DISPLAY_NAME = "Conta desidentificada"
DEIDENTIFIED_MISSION_TITLE = "Missão desidentificada"
DEIDENTIFIED_SEARCH_QUERY = "consulta removida"


class PrivacyOperationError(RuntimeError):
    """Base dos erros controlados das operações locais de privacidade."""


class PrivacyUserNotFound(PrivacyOperationError):
    pass


class PrivacyIdentityMismatch(PrivacyOperationError):
    pass


class HistoricalPersonalDataConflict(PrivacyOperationError):
    """Impede alteração parcial quando um fato imutável ainda contém PII."""

    def __init__(self, areas: tuple[str, ...]) -> None:
        self.areas = areas
        super().__init__(
            "direct personal data was found in immutable history: " + ", ".join(areas)
        )


@dataclass(frozen=True, slots=True)
class PrivacyCleanupResult:
    action_tokens_deleted: int
    auth_sessions_deleted: int


@dataclass(frozen=True, slots=True)
class AccountDeidentificationResult:
    user_id: UUID
    missions_scrubbed: int
    schedules_disabled: int
    credentials_deleted: int
    action_tokens_deleted: int
    auth_sessions_deleted: int
    already_deidentified: bool = False


def cleanup_expired_authentication_artifacts(
    session: Session, *, now: datetime
) -> PrivacyCleanupResult:
    """Remove material autenticador além dos defaults operacionais da V1."""
    _require_aware(now)
    token_cutoff = now - ACTION_TOKEN_RETENTION
    session_cutoff = now - AUTH_SESSION_RETENTION

    tokens = session.execute(
        delete(CredentialActionToken).where(
            or_(
                CredentialActionToken.expires_at <= token_cutoff,
                CredentialActionToken.consumed_at <= token_cutoff,
                CredentialActionToken.invalidated_at <= token_cutoff,
            )
        )
    )
    sessions = session.execute(
        delete(UserAuthSession).where(
            or_(
                UserAuthSession.expires_at <= session_cutoff,
                UserAuthSession.revoked_at <= session_cutoff,
            )
        )
    )
    return PrivacyCleanupResult(
        action_tokens_deleted=tokens.rowcount or 0,
        auth_sessions_deleted=sessions.rowcount or 0,
    )


def deidentify_account(
    session: Session,
    *,
    user_id: UUID,
    expected_telegram_user_id: int,
) -> AccountDeidentificationResult:
    """Remove identificadores diretos e preserva referências pseudônimas.

    O chamador controla a transação. Qualquer conflito em histórico append-only
    é detectado antes da primeira mutação.
    """
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise PrivacyUserNotFound("user was not found")
    if _is_deidentified(user):
        return AccountDeidentificationResult(user.id, 0, 0, 0, 0, 0, True)
    if user.telegram_user_id != expected_telegram_user_id:
        raise PrivacyIdentityMismatch("the expected Telegram identity does not match")

    mission_ids = tuple(
        session.scalars(select(Mission.id).where(Mission.user_id == user.id))
    )
    conflicts = _immutable_history_conflicts(
        session, user=user, mission_ids=mission_ids
    )
    if conflicts:
        raise HistoricalPersonalDataConflict(conflicts)

    mission_rows = session.scalars(
        select(Mission).where(Mission.user_id == user.id)
    ).all()
    for mission in mission_rows:
        mission.title = DEIDENTIFIED_MISSION_TITLE
    criteria_rows = (
        session.scalars(
            select(MissionCriteria).where(MissionCriteria.mission_id.in_(mission_ids))
        ).all()
        if mission_ids
        else []
    )
    for criteria in criteria_rows:
        criteria.search_query = DEIDENTIFIED_SEARCH_QUERY
    schedule_rows = (
        session.scalars(
            select(MissionSchedule).where(MissionSchedule.mission_id.in_(mission_ids))
        ).all()
        if mission_ids
        else []
    )
    schedules_disabled = sum(schedule.is_enabled for schedule in schedule_rows)
    for schedule in schedule_rows:
        schedule.is_enabled = False

    credentials = session.execute(
        delete(UserCredential).where(UserCredential.user_id == user.id)
    )
    tokens = session.execute(
        delete(CredentialActionToken).where(CredentialActionToken.user_id == user.id)
    )
    sessions = session.execute(
        delete(UserAuthSession).where(UserAuthSession.user_id == user.id)
    )

    user.display_name = DEIDENTIFIED_DISPLAY_NAME
    user.role = UserRole.USER
    user.is_active = False
    user.telegram_user_id = None
    user.telegram_chat_id = None
    user.notify_price_decreases = False
    user.notify_target_reached = False
    user.username = None
    user.email = None
    user.favorite_stores = []
    user.preferred_categories = []
    user.registration_step = None
    user.pending_intent = None
    session.add(
        AuditEntry(
            actor_type="system",
            actor_id=None,
            action="privacy.account_deidentified",
            resource_type="user",
            resource_id=user.id,
            entry_metadata={
                "direct_identifiers": "removed",
                "historical_linkage": "internal_uuid_preserved",
            },
        )
    )
    session.flush()
    return AccountDeidentificationResult(
        user_id=user.id,
        missions_scrubbed=len(mission_rows),
        schedules_disabled=schedules_disabled,
        credentials_deleted=credentials.rowcount or 0,
        action_tokens_deleted=tokens.rowcount or 0,
        auth_sessions_deleted=sessions.rowcount or 0,
    )


def _immutable_history_conflicts(
    session: Session, *, user: User, mission_ids: tuple[UUID, ...]
) -> tuple[str, ...]:
    identifiers = tuple(
        value
        for value in (
            user.display_name,
            user.username,
            user.email,
            user.telegram_user_id,
            user.telegram_chat_id,
        )
        if value is not None and str(value).strip()
    )
    conflicts: set[str] = set()

    audit_filters = [AuditEntry.actor_id == user.id, AuditEntry.resource_id == user.id]
    if mission_ids:
        audit_filters.append(AuditEntry.resource_id.in_(mission_ids))
    audit_entries = session.scalars(select(AuditEntry).where(or_(*audit_filters)))
    if any(
        _contains_identifier(entry.entry_metadata, identifiers)
        for entry in audit_entries
    ):
        conflicts.add("audit_entries.metadata")

    confirmations = session.scalars(
        select(PurchaseConfirmation).where(
            PurchaseConfirmation.owner_user_id == user.id
        )
    )
    if any(
        _contains_identifier(
            {"snapshot": item.evidence_snapshot, "url": item.url}, identifiers
        )
        for item in confirmations
    ):
        conflicts.add("purchase_confirmations")

    if mission_ids:
        reasons = session.scalars(
            select(MissionTransition.reason).where(
                MissionTransition.mission_id.in_(mission_ids),
                MissionTransition.reason.is_not(None),
            )
        )
        if any(reason and reason.strip() for reason in reasons):
            conflicts.add("mission_transitions.reason")

        events = session.scalars(
            select(Event).where(
                or_(
                    Event.mission_id.in_(mission_ids),
                    Event.aggregate_id.in_(mission_ids),
                )
            )
        )
        if any(_contains_identifier(event.payload, identifiers) for event in events):
            conflicts.add("events.payload")

        evidence = session.scalars(
            select(PriceObservation.raw_evidence)
            .join(CollectionRun, PriceObservation.collection_run_id == CollectionRun.id)
            .where(
                CollectionRun.mission_id.in_(mission_ids),
                PriceObservation.raw_evidence.is_not(None),
            )
        )
        if any(_contains_identifier(item, identifiers) for item in evidence):
            conflicts.add("price_observations.raw_evidence")
    return tuple(sorted(conflicts))


def _contains_identifier(value: Any, identifiers: tuple[Any, ...]) -> bool:
    serialized = json.dumps(value, ensure_ascii=False, default=str).casefold()
    return any(str(identifier).casefold() in serialized for identifier in identifiers)


def _is_deidentified(user: User) -> bool:
    return (
        not user.is_active
        and user.telegram_user_id is None
        and user.telegram_chat_id is None
        and user.username is None
        and user.email is None
        and user.display_name == DEIDENTIFIED_DISPLAY_NAME
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
