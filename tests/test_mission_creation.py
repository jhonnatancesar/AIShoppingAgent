"""Testes da criação orquestrada de missões a partir de critérios."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionStatus,
    VariantSelectionMode,
)
from app.missions.service import MissionCreationError, create_mission_from_criteria
from app.products.identity import ProductRequestKind
from app.users.models import User, UserRole

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_shared_monitoring(monkeypatch):
    """TASK-112 (fase 2): este arquivo testa a criação/ativação da missão
    em si -- o vínculo com `MonitoringItem` tem cobertura própria
    (`tests/integration/test_shared_monitoring.py`, contra PostgreSQL
    real). `_session()` abaixo usa uma sequência posicional de
    `session.scalar` desenhada para `transition_mission`; sem este no-op,
    a consulta nova do vínculo (antes da transição) desalinharia essa
    sequência para todo teste, por motivo alheio ao que está sendo
    testado."""
    monkeypatch.setattr(
        "app.missions.service.reconcile_mission_monitoring_item", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.missions.service.activate_monitoring_item_stores", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.missions.service.deactivate_monitoring_item_stores_if_unneeded",
        lambda *_a, **_k: None,
    )


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

    owner = User(id=uuid4(), display_name="Teste", role=UserRole.USER)
    scalar_calls = {"count": 0}

    def _scalar_side_effect(*_args: object, **_kwargs: object) -> object:
        scalar_calls["count"] += 1
        if scalar_calls["count"] == 1:
            return added_missions[-1]
        if scalar_calls["count"] in (2, 3):
            return uuid4()  # critério e fonte existentes, só precisam ser truthy
        if scalar_calls["count"] == 4:
            # TASK-107: dono da missão, buscado pela checagem de cota
            # dentro de `transition_mission` (sem override -> usa default).
            return owner
        # TASK-107: contagens de uso de cota (missões ativas, store
        # slots, pesquisas diárias, fontes desta missão) -- 0 sempre cabe
        # nos defaults (`max_active_missions=5`/`max_store_slots=18`).
        return 0

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
        actor_type="telegram",
    )

    assert sources == ("pichau", "kabum")
    assert mission.status is MissionStatus.ACTIVE
    assert mission.title == "notebook gamer"


def test_create_mission_with_explicit_title_uses_it_instead_of_search_query() -> None:
    """TASK-083 (correção de regressão): `title` (`display_query` do
    `IntentInterpreter`) vira `Mission.title`; `MissionCriteria.search_query`
    continua o valor operacional passado separadamente."""
    stores = [_FakeStore("kabum")]
    session = _session(stores)

    mission, _ = create_mission_from_criteria(
        session,
        user_id=uuid4(),
        search_query="9950X3D",
        model="9950X3D",
        title="AMD Ryzen 9 9950X3D",
        target_amount=None,
        target_currency=None,
        source_codes=("kabum",),
        requested_at=NOW,
        actor_type="telegram",
    )

    assert mission.title == "AMD Ryzen 9 9950X3D"
    criteria = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionCriteria)
    )
    assert criteria.search_query == "9950X3D"
    schedule = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionSchedule)
    )
    assert schedule.mission_id == mission.id
    assert schedule.next_run_at == NOW
    assert schedule.interval_minutes == 60


def test_create_mission_persists_structured_model_on_criteria() -> None:
    """TASK-075: model estruturado (quando informado) chega em
    MissionCriteria.model, ao lado de search_query."""
    stores = [_FakeStore("pichau"), _FakeStore("kabum")]
    session = _session(stores)

    mission, _ = create_mission_from_criteria(
        session,
        user_id=uuid4(),
        search_query="Processador AMD Ryzen 9 9950X3D",
        model="9950X3D",
        target_amount=None,
        target_currency=None,
        source_codes=("pichau", "kabum"),
        requested_at=NOW,
        actor_type="telegram",
    )

    criteria = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionCriteria)
    )
    assert criteria.mission_id == mission.id
    assert criteria.model == "9950X3D"


def test_create_mission_without_model_leaves_criteria_model_none() -> None:
    stores = [_FakeStore("pichau"), _FakeStore("kabum")]
    session = _session(stores)

    mission, _ = create_mission_from_criteria(
        session,
        user_id=uuid4(),
        search_query="notebook gamer",
        target_amount=None,
        target_currency=None,
        source_codes=("pichau", "kabum"),
        requested_at=NOW,
        actor_type="telegram",
    )

    criteria = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionCriteria)
    )
    assert criteria.mission_id == mission.id
    assert criteria.model is None


@pytest.mark.parametrize(
    ("query", "kind", "mode"),
    [
        (
            "iPhone 17 Pro 256GB",
            ProductRequestKind.SPECIFIC_PRODUCT,
            VariantSelectionMode.NOT_REQUIRED,
        ),
        (
            "iPhone 17",
            ProductRequestKind.PRODUCT_FAMILY,
            VariantSelectionMode.PENDING,
        ),
        (
            "cadeira gamer",
            ProductRequestKind.GENERIC_CATEGORY,
            VariantSelectionMode.NOT_REQUIRED,
        ),
    ],
)
def test_create_mission_classifies_product_scope_deterministically(
    query: str, kind: ProductRequestKind, mode: VariantSelectionMode
) -> None:
    session = _session([_FakeStore("amazon")])

    create_mission_from_criteria(
        session,
        user_id=uuid4(),
        search_query=query,
        target_amount=None,
        target_currency=None,
        source_codes=("amazon",),
        requested_at=NOW,
        actor_type="web",
    )

    criteria = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionCriteria)
    )
    assert criteria.request_kind == kind.value
    assert criteria.variant_selection_mode is mode
    assert (criteria.requested_identity_key is not None) == (
        kind is ProductRequestKind.SPECIFIC_PRODUCT
    )


def test_create_mission_applies_schedule_stagger_when_configured() -> None:
    stores = [_FakeStore("pichau"), _FakeStore("kabum")]
    session = _session(stores)

    mission, _ = create_mission_from_criteria(
        session,
        user_id=uuid4(),
        search_query="notebook gamer",
        target_amount=None,
        target_currency=None,
        source_codes=("pichau", "kabum"),
        requested_at=NOW,
        actor_type="telegram",
        schedule_stagger_seconds=300,
    )

    schedule = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionSchedule)
    )
    assert schedule.mission_id == mission.id
    assert NOW <= schedule.next_run_at <= NOW + timedelta(seconds=300)


def test_create_mission_without_sources_uses_all_available_sources() -> None:
    stores = [
        _FakeStore("pichau"),
        _FakeStore("terabyte"),
        _FakeStore("amazon"),
        _FakeStore("kabum"),
        _FakeStore("magalu"),
        _FakeStore("mercadolivre"),
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
        actor_type="telegram",
    )

    assert set(sources) == {
        "pichau",
        "terabyte",
        "amazon",
        "kabum",
        "magalu",
        "mercadolivre",
    }
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
            actor_type="telegram",
        )
