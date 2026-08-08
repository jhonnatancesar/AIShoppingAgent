"""Testes da criação orquestrada de missões a partir de critérios."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.missions.models import Mission, MissionStatus
from app.missions.service import MissionCreationError, create_mission_from_criteria

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


class _FakeStore:
    def __init__(self, code: str) -> None:
        self.code = code
        self.id = uuid4()


def _session(stores: list[_FakeStore]) -> MagicMock:
    """Sessão mockada onde `scalar` "encontra de volta" a missão recém
    adicionada, simulando o que uma transação real faria dentro de
    `transition_mission` logo após `create_mission_from_criteria` inseri-la.
    """
    session = MagicMock()
    session.scalars.return_value = stores
    added_missions: list[Mission] = []

    def _capture_add(obj: object) -> None:
        if isinstance(obj, Mission):
            added_missions.append(obj)

    session.add.side_effect = _capture_add

    scalar_calls = {"count": 0}

    def _scalar_side_effect(*_args: object, **_kwargs: object) -> object:
        scalar_calls["count"] += 1
        if scalar_calls["count"] == 1:
            return added_missions[-1]
        return uuid4()  # critério e fonte existentes, ambos apenas precisam ser truthy

    session.scalar.side_effect = _scalar_side_effect
    return session


def test_create_mission_with_explicit_sources_activates_with_exactly_those() -> None:
    stores = [_FakeStore("pichau"), _FakeStore("kabum")]
    session = _session(stores)

    mission, sources = create_mission_from_criteria(
        session,
        user_id=uuid4(),
        search_query="notebook gamer",
        target_amount=Decimal("5000.00"),
        target_currency="BRL",
        source_codes=("pichau", "kabum"),
        requested_at=NOW,
    )

    assert sources == ("pichau", "kabum")
    assert mission.status is MissionStatus.ACTIVE
    assert mission.title == "notebook gamer"


def test_create_mission_without_sources_uses_all_four_v1_sources() -> None:
    stores = [
        _FakeStore("pichau"),
        _FakeStore("terabyte"),
        _FakeStore("amazon"),
        _FakeStore("kabum"),
    ]
    session = _session(stores)

    mission, sources = create_mission_from_criteria(
        session,
        user_id=uuid4(),
        search_query="RTX 4060",
        target_amount=None,
        target_currency=None,
        source_codes=(),
        requested_at=NOW,
    )

    assert set(sources) == {"pichau", "terabyte", "amazon", "kabum"}
    assert mission.status is MissionStatus.ACTIVE


def test_create_mission_raises_when_a_source_store_is_not_seeded() -> None:
    session = _session(stores=[])  # nenhuma loja "semeada" para os códigos pedidos

    with pytest.raises(MissionCreationError, match="pichau"):
        create_mission_from_criteria(
            session,
            user_id=uuid4(),
            search_query="notebook gamer",
            target_amount=None,
            target_currency=None,
            source_codes=("pichau",),
            requested_at=NOW,
        )
