"""Testes unitários (`AsyncSession` mockada) de `app.collection.shared_
claim` (TASK-112, fases 3A/3B) -- mesmo padrão já estabelecido em
`tests/test_shared_collection_async.py`: `MagicMock`/`AsyncMock`
posicional configurado via `side_effect`/`return_value`, verificando
comportamento real (branches, chamadas mockadas, exceções) -- nenhum
teste raso de execução de linha. Sinal de regressão rápido, não prova de
corretude sob concorrência real (essa prova fica para o teste de
integração equivalente contra Postgres real)."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.collection.cadence import CadenceConfig, CadenceDecision
from app.collection.shared_claim import (
    V1_SOURCE_CODES,
    _advance_monitoring_item_store,
    _apply_shared_backoff,
    _claim_shared_collection,
    _claim_shared_collection_in_session,
    _constraint_name,
    _reset_shared_backoff,
    _SharedClaim,
)
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _async_cm(value=None):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value if value is not None else cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


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


def _session_factory(session: MagicMock) -> MagicMock:
    return MagicMock(return_value=session)


def _fake_scalars_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


# ---------------------------------------------------------------------------
# _constraint_name
# ---------------------------------------------------------------------------


def test_constraint_name_extracts_from_diag() -> None:
    error = MagicMock(spec=IntegrityError)
    error.orig = SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_x"))

    assert _constraint_name(error) == "uq_x"


def test_constraint_name_none_when_orig_missing() -> None:
    error = MagicMock(spec=IntegrityError)
    error.orig = None

    assert _constraint_name(error) is None


def test_constraint_name_none_when_diag_missing() -> None:
    error = MagicMock(spec=IntegrityError)
    error.orig = SimpleNamespace()

    assert _constraint_name(error) is None


# ---------------------------------------------------------------------------
# _advance_monitoring_item_store
# ---------------------------------------------------------------------------


def test_advance_monitoring_item_store_updates_schedule(monkeypatch) -> None:
    session = _mock_async_session()
    mission_id = uuid4()
    session.scalars.return_value = _fake_scalars_result([mission_id])
    decision = CadenceDecision(45, 75, "normal")
    resolve_cadence = AsyncMock(return_value=decision)
    monkeypatch.setattr(
        "app.collection.shared_claim.resolve_collection_cadence", resolve_cadence
    )
    monkeypatch.setattr(
        "app.collection.shared_claim.sample_next_run_at",
        lambda started_at, decision: started_at + timedelta(minutes=50),
    )
    item_store = SimpleNamespace(
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
        last_run_at=None,
        next_run_at=None,
        updated_at=None,
    )

    asyncio.run(
        _advance_monitoring_item_store(
            session, item_store, started_at=NOW, cadence_config=CadenceConfig()
        )
    )

    assert item_store.last_run_at == NOW
    assert item_store.next_run_at == NOW + timedelta(minutes=50)
    assert item_store.updated_at == NOW
    resolve_cadence.assert_awaited_once()
    assert (
        resolve_cadence.await_args.kwargs["scope_id"] == item_store.monitoring_item_id
    )
    assert resolve_cadence.await_args.kwargs["mission_ids"] == [mission_id]


def test_advance_monitoring_item_store_signals_high_activity_when_notify_list_given(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    session.scalars.return_value = _fake_scalars_result([])
    monkeypatch.setattr(
        "app.collection.shared_claim.resolve_collection_cadence",
        AsyncMock(return_value=CadenceDecision(30, 45, "high_activity")),
    )
    monkeypatch.setattr(
        "app.collection.shared_claim.sample_next_run_at",
        lambda started_at, decision: started_at,
    )
    item_store = SimpleNamespace(
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
        last_run_at=None,
        next_run_at=None,
        updated_at=None,
    )
    notify: list[bool] = []

    asyncio.run(
        _advance_monitoring_item_store(
            session,
            item_store,
            started_at=NOW,
            cadence_config=CadenceConfig(),
            high_activity_notify=notify,
        )
    )

    assert notify == [True]


def test_advance_monitoring_item_store_does_not_signal_when_notify_list_absent(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    session.scalars.return_value = _fake_scalars_result([])
    monkeypatch.setattr(
        "app.collection.shared_claim.resolve_collection_cadence",
        AsyncMock(return_value=CadenceDecision(30, 45, "high_activity")),
    )
    monkeypatch.setattr(
        "app.collection.shared_claim.sample_next_run_at",
        lambda started_at, decision: started_at,
    )
    item_store = SimpleNamespace(
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
        last_run_at=None,
        next_run_at=None,
        updated_at=None,
    )

    asyncio.run(
        _advance_monitoring_item_store(
            session,
            item_store,
            started_at=NOW,
            cadence_config=CadenceConfig(),
            high_activity_notify=None,
        )
    )
    # não levanta exceção mesmo sob HIGH_ACTIVITY -- comportamento
    # esperado, cobre a ausência do parâmetro opcional


def test_advance_monitoring_item_store_does_not_signal_under_normal_mode(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    session.scalars.return_value = _fake_scalars_result([])
    monkeypatch.setattr(
        "app.collection.shared_claim.resolve_collection_cadence",
        AsyncMock(return_value=CadenceDecision(45, 75, "normal")),
    )
    monkeypatch.setattr(
        "app.collection.shared_claim.sample_next_run_at",
        lambda started_at, decision: started_at,
    )
    item_store = SimpleNamespace(
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
        last_run_at=None,
        next_run_at=None,
        updated_at=None,
    )
    notify: list[bool] = []

    asyncio.run(
        _advance_monitoring_item_store(
            session,
            item_store,
            started_at=NOW,
            cadence_config=CadenceConfig(),
            high_activity_notify=notify,
        )
    )

    assert notify == []


# ---------------------------------------------------------------------------
# _apply_shared_backoff
# ---------------------------------------------------------------------------


def test_apply_shared_backoff_returns_early_when_item_store_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(
        _apply_shared_backoff(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            base_interval_minutes=60,
            failed_at=NOW,
        )
    )

    session.get.assert_awaited_once()


def test_apply_shared_backoff_advances_consecutive_blocks(monkeypatch) -> None:
    session = _mock_async_session()
    item_store = SimpleNamespace(
        consecutive_blocks=1, next_eligible_at=None, updated_at=None
    )
    session.get.return_value = item_store
    monkeypatch.setattr(
        "app.collection.shared_claim.next_source_backoff",
        lambda *, base_interval_minutes, consecutive_blocks, cap_minutes: (
            consecutive_blocks + 1,
            30,
        ),
    )

    asyncio.run(
        _apply_shared_backoff(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            base_interval_minutes=60,
            failed_at=NOW,
        )
    )

    assert item_store.consecutive_blocks == 2
    assert item_store.next_eligible_at == NOW + timedelta(minutes=30)
    assert item_store.updated_at == NOW


# ---------------------------------------------------------------------------
# _reset_shared_backoff
# ---------------------------------------------------------------------------


def test_reset_shared_backoff_returns_early_when_item_store_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(
        _reset_shared_backoff(
            session, monitoring_item_id=uuid4(), store_id=uuid4(), now=NOW
        )
    )

    session.get.assert_awaited_once()


def test_reset_shared_backoff_is_noop_when_already_reset() -> None:
    session = _mock_async_session()
    item_store = SimpleNamespace(
        consecutive_blocks=0, next_eligible_at=None, updated_at=None
    )
    session.get.return_value = item_store

    asyncio.run(
        _reset_shared_backoff(
            session, monitoring_item_id=uuid4(), store_id=uuid4(), now=NOW
        )
    )

    assert item_store.updated_at is None


def test_reset_shared_backoff_clears_state_when_needed() -> None:
    session = _mock_async_session()
    item_store = SimpleNamespace(
        consecutive_blocks=3,
        next_eligible_at=NOW + timedelta(minutes=10),
        updated_at=None,
    )
    session.get.return_value = item_store

    asyncio.run(
        _reset_shared_backoff(
            session, monitoring_item_id=uuid4(), store_id=uuid4(), now=NOW
        )
    )

    assert item_store.consecutive_blocks == 0
    assert item_store.next_eligible_at is None
    assert item_store.updated_at == NOW


# ---------------------------------------------------------------------------
# _claim_shared_collection_in_session
# ---------------------------------------------------------------------------


def _active_store(*, code: str = "pichau") -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), is_active=True, code=code)


def _enabled_item_store(
    *, next_run_at=None, next_eligible_at=None, is_enabled=True
) -> SimpleNamespace:
    return SimpleNamespace(
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
        is_enabled=is_enabled,
        next_run_at=next_run_at,
        next_eligible_at=next_eligible_at,
    )


def test_claim_shared_collection_in_session_returns_none_when_item_missing() -> None:
    session = _mock_async_session()
    session.get.side_effect = [None, _active_store()]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_store_missing() -> None:
    session = _mock_async_session()
    session.get.side_effect = [SimpleNamespace(canonical_identity={}), None]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_store_inactive() -> None:
    session = _mock_async_session()
    store = _active_store()
    store.is_active = False
    session.get.side_effect = [SimpleNamespace(canonical_identity={}), store]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_store_not_v1() -> None:
    session = _mock_async_session()
    store = _active_store(code="shopee")
    assert store.code not in V1_SOURCE_CODES
    session.get.side_effect = [SimpleNamespace(canonical_identity={}), store]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_item_store_missing() -> (
    None
):
    session = _mock_async_session()
    store = _active_store()
    session.get.side_effect = [SimpleNamespace(canonical_identity={}), store, None]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_disabled() -> None:
    session = _mock_async_session()
    store = _active_store()
    item_store = _enabled_item_store(is_enabled=False)
    session.get.side_effect = [
        SimpleNamespace(canonical_identity={}),
        store,
        item_store,
    ]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_not_due_yet() -> None:
    session = _mock_async_session()
    store = _active_store()
    item_store = _enabled_item_store(next_run_at=NOW + timedelta(minutes=5))
    session.get.side_effect = [
        SimpleNamespace(canonical_identity={}),
        store,
        item_store,
    ]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_backoff_still_active() -> (
    None
):
    session = _mock_async_session()
    store = _active_store()
    item_store = _enabled_item_store(next_eligible_at=NOW + timedelta(minutes=5))
    session.get.side_effect = [
        SimpleNamespace(canonical_identity={}),
        store,
        item_store,
    ]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_when_store_throttled() -> None:
    session = _mock_async_session()
    store = _active_store()
    item_store = _enabled_item_store()
    throttle = SimpleNamespace(next_allowed_at=NOW + timedelta(seconds=1))
    session.get.side_effect = [
        SimpleNamespace(canonical_identity={}),
        store,
        item_store,
        throttle,
    ]

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_returns_none_on_unique_violation(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    store = _active_store()
    item_store = _enabled_item_store()
    session.get.side_effect = [
        SimpleNamespace(canonical_identity={}),
        store,
        item_store,
        None,
    ]
    error = IntegrityError("x", {}, Exception())
    monkeypatch.setattr(
        "app.collection.shared_claim.start_collection_run",
        AsyncMock(side_effect=error),
    )
    monkeypatch.setattr(
        "app.collection.shared_claim._constraint_name",
        lambda err: "uq_collection_runs_running_monitoring_item_store",
    )

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
        )
    )

    assert result is None


def test_claim_shared_collection_in_session_reraises_unrelated_integrity_error(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    store = _active_store()
    item_store = _enabled_item_store()
    session.get.side_effect = [
        SimpleNamespace(canonical_identity={}),
        store,
        item_store,
        None,
    ]
    error = IntegrityError("x", {}, Exception())
    monkeypatch.setattr(
        "app.collection.shared_claim.start_collection_run",
        AsyncMock(side_effect=error),
    )
    monkeypatch.setattr(
        "app.collection.shared_claim._constraint_name", lambda err: "some_other_index"
    )

    with pytest.raises(IntegrityError):
        asyncio.run(
            _claim_shared_collection_in_session(
                session,
                monitoring_item_id=uuid4(),
                store_id=uuid4(),
                now=NOW,
                cadence_config=CadenceConfig(),
                store_min_interval_seconds=2.0,
            )
        )


def test_claim_shared_collection_in_session_success_returns_shared_claim(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monitoring_item_id = uuid4()
    store_id = uuid4()
    item = SimpleNamespace(canonical_identity={"category": "gpu"})
    store = _active_store()
    item_store = _enabled_item_store()
    session.get.side_effect = [item, store, item_store, None]
    run = SimpleNamespace(id=uuid4(), fairness_owner_user_id=None)
    start_run = AsyncMock(return_value=run)
    monkeypatch.setattr("app.collection.shared_claim.start_collection_run", start_run)
    advance_item = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_claim._advance_monitoring_item_store", advance_item
    )
    advance_throttle = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_claim._advance_store_throttle", advance_throttle
    )
    criteria = object()
    monkeypatch.setattr(
        "app.collection.shared_claim.canonical_collection_criteria",
        lambda identity: criteria,
    )
    owner_id = uuid4()

    result = asyncio.run(
        _claim_shared_collection_in_session(
            session,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            now=NOW,
            cadence_config=CadenceConfig(),
            store_min_interval_seconds=2.0,
            fairness_owner_user_id=owner_id,
        )
    )

    assert result == _SharedClaim(
        run_id=run.id,
        criteria=criteria,
        store_code=store.code,
        monitoring_item_id=monitoring_item_id,
        store_id=store_id,
    )
    assert run.fairness_owner_user_id == owner_id
    advance_item.assert_awaited_once()
    advance_throttle.assert_awaited_once_with(
        session, store_id=store_id, claimed_at=NOW, min_interval_seconds=2.0
    )


# ---------------------------------------------------------------------------
# _claim_shared_collection (wrapper standalone)
# ---------------------------------------------------------------------------


def test_claim_shared_collection_delegates_to_in_session_with_own_transaction(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    factory = _session_factory(session)
    claim = _SharedClaim(
        run_id=uuid4(),
        criteria=object(),
        store_code="pichau",
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
    )
    in_session = AsyncMock(return_value=claim)
    monkeypatch.setattr(
        "app.collection.shared_claim._claim_shared_collection_in_session", in_session
    )

    result = asyncio.run(
        _claim_shared_collection(
            factory,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
        )
    )

    assert result is claim
    in_session.assert_awaited_once()
    assert isinstance(in_session.await_args.kwargs["cadence_config"], CadenceConfig)


def test_claim_shared_collection_uses_provided_cadence_config(monkeypatch) -> None:
    session = _mock_async_session()
    factory = _session_factory(session)
    in_session = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.shared_claim._claim_shared_collection_in_session", in_session
    )
    config = CadenceConfig(normal_min_minutes=50, normal_max_minutes=80)

    result = asyncio.run(
        _claim_shared_collection(
            factory,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            cadence_config=config,
        )
    )

    assert result is None
    assert in_session.await_args.kwargs["cadence_config"] is config
