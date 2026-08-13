"""Testes da edição de critérios de missão já criada (TASK-069)."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.missions.models import MissionStatus
from app.missions.service import (
    MissionEditConditionError,
    MissionNotFoundError,
    MissionVersionConflictError,
    edit_mission_criteria,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


class _FakeStore:
    def __init__(self, code: str) -> None:
        self.code = code
        self.id = uuid4()


def _mission(status: MissionStatus = MissionStatus.PAUSED, *, state_version: int = 1):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        state_version=state_version,
        updated_at=NOW - timedelta(days=1),
    )


def _criteria(*, amount=None, currency=None):
    return SimpleNamespace(
        target_amount=amount,
        target_currency=currency,
        updated_at=NOW - timedelta(days=1),
    )


def _session(*, mission=None, criteria=None, stores=None, current_store_ids=None):
    session = MagicMock()
    scalar_results = []
    if mission is not None:
        scalar_results.append(mission)
    if criteria is not None:
        scalar_results.append(criteria)
    session.scalar = AsyncMock(side_effect=scalar_results)

    scalars_results = []
    if stores is not None:
        scalars_results.append(stores)
    if current_store_ids is not None:
        scalars_results.append(current_store_ids)
    session.scalars = AsyncMock(side_effect=scalars_results)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


def test_edit_requires_target_or_sources() -> None:
    with pytest.raises(ValueError, match="target_update or source_codes"):
        asyncio.run(
            edit_mission_criteria(
                MagicMock(),
                mission_id=uuid4(),
                expected_state_version=0,
                target_update=None,
                source_codes=None,
            )
        )


def test_edit_rejects_mismatched_target_pair() -> None:
    with pytest.raises(ValueError, match="paired"):
        asyncio.run(
            edit_mission_criteria(
                MagicMock(),
                mission_id=uuid4(),
                expected_state_version=0,
                target_update=(Decimal("100"), None),
                source_codes=None,
            )
        )


def test_edit_rejects_empty_source_codes() -> None:
    with pytest.raises(MissionEditConditionError, match="ao menos uma loja"):
        asyncio.run(
            edit_mission_criteria(
                MagicMock(),
                mission_id=uuid4(),
                expected_state_version=0,
                target_update=None,
                source_codes=(),
            )
        )


def test_edit_rejects_negative_expected_state_version() -> None:
    with pytest.raises(ValueError, match="negativo"):
        asyncio.run(
            edit_mission_criteria(
                MagicMock(),
                mission_id=uuid4(),
                expected_state_version=-1,
                target_update=None,
                source_codes=("kabum",),
            )
        )


def test_edit_raises_when_mission_not_found() -> None:
    session = _session(mission=None)
    session.scalar = AsyncMock(side_effect=[None])

    with pytest.raises(MissionNotFoundError):
        asyncio.run(
            edit_mission_criteria(
                session,
                mission_id=uuid4(),
                expected_state_version=0,
                target_update=None,
                source_codes=("kabum",),
            )
        )


def test_edit_raises_on_version_conflict() -> None:
    mission = _mission(state_version=5)
    session = _session(mission=mission)

    with pytest.raises(MissionVersionConflictError):
        asyncio.run(
            edit_mission_criteria(
                session,
                mission_id=mission.id,
                expected_state_version=1,
                target_update=None,
                source_codes=("kabum",),
            )
        )


@pytest.mark.parametrize(
    "status",
    [
        MissionStatus.DRAFT,
        MissionStatus.ACTIVE,
        MissionStatus.COMPLETED,
        MissionStatus.CANCELLED,
        MissionStatus.EXPIRED,
    ],
)
def test_edit_raises_when_mission_is_not_paused(status: MissionStatus) -> None:
    mission = _mission(status=status)
    session = _session(mission=mission)

    with pytest.raises(MissionEditConditionError, match="pausada"):
        asyncio.run(
            edit_mission_criteria(
                session,
                mission_id=mission.id,
                expected_state_version=mission.state_version,
                target_update=None,
                source_codes=("kabum",),
            )
        )


def test_edit_updates_target_only() -> None:
    mission = _mission()
    criteria = _criteria(amount=Decimal("500.00"), currency="BRL")
    session = _session(mission=mission, criteria=criteria)

    result_mission, effective_codes = asyncio.run(
        edit_mission_criteria(
            session,
            mission_id=mission.id,
            expected_state_version=mission.state_version,
            target_update=(Decimal("300.00"), "BRL"),
            source_codes=None,
            edited_at=NOW,
        )
    )

    assert criteria.target_amount == Decimal("300.00")
    assert criteria.target_currency == "BRL"
    assert result_mission.updated_at == NOW
    assert effective_codes == ()
    session.add.assert_not_called()


def test_edit_clears_target() -> None:
    mission = _mission()
    criteria = _criteria(amount=Decimal("500.00"), currency="BRL")
    session = _session(mission=mission, criteria=criteria)

    asyncio.run(
        edit_mission_criteria(
            session,
            mission_id=mission.id,
            expected_state_version=mission.state_version,
            target_update=(None, None),
            source_codes=None,
            edited_at=NOW,
        )
    )

    assert criteria.target_amount is None
    assert criteria.target_currency is None


def test_edit_raises_when_criteria_missing() -> None:
    mission = _mission()
    session = _session(mission=mission, criteria=None)
    session.scalar = AsyncMock(side_effect=[mission, None])

    with pytest.raises(MissionEditConditionError, match="critérios válidos"):
        asyncio.run(
            edit_mission_criteria(
                session,
                mission_id=mission.id,
                expected_state_version=mission.state_version,
                target_update=(Decimal("300.00"), "BRL"),
                source_codes=None,
            )
        )


def test_edit_replaces_sources_adding_and_removing() -> None:
    mission = _mission()
    kabum, pichau, terabyte = (
        _FakeStore("kabum"),
        _FakeStore("pichau"),
        _FakeStore("terabyte"),
    )
    # estado atual: kabum + pichau; pedido novo: pichau + terabyte
    # -> kabum sai, pichau fica, terabyte entra.
    session = _session(
        mission=mission,
        stores=[pichau, terabyte],
        current_store_ids=[kabum.id, pichau.id],
    )

    _mission_out, effective_codes = asyncio.run(
        edit_mission_criteria(
            session,
            mission_id=mission.id,
            expected_state_version=mission.state_version,
            target_update=None,
            source_codes=("pichau", "terabyte"),
            edited_at=NOW,
        )
    )

    assert set(effective_codes) == {"pichau", "terabyte"}
    session.execute.assert_called_once()  # DELETE só da kabum removida
    added_store_ids = {call.args[0].store_id for call in session.add.call_args_list}
    assert added_store_ids == {terabyte.id}  # só a nova entra via add


def test_edit_raises_on_unknown_source_code() -> None:
    mission = _mission()
    session = _session(mission=mission, stores=[_FakeStore("kabum")])

    with pytest.raises(MissionEditConditionError, match="pichau"):
        asyncio.run(
            edit_mission_criteria(
                session,
                mission_id=mission.id,
                expected_state_version=mission.state_version,
                target_update=None,
                source_codes=("kabum", "pichau"),
            )
        )
