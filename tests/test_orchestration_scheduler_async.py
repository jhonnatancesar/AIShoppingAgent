"""Testes com `AsyncSession` mockada (`MagicMock`/`AsyncMock`) para o
scheduler unificado do `collection_worker` (TASK-112 fase 3B):
`_select_due_work_for_batch`, `_build_claim_attempts`,
`_claim_legacy_source_attempt` e `claim_due_work`
(`app.collection.orchestration`).

Mesmo papel de `tests/test_collection_orchestration_async.py`: sinal de
regressão rápido sobre chamadas/branches/retornos com dependências
controladas -- nunca prova de corretude sob concorrência real de
Postgres, que é `tests/integration/test_unified_fair_queue.py`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.collection.cadence import CadenceConfig, CadenceDecision
from app.collection.models import (
    StoreThrottleState,
    UserCollectionQueueState,
)
from app.collection.orchestration import (
    _RUNNING_INDEX,
    ClaimedCollection,
    _build_claim_attempts,
    _claim_legacy_source_attempt,
    _ClaimAttempt,
    _ClaimedBatch,
    _select_due_work_for_batch,
    _SelectedWork,
    claim_due_work,
)
from app.missions.models import MissionCriteria, MissionSource
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _mock_async_session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    session.scalars = AsyncMock()
    session.flush = AsyncMock()
    session.begin = MagicMock(side_effect=lambda: _async_cm())
    session.begin_nested = MagicMock(side_effect=lambda: _async_cm())
    return session


def _async_cm(value=None):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _rows_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


# ---------------------------------------------------------------------------
# _select_due_work_for_batch
# ---------------------------------------------------------------------------


def test_select_due_work_legacy_only_groups_by_owner(monkeypatch) -> None:
    session = _mock_async_session()
    owner = uuid4()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._select_due_legacy_sources_for_batch",
        AsyncMock(return_value=[(source, "KABUM", owner)]),
    )
    # única query real restante: MonitoringItemStore devidos -- vazia.
    session.execute.return_value = _rows_result([])

    selected = asyncio.run(
        _select_due_work_for_batch(
            session, due_at=NOW, limit=10, max_users=5, candidate_scan_limit=1000
        )
    )

    assert selected.owner_ids == frozenset({owner})
    assert selected.old_path_sources_by_owner[owner] == ((source, "KABUM"),)
    assert selected.shared_targets_by_owner == {}
    # sem pairs -- consulta de link_rows nunca disparada, só 1 execute.
    assert session.execute.await_count == 1


def test_select_due_work_no_shared_rows_skips_link_query(monkeypatch) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._select_due_legacy_sources_for_batch",
        AsyncMock(return_value=[]),
    )
    session.execute.return_value = _rows_result([])

    selected = asyncio.run(
        _select_due_work_for_batch(
            session, due_at=NOW, limit=10, max_users=5, candidate_scan_limit=1000
        )
    )

    assert selected == _SelectedWork(
        owner_ids=frozenset(), old_path_sources_by_owner={}, shared_targets_by_owner={}
    )
    assert session.execute.await_count == 1
    session.scalars.assert_not_awaited()


def test_select_due_work_shared_path_picks_argmin_eligible_owner(monkeypatch) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._select_due_legacy_sources_for_batch",
        AsyncMock(return_value=[]),
    )
    item_id, store_id = uuid4(), uuid4()
    never_processed = uuid4()
    already_processed = uuid4()
    session.execute.side_effect = [
        _rows_result([(item_id, store_id)]),
        _rows_result(
            [
                (item_id, store_id, never_processed),
                (item_id, store_id, already_processed),
            ]
        ),
    ]
    session.scalars.return_value = [
        UserCollectionQueueState(
            user_id=already_processed, last_processed_at=NOW - timedelta(hours=1)
        )
    ]

    selected = asyncio.run(
        _select_due_work_for_batch(
            session, due_at=NOW, limit=10, max_users=5, candidate_scan_limit=1000
        )
    )

    # `never_processed` não tem estado registrado -- vence no round-robin
    # (last_processed_at IS NULL sempre ganha, ver `queue_sort_key`).
    assert selected.shared_targets_by_owner == {never_processed: ((item_id, store_id),)}
    assert selected.owner_ids == frozenset({never_processed})


def test_select_due_work_excludes_ineligible_linked_users(monkeypatch) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._select_due_legacy_sources_for_batch",
        AsyncMock(return_value=[]),
    )
    item_id, store_id = uuid4(), uuid4()
    blocked_user = uuid4()
    session.execute.side_effect = [
        _rows_result([(item_id, store_id)]),
        _rows_result([(item_id, store_id, blocked_user)]),
    ]
    session.scalars.return_value = [
        UserCollectionQueueState(
            user_id=blocked_user, next_eligible_at=NOW + timedelta(hours=1)
        )
    ]

    selected = asyncio.run(
        _select_due_work_for_batch(
            session, due_at=NOW, limit=10, max_users=5, candidate_scan_limit=1000
        )
    )

    assert selected.shared_targets_by_owner == {}
    assert selected.owner_ids == frozenset()


def test_select_due_work_max_users_cuts_least_prioritary_owners(monkeypatch) -> None:
    session = _mock_async_session()
    winner = uuid4()
    loser = uuid4()
    source_winner = MissionSource(mission_id=uuid4(), store_id=uuid4())
    source_loser = MissionSource(mission_id=uuid4(), store_id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._select_due_legacy_sources_for_batch",
        AsyncMock(
            return_value=[
                (source_winner, "KABUM", winner),
                (source_loser, "PICHAU", loser),
            ]
        ),
    )
    session.execute.return_value = _rows_result([])
    session.scalars.return_value = [
        UserCollectionQueueState(
            user_id=winner, last_processed_at=NOW - timedelta(days=1)
        ),
        UserCollectionQueueState(
            user_id=loser, last_processed_at=NOW - timedelta(minutes=1)
        ),
    ]

    selected = asyncio.run(
        _select_due_work_for_batch(
            session, due_at=NOW, limit=10, max_users=1, candidate_scan_limit=1000
        )
    )

    assert selected.owner_ids == frozenset({winner})
    assert loser not in selected.old_path_sources_by_owner


# ---------------------------------------------------------------------------
# _build_claim_attempts
# ---------------------------------------------------------------------------


def test_build_claim_attempts_skips_mission_without_criteria() -> None:
    session = _mock_async_session()
    owner = uuid4()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.scalar.return_value = None
    selected = _SelectedWork(
        owner_ids=frozenset({owner}),
        old_path_sources_by_owner={owner: ((source, "KABUM"),)},
        shared_targets_by_owner={},
    )

    attempts = asyncio.run(
        _build_claim_attempts(session, selected, {owner}, due_at=NOW)
    )

    assert attempts == []


def test_build_claim_attempts_skips_blank_search_query() -> None:
    session = _mock_async_session()
    owner = uuid4()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.scalar.return_value = MissionCriteria(
        mission_id=source.mission_id, search_query="   "
    )
    selected = _SelectedWork(
        owner_ids=frozenset({owner}),
        old_path_sources_by_owner={owner: ((source, "KABUM"),)},
        shared_targets_by_owner={},
    )

    attempts = asyncio.run(
        _build_claim_attempts(session, selected, {owner}, due_at=NOW)
    )

    assert attempts == []


def test_build_claim_attempts_caches_criteria_per_mission() -> None:
    session = _mock_async_session()
    owner = uuid4()
    mission_id = uuid4()
    source_a = MissionSource(mission_id=mission_id, store_id=uuid4(), next_run_at=NOW)
    source_b = MissionSource(mission_id=mission_id, store_id=uuid4(), next_run_at=NOW)
    session.scalar.return_value = MissionCriteria(
        mission_id=mission_id, search_query="rtx 4070", model="RTX 4070"
    )
    selected = _SelectedWork(
        owner_ids=frozenset({owner}),
        old_path_sources_by_owner={owner: ((source_a, "KABUM"), (source_b, "PICHAU"))},
        shared_targets_by_owner={},
    )

    attempts = asyncio.run(
        _build_claim_attempts(session, selected, {owner}, due_at=NOW)
    )

    assert len(attempts) == 2
    assert session.scalar.await_count == 1
    assert {a.store_id for a in attempts} == {source_a.store_id, source_b.store_id}
    assert all(a.search_query == "rtx 4070" and a.model == "RTX 4070" for a in attempts)


def test_build_claim_attempts_falls_back_to_due_at_when_next_run_at_is_none() -> None:
    session = _mock_async_session()
    owner = uuid4()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.scalar.return_value = MissionCriteria(
        mission_id=source.mission_id, search_query="ryzen 9"
    )
    selected = _SelectedWork(
        owner_ids=frozenset({owner}),
        old_path_sources_by_owner={owner: ((source, "KABUM"),)},
        shared_targets_by_owner={},
    )

    attempts = asyncio.run(
        _build_claim_attempts(session, selected, {owner}, due_at=NOW)
    )

    assert attempts[0].due_at == NOW


def test_build_claim_attempts_ignores_owners_not_reserved() -> None:
    session = _mock_async_session()
    reserved_owner = uuid4()
    dropped_owner = uuid4()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=NOW)
    session.scalar.return_value = MissionCriteria(
        mission_id=source.mission_id, search_query="ssd nvme"
    )
    selected = _SelectedWork(
        owner_ids=frozenset({reserved_owner, dropped_owner}),
        old_path_sources_by_owner={dropped_owner: ((source, "KABUM"),)},
        shared_targets_by_owner={},
    )

    attempts = asyncio.run(
        _build_claim_attempts(session, selected, {reserved_owner}, due_at=NOW)
    )

    assert attempts == []
    session.scalar.assert_not_awaited()


def test_build_claim_attempts_includes_shared_targets_and_sorts_global_order() -> None:
    session = _mock_async_session()
    owner = uuid4()
    early_store = uuid4()
    late_store = uuid4()
    item_id = uuid4()
    source = MissionSource(mission_id=uuid4(), store_id=late_store, next_run_at=NOW)
    session.scalar.return_value = MissionCriteria(
        mission_id=source.mission_id, search_query="mouse gamer"
    )
    selected = _SelectedWork(
        owner_ids=frozenset({owner}),
        old_path_sources_by_owner={owner: ((source, "KABUM"),)},
        shared_targets_by_owner={owner: [(item_id, early_store)]},
    )

    attempts = asyncio.run(
        _build_claim_attempts(session, selected, {owner}, due_at=NOW)
    )

    assert len(attempts) == 2
    expected_order = sorted(
        attempts, key=lambda a: (str(a.store_id), a.due_at, a.kind, a.resource_id)
    )
    assert attempts == expected_order
    assert {a.store_id for a in attempts} == {early_store, late_store}
    shared_attempt = next(a for a in attempts if a.kind == "shared")
    assert shared_attempt.monitoring_item_id == item_id
    assert shared_attempt.resource_id == f"{item_id}:{early_store}"


# ---------------------------------------------------------------------------
# _claim_legacy_source_attempt
# ---------------------------------------------------------------------------


def _attempt(*, mission_id=None, store_id=None) -> _ClaimAttempt:
    return _ClaimAttempt(
        kind="legacy_store",
        owner_user_id=uuid4(),
        store_id=store_id or uuid4(),
        due_at=NOW,
        resource_id="r",
        mission_id=mission_id or uuid4(),
        source_code="KABUM",
        search_query="rtx 4070",
        model="RTX 4070",
    )


def test_claim_legacy_attempt_returns_none_when_source_vanished() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    result = asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
        )
    )

    assert result is None


def test_claim_legacy_attempt_returns_none_when_next_run_at_is_still_future() -> None:
    session = _mock_async_session()
    session.get.return_value = MissionSource(
        mission_id=uuid4(), store_id=uuid4(), next_run_at=NOW + timedelta(minutes=5)
    )

    result = asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
        )
    )

    assert result is None


def test_claim_legacy_attempt_returns_none_when_next_eligible_at_is_still_future() -> (
    None
):
    session = _mock_async_session()
    session.get.return_value = MissionSource(
        mission_id=uuid4(),
        store_id=uuid4(),
        next_run_at=None,
        next_eligible_at=NOW + timedelta(minutes=5),
    )

    result = asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
        )
    )

    assert result is None


def test_claim_legacy_attempt_returns_none_when_store_throttle_still_active(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.get.side_effect = [
        source,
        StoreThrottleState(
            store_id=source.store_id, next_allowed_at=NOW + timedelta(seconds=1)
        ),
    ]

    result = asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(mission_id=source.mission_id, store_id=source.store_id),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
        )
    )

    assert result is None


def test_claim_legacy_attempt_returns_none_on_running_index_conflict(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.get.side_effect = [source, None]
    error = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda err: _RUNNING_INDEX,
    )
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(side_effect=error),
    )

    result = asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(mission_id=source.mission_id, store_id=source.store_id),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
        )
    )

    assert result is None


def test_claim_legacy_attempt_reraises_unrelated_integrity_error(monkeypatch) -> None:
    session = _mock_async_session()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.get.side_effect = [source, None]
    error = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda err: "some_other_constraint",
    )
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(side_effect=error),
    )

    try:
        asyncio.run(
            _claim_legacy_source_attempt(
                session,
                _attempt(mission_id=source.mission_id, store_id=source.store_id),
                effective_now=NOW,
                store_min_interval_seconds=2.0,
                cadence_config=CadenceConfig(),
            )
        )
    except IntegrityError as caught:
        assert caught is error
    else:
        raise AssertionError("deveria propagar IntegrityError não relacionada")


def test_claim_legacy_attempt_high_activity_appends_notify_flag(monkeypatch) -> None:
    session = _mock_async_session()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.get.side_effect = [source, None]
    run = MagicMock(id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(return_value=run),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_collection_cadence",
        AsyncMock(
            return_value=CadenceDecision(
                min_minutes=30, max_minutes=45, mode="high_activity"
            )
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.sample_next_run_at",
        lambda started_at, decision: started_at + timedelta(minutes=30),
    )
    advance_throttle = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._advance_store_throttle", advance_throttle
    )
    notify: list[bool] = []

    result = asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(mission_id=source.mission_id, store_id=source.store_id),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
            high_activity_notify=notify,
        )
    )

    assert notify == [True]
    assert isinstance(result, ClaimedCollection)
    assert result.run_id == run.id
    assert source.last_run_at == NOW
    assert source.next_run_at == NOW + timedelta(minutes=30)
    advance_throttle.assert_awaited_once_with(
        session, store_id=source.store_id, claimed_at=NOW, min_interval_seconds=2.0
    )


def test_claim_legacy_attempt_normal_mode_never_touches_notify_list(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.get.side_effect = [source, None]
    run = MagicMock(id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(return_value=run),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_collection_cadence",
        AsyncMock(
            return_value=CadenceDecision(min_minutes=45, max_minutes=75, mode="normal")
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.sample_next_run_at",
        lambda started_at, decision: started_at + timedelta(minutes=60),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._advance_store_throttle", AsyncMock()
    )
    notify: list[bool] = []

    asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(mission_id=source.mission_id, store_id=source.store_id),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
            high_activity_notify=notify,
        )
    )

    assert notify == []


def test_claim_legacy_attempt_high_activity_without_notify_list_does_not_crash(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4(), next_run_at=None)
    session.get.side_effect = [source, None]
    run = MagicMock(id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(return_value=run),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_collection_cadence",
        AsyncMock(
            return_value=CadenceDecision(
                min_minutes=30, max_minutes=45, mode="high_activity"
            )
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.sample_next_run_at",
        lambda started_at, decision: started_at + timedelta(minutes=30),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._advance_store_throttle", AsyncMock()
    )

    result = asyncio.run(
        _claim_legacy_source_attempt(
            session,
            _attempt(mission_id=source.mission_id, store_id=source.store_id),
            effective_now=NOW,
            store_min_interval_seconds=2.0,
            cadence_config=CadenceConfig(),
            high_activity_notify=None,
        )
    )

    assert isinstance(result, ClaimedCollection)


# ---------------------------------------------------------------------------
# claim_due_work
# ---------------------------------------------------------------------------


def _patch_scheduler_collaborators(
    monkeypatch,
    *,
    selected: _SelectedWork | None = None,
    reserved_owners: set | None = None,
    attempts: list[_ClaimAttempt] | None = None,
    legacy_result=None,
    shared_result=None,
):
    monkeypatch.setattr(
        "app.collection.orchestration._select_due_work_for_batch",
        AsyncMock(
            return_value=selected
            or _SelectedWork(
                owner_ids=frozenset(),
                old_path_sources_by_owner={},
                shared_targets_by_owner={},
            )
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reserve_fairness_owners",
        AsyncMock(return_value=reserved_owners or set()),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._build_claim_attempts",
        AsyncMock(return_value=attempts or []),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._claim_legacy_source_attempt",
        AsyncMock(return_value=legacy_result),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._claim_shared_collection_in_session",
        AsyncMock(return_value=shared_result),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._commit_fairness_turn_for_owner",
        AsyncMock(),
    )


def test_claim_due_work_returns_empty_batch_when_no_owner_reserved(monkeypatch) -> None:
    session = _mock_async_session()
    owner = uuid4()
    _patch_scheduler_collaborators(
        monkeypatch,
        selected=_SelectedWork(
            owner_ids=frozenset({owner}),
            old_path_sources_by_owner={},
            shared_targets_by_owner={},
        ),
        reserved_owners=set(),
    )
    build_attempts = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._build_claim_attempts", build_attempts
    )

    batch = asyncio.run(claim_due_work(session, now=NOW))

    assert batch == _ClaimedBatch(old_path=(), shared=())
    build_attempts.assert_not_awaited()
    session.flush.assert_not_awaited()


def test_claim_due_work_legacy_claim_success_refreshes_aggregate_and_commits_turn(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    owner = uuid4()
    mission_id = uuid4()
    attempt = _ClaimAttempt(
        kind="legacy_store",
        owner_user_id=owner,
        store_id=uuid4(),
        due_at=NOW,
        resource_id="r",
        mission_id=mission_id,
        source_code="KABUM",
        search_query="rtx 4070",
        model="RTX 4070",
    )
    claimed = ClaimedCollection(
        uuid4(), mission_id, attempt.store_id, "KABUM", "rtx 4070", NOW, "RTX 4070"
    )
    refresh_mock = AsyncMock()
    commit_turn_mock = AsyncMock()
    _patch_scheduler_collaborators(
        monkeypatch,
        reserved_owners={owner},
        attempts=[attempt],
        legacy_result=claimed,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate",
        refresh_mock,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._commit_fairness_turn_for_owner",
        commit_turn_mock,
    )

    batch = asyncio.run(claim_due_work(session, now=NOW))

    assert batch.old_path == (claimed,)
    assert batch.shared == ()
    refresh_mock.assert_awaited_once_with(session, mission_id=mission_id, now=NOW)
    commit_turn_mock.assert_awaited_once()
    session.flush.assert_awaited_once()


def test_claim_due_work_lost_race_never_refreshes_or_commits_turn(monkeypatch) -> None:
    session = _mock_async_session()
    owner = uuid4()
    attempt = _ClaimAttempt(
        kind="legacy_store",
        owner_user_id=owner,
        store_id=uuid4(),
        due_at=NOW,
        resource_id="r",
        mission_id=uuid4(),
        source_code="KABUM",
        search_query="rtx 4070",
        model="RTX 4070",
    )
    refresh_mock = AsyncMock()
    commit_turn_mock = AsyncMock()
    _patch_scheduler_collaborators(
        monkeypatch, reserved_owners={owner}, attempts=[attempt], legacy_result=None
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate",
        refresh_mock,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._commit_fairness_turn_for_owner",
        commit_turn_mock,
    )

    batch = asyncio.run(claim_due_work(session, now=NOW))

    assert batch == _ClaimedBatch(old_path=(), shared=())
    refresh_mock.assert_not_awaited()
    commit_turn_mock.assert_not_awaited()


def test_claim_due_work_shared_claim_success_commits_turn_but_no_aggregate_refresh(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    owner = uuid4()
    item_id, store_id = uuid4(), uuid4()
    attempt = _ClaimAttempt(
        kind="shared",
        owner_user_id=owner,
        store_id=store_id,
        due_at=NOW,
        resource_id=f"{item_id}:{store_id}",
        monitoring_item_id=item_id,
    )
    shared_claim = MagicMock()
    refresh_mock = AsyncMock()
    commit_turn_mock = AsyncMock()
    _patch_scheduler_collaborators(
        monkeypatch,
        reserved_owners={owner},
        attempts=[attempt],
        shared_result=shared_claim,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate",
        refresh_mock,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._commit_fairness_turn_for_owner",
        commit_turn_mock,
    )

    batch = asyncio.run(claim_due_work(session, now=NOW))

    assert batch.shared == (shared_claim,)
    assert batch.old_path == ()
    refresh_mock.assert_not_awaited()
    commit_turn_mock.assert_awaited_once()


def test_claim_due_work_without_settings_never_reports_high_activity(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    owner = uuid4()
    attempt = _ClaimAttempt(
        kind="legacy_store",
        owner_user_id=owner,
        store_id=uuid4(),
        due_at=NOW,
        resource_id="r",
        mission_id=uuid4(),
        source_code="KABUM",
        search_query="rtx 4070",
        model="RTX 4070",
    )

    async def _fake_claim(*_args, high_activity_notify=None, **_kwargs):
        assert high_activity_notify is None
        return ClaimedCollection(
            uuid4(),
            attempt.mission_id,
            attempt.store_id,
            "KABUM",
            "rtx 4070",
            NOW,
            "RTX 4070",
        )

    _patch_scheduler_collaborators(
        monkeypatch, reserved_owners={owner}, attempts=[attempt]
    )
    monkeypatch.setattr(
        "app.collection.orchestration._claim_legacy_source_attempt", _fake_claim
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._commit_fairness_turn_for_owner", AsyncMock()
    )

    batch = asyncio.run(claim_due_work(session, now=NOW, settings=None))

    assert batch.high_activity_detected is False


def test_claim_due_work_with_settings_propagates_high_activity_flag(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    owner = uuid4()
    attempt = _ClaimAttempt(
        kind="legacy_store",
        owner_user_id=owner,
        store_id=uuid4(),
        due_at=NOW,
        resource_id="r",
        mission_id=uuid4(),
        source_code="KABUM",
        search_query="rtx 4070",
        model="RTX 4070",
    )

    async def _fake_claim(*_args, high_activity_notify=None, **_kwargs):
        assert high_activity_notify == []
        high_activity_notify.append(True)
        return ClaimedCollection(
            uuid4(),
            attempt.mission_id,
            attempt.store_id,
            "KABUM",
            "rtx 4070",
            NOW,
            "RTX 4070",
        )

    fake_settings = MagicMock()
    _patch_scheduler_collaborators(
        monkeypatch, reserved_owners={owner}, attempts=[attempt]
    )
    monkeypatch.setattr(
        "app.collection.orchestration._claim_legacy_source_attempt", _fake_claim
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._commit_fairness_turn_for_owner", AsyncMock()
    )

    batch = asyncio.run(claim_due_work(session, now=NOW, settings=fake_settings))

    assert batch.high_activity_detected is True


def test_claim_due_work_deduplicates_aggregate_refresh_per_mission(monkeypatch) -> None:
    session = _mock_async_session()
    owner = uuid4()
    mission_id = uuid4()
    attempt_a = _ClaimAttempt(
        kind="legacy_store",
        owner_user_id=owner,
        store_id=uuid4(),
        due_at=NOW,
        resource_id="a",
        mission_id=mission_id,
        source_code="KABUM",
        search_query="rtx 4070",
        model="RTX 4070",
    )
    attempt_b = _ClaimAttempt(
        kind="legacy_store",
        owner_user_id=owner,
        store_id=uuid4(),
        due_at=NOW,
        resource_id="b",
        mission_id=mission_id,
        source_code="PICHAU",
        search_query="rtx 4070",
        model="RTX 4070",
    )
    refresh_mock = AsyncMock()
    claimed_a = ClaimedCollection(
        uuid4(), mission_id, attempt_a.store_id, "KABUM", "rtx 4070", NOW, "RTX 4070"
    )
    claimed_b = ClaimedCollection(
        uuid4(), mission_id, attempt_b.store_id, "PICHAU", "rtx 4070", NOW, "RTX 4070"
    )
    _patch_scheduler_collaborators(
        monkeypatch, reserved_owners={owner}, attempts=[attempt_a, attempt_b]
    )
    claim_mock = AsyncMock(side_effect=[claimed_a, claimed_b])
    monkeypatch.setattr(
        "app.collection.orchestration._claim_legacy_source_attempt", claim_mock
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate", refresh_mock
    )
    monkeypatch.setattr(
        "app.collection.orchestration._commit_fairness_turn_for_owner", AsyncMock()
    )

    batch = asyncio.run(claim_due_work(session, now=NOW))

    assert len(batch.old_path) == 2
    refresh_mock.assert_awaited_once_with(session, mission_id=mission_id, now=NOW)
