"""Valida a TASK-041 contra PostgreSQL real em banco temporário isolado."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Lock
from uuid import uuid4

import app.purchase.confirmation as confirmation_module
import app.purchase.trail as trail_module
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability
from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.missions.models import Mission, MissionCriteria, MissionSource, MissionStatus
from app.offers.models import Offer
from app.products.models import Product
from app.purchase import (
    PurchaseConfirmationConflictError,
    PurchaseConfirmationDecision,
    PurchaseConfirmationStaleReason,
    PurchaseConfirmationStatus,
    list_purchase_trail_entries,
    recover_purchase_confirmation,
    request_purchase_confirmation,
    resolve_purchase_confirmation,
)
from app.purchase.models import (
    PurchaseConfirmation,
    PurchaseTrailEntry,
    PurchaseTrailEntryType,
)
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError


def validate() -> None:
    engine = create_database_engine(Settings())
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    user_id, mission_id, offer_id, run_id = _seed(session_factory, now)

    _validate_atomic_creation(session_factory, mission_id, offer_id, user_id, now)
    _validate_confirmation_recovery_and_idempotency(
        session_factory, mission_id, offer_id, user_id, now
    )
    _validate_expiration_precedence(session_factory, mission_id, offer_id, user_id, now)
    _validate_expired_cancel(session_factory, mission_id, offer_id, user_id, now)
    _validate_equivalent_new_observation(
        session_factory, mission_id, offer_id, run_id, user_id, now
    )
    _validate_changed_evidence(
        session_factory, mission_id, offer_id, run_id, user_id, now
    )
    _validate_unrelated_integrity_propagates(
        session_factory, mission_id, offer_id, user_id, now
    )
    _validate_identical_concurrency(session_factory, mission_id, offer_id, user_id, now)
    _validate_conflicting_concurrency(
        session_factory, mission_id, offer_id, user_id, now
    )
    _validate_database_guarantees(session_factory, mission_id, user_id)

    with session_factory() as session:
        trail = list_purchase_trail_entries(session, mission_id, owner_user_id=user_id)
        requested_count = sum(
            entry.entry_type is PurchaseTrailEntryType.REQUESTED for entry in trail
        )
        terminal_count = len(trail) - requested_count
        if requested_count == 0 or terminal_count == 0:
            raise RuntimeError("mission trail query did not return persisted history")

    engine.dispose()
    print(
        "TASK-041 PostgreSQL validation passed:",
        {
            "requested_entries": requested_count,
            "terminal_entries": terminal_count,
            "atomic_creation": True,
            "immutable_triggers": True,
            "restrict_fks": True,
            "identical_race": "idempotent",
            "conflicting_race": "domain_conflict",
        },
    )


def _seed(session_factory, now: datetime):
    with session_factory.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        if store is None:
            raise RuntimeError("V1 store seed is missing")
        user = User(
            display_name="TASK-041 isolated validation",
            role=UserRole.USER,
            is_active=True,
        )
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-041 isolated validation",
            status=MissionStatus.ACTIVE,
            state_version=1,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission.id,
                    search_query="notebook",
                    target_amount=Decimal("5000"),
                    target_currency="BRL",
                ),
                MissionSource(mission_id=mission.id, store_id=store.id),
            )
        )
        product = Product(name="Notebook TASK-041", brand="Marca", model="Modelo")
        session.add(product)
        session.flush()
        offer_token = uuid4()
        offer = Offer(
            product_id=product.id,
            store_id=store.id,
            external_id=f"task-041-{offer_token}",
            url=f"{store.base_url.rstrip('/')}/task-041-{offer_token}",
        )
        session.add(offer)
        session.flush()
        run = CollectionRun(
            mission_id=mission.id,
            store_id=store.id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=now,
            finished_at=now + timedelta(seconds=1),
        )
        session.add(run)
        session.flush()
        _add_observation(session, offer.id, run.id, now, "2800", "100")
        session.flush()
        return user.id, mission.id, offer.id, run.id


def _new_request(session_factory, mission_id, offer_id, user_id, now):
    with session_factory.begin() as session:
        return request_purchase_confirmation(
            session,
            mission_id=mission_id,
            offer_id=offer_id,
            owner_user_id=user_id,
            now=now,
        )


def _validate_atomic_creation(session_factory, mission_id, offer_id, user_id, now):
    before = _confirmation_count(session_factory, mission_id)
    original = trail_module._trail_model

    def invalid_requested(*args, **kwargs):
        entry = original(*args, **kwargs)
        entry.decision = PurchaseConfirmationDecision.CONFIRM
        return entry

    trail_module._trail_model = invalid_requested
    try:
        with session_factory() as session:
            try:
                request_purchase_confirmation(
                    session,
                    mission_id=mission_id,
                    offer_id=offer_id,
                    owner_user_id=user_id,
                    now=now,
                )
            except IntegrityError:
                session.rollback()
            else:
                raise RuntimeError("invalid requested entry unexpectedly persisted")
    finally:
        trail_module._trail_model = original
    if _confirmation_count(session_factory, mission_id) != before:
        raise RuntimeError("confirmation survived failed atomic creation")


def _validate_confirmation_recovery_and_idempotency(
    session_factory, mission_id, offer_id, user_id, now
):
    request = _new_request(session_factory, mission_id, offer_id, user_id, now)
    with session_factory() as restarted_session:
        recovered = recover_purchase_confirmation(
            restarted_session,
            request.confirmation_id,
            owner_user_id=user_id,
        )
        if recovered != request:
            raise RuntimeError("confirmation was not recovered exactly after restart")

    with session_factory.begin() as session:
        first = resolve_purchase_confirmation(
            session,
            request.confirmation_id,
            owner_user_id=user_id,
            decision=PurchaseConfirmationDecision.CONFIRM,
            now=now + timedelta(minutes=1),
        )
    with session_factory.begin() as session:
        repeated = resolve_purchase_confirmation(
            session,
            request.confirmation_id,
            owner_user_id=user_id,
            decision=PurchaseConfirmationDecision.CONFIRM,
            now=now + timedelta(minutes=2),
        )
    if first.status is not PurchaseConfirmationStatus.CONFIRMED or repeated != first:
        raise RuntimeError("identical terminal repetition was not idempotent")

    with session_factory.begin() as session:
        try:
            resolve_purchase_confirmation(
                session,
                request.confirmation_id,
                owner_user_id=user_id,
                decision=PurchaseConfirmationDecision.CANCEL,
                now=now + timedelta(minutes=2),
            )
        except PurchaseConfirmationConflictError:
            pass
        else:
            raise RuntimeError("conflicting terminal was not rejected")


def _validate_expiration_precedence(
    session_factory, mission_id, offer_id, user_id, now
):
    request = _new_request(session_factory, mission_id, offer_id, user_id, now)
    original = confirmation_module.compare_offers_for_mission
    confirmation_module.compare_offers_for_mission = lambda *args, **kwargs: (
        _ for _ in ()
    ).throw(RuntimeError("expired confirm recalculated evidence"))
    try:
        with session_factory.begin() as session:
            result = resolve_purchase_confirmation(
                session,
                request.confirmation_id,
                owner_user_id=user_id,
                decision=PurchaseConfirmationDecision.CONFIRM,
                now=request.expires_at,
            )
    finally:
        confirmation_module.compare_offers_for_mission = original
    if (
        result.status is not PurchaseConfirmationStatus.STALE
        or result.stale_reason is not PurchaseConfirmationStaleReason.EXPIRED
    ):
        raise RuntimeError("expiration did not take precedence")


def _validate_expired_cancel(session_factory, mission_id, offer_id, user_id, now):
    request = _new_request(session_factory, mission_id, offer_id, user_id, now)
    with session_factory.begin() as session:
        result = resolve_purchase_confirmation(
            session,
            request.confirmation_id,
            owner_user_id=user_id,
            decision=PurchaseConfirmationDecision.CANCEL,
            now=request.expires_at + timedelta(minutes=1),
        )
    if result.status is not PurchaseConfirmationStatus.CANCELLED:
        raise RuntimeError("expired cancellation became stale")


def _validate_equivalent_new_observation(
    session_factory, mission_id, offer_id, run_id, user_id, now
):
    request = _new_request(
        session_factory, mission_id, offer_id, user_id, now + timedelta(minutes=3)
    )
    with session_factory.begin() as session:
        _add_observation(
            session,
            offer_id,
            run_id,
            now + timedelta(minutes=4),
            "2800",
            "100",
        )
    with session_factory.begin() as session:
        result = resolve_purchase_confirmation(
            session,
            request.confirmation_id,
            owner_user_id=user_id,
            decision=PurchaseConfirmationDecision.CONFIRM,
            now=now + timedelta(minutes=5),
        )
    if (
        result.status is not PurchaseConfirmationStatus.CONFIRMED
        or result.request.price_observation_id != request.price_observation_id
    ):
        raise RuntimeError("equivalent newer observation invalidated provenance")


def _validate_changed_evidence(
    session_factory, mission_id, offer_id, run_id, user_id, now
):
    request = _new_request(
        session_factory, mission_id, offer_id, user_id, now + timedelta(minutes=6)
    )
    with session_factory.begin() as session:
        _add_observation(
            session,
            offer_id,
            run_id,
            now + timedelta(minutes=7),
            "2799",
            "100",
        )
    with session_factory.begin() as session:
        result = resolve_purchase_confirmation(
            session,
            request.confirmation_id,
            owner_user_id=user_id,
            decision=PurchaseConfirmationDecision.CONFIRM,
            now=now + timedelta(minutes=8),
        )
    if (
        result.status is not PurchaseConfirmationStatus.STALE
        or result.stale_reason is not PurchaseConfirmationStaleReason.EVIDENCE_CHANGED
    ):
        raise RuntimeError("material evidence change did not become stale")


def _validate_unrelated_integrity_propagates(
    session_factory, mission_id, offer_id, user_id, now
):
    request = _new_request(
        session_factory, mission_id, offer_id, user_id, now + timedelta(minutes=9)
    )
    original = trail_module._trail_model

    def invalid_terminal(*args, **kwargs):
        entry = original(*args, **kwargs)
        if entry.entry_type is not PurchaseTrailEntryType.REQUESTED:
            entry.decision = PurchaseConfirmationDecision.CANCEL
        return entry

    trail_module._trail_model = invalid_terminal
    try:
        with session_factory() as session:
            try:
                resolve_purchase_confirmation(
                    session,
                    request.confirmation_id,
                    owner_user_id=user_id,
                    decision=PurchaseConfirmationDecision.CONFIRM,
                    now=now + timedelta(minutes=10),
                )
            except IntegrityError as exc:
                session.rollback()
                if trail_module._is_terminal_unique_violation(exc):
                    raise RuntimeError("wrong integrity error was classified as race")
            else:
                raise RuntimeError("unrelated integrity violation was swallowed")
    finally:
        trail_module._trail_model = original


def _validate_identical_concurrency(
    session_factory, mission_id, offer_id, user_id, now
):
    request = _new_request(
        session_factory, mission_id, offer_id, user_id, now + timedelta(minutes=11)
    )
    barrier = Barrier(2)
    original_evaluate = trail_module._evaluate_purchase_confirmation
    original_classifier = trail_module._is_terminal_unique_violation
    handled = 0
    lock = Lock()

    def synchronized_evaluate(*args, **kwargs):
        barrier.wait(timeout=10)
        return original_evaluate(*args, **kwargs)

    def counted_classifier(exc):
        nonlocal handled
        matched = original_classifier(exc)
        if matched:
            with lock:
                handled += 1
        return matched

    trail_module._evaluate_purchase_confirmation = synchronized_evaluate
    trail_module._is_terminal_unique_violation = counted_classifier
    try:
        results = _race(
            session_factory,
            request.confirmation_id,
            user_id,
            (PurchaseConfirmationDecision.CONFIRM,) * 2,
            now + timedelta(minutes=12),
        )
    finally:
        trail_module._evaluate_purchase_confirmation = original_evaluate
        trail_module._is_terminal_unique_violation = original_classifier
    if (
        any(isinstance(result, Exception) for result in results)
        or results[0] != results[1]
        or handled != 1
    ):
        raise RuntimeError("identical PostgreSQL race was not idempotent")


def _validate_conflicting_concurrency(
    session_factory, mission_id, offer_id, user_id, now
):
    request = _new_request(
        session_factory, mission_id, offer_id, user_id, now + timedelta(minutes=13)
    )
    barrier = Barrier(2)
    original = trail_module._evaluate_purchase_confirmation

    def synchronized_evaluate(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    trail_module._evaluate_purchase_confirmation = synchronized_evaluate
    try:
        results = _race(
            session_factory,
            request.confirmation_id,
            user_id,
            (
                PurchaseConfirmationDecision.CONFIRM,
                PurchaseConfirmationDecision.CANCEL,
            ),
            now + timedelta(minutes=14),
        )
    finally:
        trail_module._evaluate_purchase_confirmation = original
    conflicts = sum(
        isinstance(result, PurchaseConfirmationConflictError) for result in results
    )
    successes = sum(
        isinstance(result, PurchaseConfirmationStatus) for result in results
    )
    if conflicts != 1 or successes != 1:
        raise RuntimeError("conflicting PostgreSQL race was not rejected")


def _race(session_factory, confirmation_id, user_id, decisions, resolved_at):
    def resolve(decision):
        try:
            with session_factory.begin() as session:
                result = resolve_purchase_confirmation(
                    session,
                    confirmation_id,
                    owner_user_id=user_id,
                    decision=decision,
                    now=resolved_at,
                )
                return result.status
        except Exception as exc:  # validated by the caller
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        return tuple(executor.map(resolve, decisions))


def _validate_database_guarantees(session_factory, mission_id, user_id):
    with session_factory() as session:
        confirmation = session.scalar(
            select(PurchaseConfirmation)
            .where(PurchaseConfirmation.mission_id == mission_id)
            .order_by(PurchaseConfirmation.requested_at)
        )
        if confirmation is None or confirmation.recorded_at is None:
            raise RuntimeError("PostgreSQL did not set recorded_at")
        requested = session.scalar(
            select(PurchaseTrailEntry).where(
                PurchaseTrailEntry.confirmation_id == confirmation.id,
                PurchaseTrailEntry.entry_type == PurchaseTrailEntryType.REQUESTED,
            )
        )
        if requested is None or requested.recorded_at is None:
            raise RuntimeError("requested trail entry is missing")

        duplicate = PurchaseTrailEntry(
            confirmation_id=requested.confirmation_id,
            mission_id=requested.mission_id,
            owner_user_id=requested.owner_user_id,
            offer_id=requested.offer_id,
            price_observation_id=requested.price_observation_id,
            entry_type=PurchaseTrailEntryType.REQUESTED,
        )
        try:
            with session.begin_nested():
                session.add(duplicate)
                session.flush()
        except IntegrityError as exc:
            if getattr(exc.orig.diag, "constraint_name", None) != (
                "ux_purchase_trail_entries_requested_confirmation"
            ):
                raise
        else:
            raise RuntimeError("duplicate requested entry was accepted")

        for statement in (
            update(PurchaseConfirmation)
            .where(PurchaseConfirmation.id == confirmation.id)
            .values(position=2),
            delete(PurchaseTrailEntry).where(PurchaseTrailEntry.id == requested.id),
        ):
            try:
                with session.begin_nested():
                    session.execute(statement)
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) != "55000":
                    raise
            else:
                raise RuntimeError("immutable row accepted mutation")

        try:
            with session.begin_nested():
                session.execute(
                    delete(PriceObservation).where(
                        PriceObservation.id == confirmation.price_observation_id
                    )
                )
        except IntegrityError as exc:
            constraint_name = getattr(exc.orig.diag, "constraint_name", "")
            if (
                getattr(exc.orig, "sqlstate", None) != "23001"
                or "purchase_confirmations" not in constraint_name
            ):
                raise
        else:
            raise RuntimeError("RESTRICT foreign key accepted parent deletion")


def _confirmation_count(session_factory, mission_id) -> int:
    with session_factory() as session:
        return session.scalar(
            select(func.count())
            .select_from(PurchaseConfirmation)
            .where(PurchaseConfirmation.mission_id == mission_id)
        )


def _add_observation(session, offer_id, run_id, observed_at, amount, shipping):
    existing_in_run = session.scalar(
        select(PriceObservation.id).where(
            PriceObservation.collection_run_id == run_id,
            PriceObservation.offer_id == offer_id,
        )
    )
    effective_run_id = run_id
    if existing_in_run is not None:
        original_run = session.get(CollectionRun, run_id)
        if original_run is None:
            raise RuntimeError("collection run not found")
        replacement_run = CollectionRun(
            mission_id=original_run.mission_id,
            store_id=original_run.store_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=observed_at - timedelta(seconds=1),
            finished_at=observed_at,
        )
        session.add(replacement_run)
        session.flush()
        effective_run_id = replacement_run.id
    item_amount = Decimal(amount)
    shipping_amount = Decimal(shipping)
    session.add(
        PriceObservation(
            offer_id=offer_id,
            collection_run_id=effective_run_id,
            amount=item_amount,
            shipping_amount=shipping_amount,
            total_amount=item_amount + shipping_amount,
            currency="BRL",
            fulfillment="Loja",
            availability=Availability.AVAILABLE,
            observed_at=observed_at,
            raw_evidence={"validation": "TASK-041"},
        )
    )


if __name__ == "__main__":
    validate()
