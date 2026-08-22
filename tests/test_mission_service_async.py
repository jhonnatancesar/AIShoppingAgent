"""Equivalentes assíncronos do serviço de missões (extensão da TASK-079,
usados pelo webhook Telegram) -- mesmas regras de
`tests/test_mission_transitions.py`/`tests/test_mission_creation.py`,
cobrindo `transition_mission_async`/`create_mission_from_criteria_async`."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionSchedule,
    MissionStatus,
    VariantSelectionMode,
)
from app.missions.service import (
    MissionNotFoundError,
    MissionVersionConflictError,
    create_mission_from_criteria_async,
    promote_confirmed_product_identity_async,
    set_mission_product_selection_async,
    transition_mission_async,
)
from app.products.identity import classify_product_request
from app.products.models import Product

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


def _mission(
    status: MissionStatus = MissionStatus.DRAFT,
    *,
    expires_at: datetime | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        user_id=uuid4(),
        title="Comprar notebook",
        status=status,
        state_version=0,
        expires_at=expires_at,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )


def _session(*scalar_results: object) -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=scalar_results)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


def test_transition_mission_async_updates_state_version_and_appends_history() -> None:
    mission = _mission(MissionStatus.DRAFT)
    session = _session(mission, uuid4(), uuid4())

    transition = asyncio.run(
        transition_mission_async(
            session,
            mission_id=mission.id,
            command=MissionCommand.ACTIVATE,
            expected_state_version=0,
            actor_type="user",
            actor_id=mission.user_id,
            transitioned_at=NOW,
        )
    )

    assert mission.status is MissionStatus.ACTIVE
    assert mission.state_version == 1
    assert transition.from_status is MissionStatus.DRAFT
    assert transition.to_status is MissionStatus.ACTIVE
    session.add.assert_called_once_with(transition)
    session.flush.assert_called_once_with()


def test_transition_mission_async_cancel_disables_existing_schedule() -> None:
    mission = _mission(MissionStatus.ACTIVE)
    schedule = MissionSchedule(
        mission_id=mission.id,
        interval_minutes=60,
        next_run_at=NOW,
        is_enabled=True,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )
    session = _session(mission, schedule)

    asyncio.run(
        transition_mission_async(
            session,
            mission_id=mission.id,
            command=MissionCommand.CANCEL,
            expected_state_version=0,
            actor_type="telegram",
            actor_id=mission.user_id,
            transitioned_at=NOW,
        )
    )

    assert mission.status is MissionStatus.CANCELLED
    assert mission.state_version == 1
    assert schedule.is_enabled is False
    assert schedule.updated_at == NOW


def test_transition_mission_async_rejects_missing_mission() -> None:
    with pytest.raises(MissionNotFoundError):
        asyncio.run(
            transition_mission_async(
                _session(None),
                mission_id=uuid4(),
                command=MissionCommand.CANCEL,
                expected_state_version=0,
                actor_type="user",
                transitioned_at=NOW,
            )
        )


def test_transition_mission_async_rejects_stale_version() -> None:
    mission = _mission()
    mission.state_version = 2

    with pytest.raises(MissionVersionConflictError):
        asyncio.run(
            transition_mission_async(
                _session(mission),
                mission_id=mission.id,
                command=MissionCommand.CANCEL,
                expected_state_version=1,
                actor_type="user",
                transitioned_at=NOW,
            )
        )


class _FakeStore:
    def __init__(self, code: str) -> None:
        self.code = code
        self.id = uuid4()


def _creation_session(stores: list[_FakeStore]) -> MagicMock:
    """Sessão mockada onde `scalar` "encontra de volta" a missão recém
    adicionada, simulando o que uma transação real faria dentro de
    `transition_mission_async` logo após `create_mission_from_criteria_async`
    inseri-la."""
    session = MagicMock()
    session.scalars = AsyncMock(return_value=stores)
    session.flush = AsyncMock()
    added_missions: list[Mission] = []

    def _capture_add(obj: object) -> None:
        if isinstance(obj, Mission):
            added_missions.append(obj)

    session.add.side_effect = _capture_add

    scalar_calls = {"count": 0}

    async def _scalar_side_effect(*_args: object, **_kwargs: object) -> object:
        scalar_calls["count"] += 1
        if scalar_calls["count"] == 1:
            return added_missions[-1]
        return uuid4()  # critério e fonte existentes, ambos apenas precisam ser truthy

    session.scalar = AsyncMock(side_effect=_scalar_side_effect)
    return session


def test_create_mission_from_criteria_async_activates_with_explicit_sources() -> None:
    stores = [_FakeStore("pichau"), _FakeStore("kabum")]
    session = _creation_session(stores)

    mission, sources = asyncio.run(
        create_mission_from_criteria_async(
            session,
            user_id=uuid4(),
            search_query="notebook gamer",
            target_amount=Decimal("5000.00"),
            target_currency="BRL",
            source_codes=("pichau", "kabum"),
            requested_at=NOW,
            actor_type="telegram",
        )
    )

    assert sources == ("pichau", "kabum")
    assert mission.status is MissionStatus.ACTIVE
    assert mission.title == "notebook gamer"
    schedule = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionSchedule)
    )
    assert schedule.mission_id == mission.id
    assert schedule.next_run_at == NOW
    assert schedule.interval_minutes == 60


def test_select_multiple_discovered_variants_persists_explicit_policy() -> None:
    mission = _mission(MissionStatus.ACTIVE)
    mission.state_version = 4
    request = classify_product_request("iPhone 17")
    criteria = MissionCriteria(
        mission_id=mission.id,
        search_query="iPhone 17",
        request_kind=request.kind.value,
        requested_family_key=request.family_key,
        variant_selection_mode=VariantSelectionMode.PENDING,
    )
    products = tuple(
        Product(
            id=uuid4(),
            name=label,
            display_name=label,
            family_key=request.family_key,
            identity_key=f"v1:{index}",
        )
        for index, label in enumerate(("iPhone 17 256 GB", "iPhone 17 Pro 256 GB"))
    )
    session = _session(mission, criteria)
    session.scalars = AsyncMock(return_value=products)

    selected = asyncio.run(
        set_mission_product_selection_async(
            session,
            user_id=mission.user_id,
            mission_id=mission.id,
            expected_state_version=4,
            product_ids=tuple(product.id for product in products),
            selected_at=NOW,
        )
    )

    assert selected == products
    assert criteria.variant_selection_mode is VariantSelectionMode.SELECTED
    assert mission.state_version == 5
    assert len(tuple(session.add_all.call_args.args[0])) == 2


# ---------------------------------------------------------------------------
# TASK-083 (correção de regressão): promote_confirmed_product_identity_async
# ---------------------------------------------------------------------------


def _criteria(
    mission_id: object, *, search_query: str = "9950X3D", model: str | None = "9950X3D"
) -> MissionCriteria:
    return MissionCriteria(
        id=uuid4(),
        mission_id=mission_id,
        search_query=search_query,
        model=model,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )


def test_promote_confirmed_identity_updates_search_query_and_title() -> None:
    mission = _mission(MissionStatus.ACTIVE)
    criteria = _criteria(mission.id)
    session = _session(mission, criteria)

    promoted = asyncio.run(
        promote_confirmed_product_identity_async(
            session,
            mission_id=mission.id,
            confirmed_search_query="Processador AMD Ryzen 9 9950X3D",
            promoted_at=NOW,
        )
    )

    assert promoted is True
    assert criteria.search_query == "Processador AMD Ryzen 9 9950X3D"
    assert mission.title == "Processador AMD Ryzen 9 9950X3D"
    # TASK-083 (correção): model é o código cru usado pelo matcher
    # determinístico em toda coleta futura -- nunca é a origem do
    # problema, então nunca é alterado pela promoção.
    assert criteria.model == "9950X3D"
    session.flush.assert_called_once_with()


def test_promote_confirmed_identity_corrects_9800x3d_without_changing_model() -> None:
    """Caso B aprovado: a identidade provisória contraditória nunca foi
    persistida; depois da confirmação real, a promoção grava Ryzen 7 e
    preserva o código cru que alimenta o matcher determinístico."""
    mission = _mission(MissionStatus.ACTIVE)
    mission.title = "9800X3D"
    criteria = _criteria(
        mission.id,
        search_query="9800X3D",
        model="9800X3D",
    )
    session = _session(mission, criteria)

    promoted = asyncio.run(
        promote_confirmed_product_identity_async(
            session,
            mission_id=mission.id,
            confirmed_search_query="Processador AMD Ryzen 7 9800X3D",
            promoted_at=NOW,
        )
    )

    assert promoted is True
    assert criteria.search_query == "Processador AMD Ryzen 7 9800X3D"
    assert mission.title == "Processador AMD Ryzen 7 9800X3D"
    assert criteria.model == "9800X3D"


def test_promote_confirmed_identity_truncates_title_to_200_chars() -> None:
    mission = _mission(MissionStatus.ACTIVE)
    criteria = _criteria(mission.id)
    session = _session(mission, criteria)
    long_confirmed = "Processador " + "X" * 250

    asyncio.run(
        promote_confirmed_product_identity_async(
            session,
            mission_id=mission.id,
            confirmed_search_query=long_confirmed,
            promoted_at=NOW,
        )
    )

    assert mission.title == long_confirmed[:200]
    assert criteria.search_query == long_confirmed  # sem truncar (Text, sem limite)


def test_promote_confirmed_identity_missing_mission_is_a_safe_no_op() -> None:
    session = _session(None)  # session.scalar(mission) -> None

    promoted = asyncio.run(
        promote_confirmed_product_identity_async(
            session,
            mission_id=uuid4(),
            confirmed_search_query="Processador AMD Ryzen 9 9950X3D",
        )
    )

    assert promoted is False
    session.flush.assert_not_called()


def test_promote_confirmed_identity_missing_criteria_is_a_safe_no_op() -> None:
    mission = _mission(MissionStatus.ACTIVE)
    original_title = mission.title
    session = _session(mission, None)  # criteria não encontrada

    promoted = asyncio.run(
        promote_confirmed_product_identity_async(
            session,
            mission_id=mission.id,
            confirmed_search_query="Processador AMD Ryzen 9 9950X3D",
        )
    )

    assert promoted is False
    assert mission.title == original_title
    session.flush.assert_not_called()


def test_promote_confirmed_identity_rejects_blank_search_query() -> None:
    session = _session()

    with pytest.raises(ValueError, match="confirmed_search_query"):
        asyncio.run(
            promote_confirmed_product_identity_async(
                session, mission_id=uuid4(), confirmed_search_query="   "
            )
        )
