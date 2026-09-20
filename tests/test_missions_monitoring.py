"""Testes unitários (sessão síncrona `MagicMock` e `AsyncSession`
mockada) de `app.missions.monitoring` (TASK-112, fase 2) -- mesmo padrão
já estabelecido nos demais arquivos de `app.collection`: mock
configurado via `side_effect`/`return_value`, verificando comportamento
real (branches, chamadas mockadas, exceções, ordenação determinística)
-- nenhum teste raso de execução de linha. Cada função tem par
sync/async espelhado; ambos são testados separadamente porque contam
como statements distintos para a cobertura."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.missions.models import (
    MissionCriteria,
    MonitoringItem,
    VariantSelectionMode,
)
from app.missions.monitoring import (
    _activate_item_stores,
    _activate_item_stores_async,
    _canonical_identity_payload,
    _constraint_name,
    _criteria_identity_text,
    _deactivate_item_stores_if_unneeded,
    _deactivate_item_stores_if_unneeded_async,
    _effective_identity,
    _effective_identity_async,
    _other_active_mission_needs_store,
    _other_active_mission_needs_store_async,
    _selected_product_identity,
    _selected_product_identity_async,
    _sorted_store_ids,
    activate_monitoring_item_stores,
    activate_monitoring_item_stores_async,
    deactivate_monitoring_item_stores_if_unneeded,
    deactivate_monitoring_item_stores_if_unneeded_async,
    reconcile_mission_monitoring_item,
    reconcile_mission_monitoring_item_async,
    resolve_or_create_monitoring_item,
    resolve_or_create_monitoring_item_async,
)
from app.products.identity import MonitoringIdentity, MonitoringScope
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _sync_session() -> MagicMock:
    session = MagicMock()
    session.begin_nested.return_value.__exit__.return_value = False
    return session


def _async_session() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=cm)
    return session


def _scalars_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


def _identity(**overrides) -> MonitoringIdentity:
    defaults = dict(
        scope=MonitoringScope.SPECIFIC,
        category="gpu",
        brand="nvidia",
        family="rtx-4070",
        model="rtx-4070",
        variant="ANY",
        attributes=(),
        monitoring_key="gpu:nvidia:rtx-4070:rtx-4070:any",
    )
    defaults.update(overrides)
    return MonitoringIdentity(**defaults)


def _criteria(**overrides) -> MissionCriteria:
    defaults = dict(
        search_query="rtx 4070",
        model=None,
        variant_selection_mode=VariantSelectionMode.PENDING,
    )
    defaults.update(overrides)
    return MissionCriteria(**defaults)


# ---------------------------------------------------------------------------
# funções puras
# ---------------------------------------------------------------------------


def test_constraint_name_extracts_from_diag() -> None:
    error = MagicMock(spec=IntegrityError)
    error.orig.diag.constraint_name = "uq_monitoring_items_monitoring_key"

    assert _constraint_name(error) == "uq_monitoring_items_monitoring_key"


def test_constraint_name_none_when_diag_missing() -> None:
    error = MagicMock(spec=IntegrityError)
    error.orig = None

    assert _constraint_name(error) is None


def test_canonical_identity_payload_builds_dict() -> None:
    identity = _identity(variant="ANY", attributes=(("vram", "12gb"),))

    payload = _canonical_identity_payload(identity)

    assert payload == {
        "scope": "specific",
        "category": "gpu",
        "brand": "nvidia",
        "family": "rtx-4070",
        "model": "rtx-4070",
        "variant": "ANY",
        "attributes": {"vram": "12gb"},
    }


def test_criteria_identity_text_uses_model_when_present() -> None:
    criteria = _criteria(search_query="rtx 4070", model="founders edition")

    assert _criteria_identity_text(criteria) == "rtx 4070 founders edition"


def test_criteria_identity_text_falls_back_to_search_query_only() -> None:
    criteria = _criteria(search_query="rtx 4070", model=None)

    assert _criteria_identity_text(criteria) == "rtx 4070"


def test_sorted_store_ids_returns_deterministic_order() -> None:
    ids = [uuid4() for _ in range(3)]
    shuffled = [ids[2], ids[0], ids[1]]

    assert _sorted_store_ids(shuffled) == sorted(ids)


# ---------------------------------------------------------------------------
# resolve_or_create_monitoring_item (sync + async)
# ---------------------------------------------------------------------------


def test_resolve_or_create_monitoring_item_returns_existing_sync() -> None:
    session = _sync_session()
    existing = MagicMock(spec=MonitoringItem)
    session.scalar.side_effect = [existing]

    result = resolve_or_create_monitoring_item(session, _identity(), now=NOW)

    assert result is existing
    session.add.assert_not_called()


def test_resolve_or_create_monitoring_item_creates_new_sync() -> None:
    session = _sync_session()
    session.scalar.side_effect = [None]

    result = resolve_or_create_monitoring_item(session, _identity(), now=NOW)

    assert isinstance(result, MonitoringItem)
    session.add.assert_called_once()
    session.flush.assert_called_once()


def test_resolve_or_create_monitoring_item_race_requeries_on_matching_constraint_sync(
    monkeypatch,
) -> None:
    session = _sync_session()
    winner = MagicMock(spec=MonitoringItem)
    session.scalar.side_effect = [None, winner]
    error = IntegrityError("x", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.missions.monitoring._constraint_name",
        lambda err: "uq_monitoring_items_monitoring_key",
    )

    result = resolve_or_create_monitoring_item(session, _identity(), now=NOW)

    assert result is winner


def test_resolve_or_create_monitoring_item_reraises_unrelated_integrity_error_sync(
    monkeypatch,
) -> None:
    session = _sync_session()
    session.scalar.side_effect = [None]
    error = IntegrityError("x", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.missions.monitoring._constraint_name", lambda err: "some_other_index"
    )

    with pytest.raises(IntegrityError):
        resolve_or_create_monitoring_item(session, _identity(), now=NOW)


def test_resolve_or_create_monitoring_item_reraises_when_requery_finds_nothing_sync(
    monkeypatch,
) -> None:
    session = _sync_session()
    session.scalar.side_effect = [None, None]
    error = IntegrityError("x", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.missions.monitoring._constraint_name",
        lambda err: "uq_monitoring_items_monitoring_key",
    )

    with pytest.raises(IntegrityError):
        resolve_or_create_monitoring_item(session, _identity(), now=NOW)


def test_resolve_or_create_monitoring_item_returns_existing_async() -> None:
    session = _async_session()
    existing = MagicMock(spec=MonitoringItem)
    session.scalar.side_effect = [existing]

    result = asyncio.run(
        resolve_or_create_monitoring_item_async(session, _identity(), now=NOW)
    )

    assert result is existing
    session.add.assert_not_called()


def test_resolve_or_create_monitoring_item_creates_new_async() -> None:
    session = _async_session()
    session.scalar.side_effect = [None]

    result = asyncio.run(
        resolve_or_create_monitoring_item_async(session, _identity(), now=NOW)
    )

    assert isinstance(result, MonitoringItem)
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


def test_resolve_or_create_monitoring_item_race_requeries_on_matching_constraint_async(
    monkeypatch,
) -> None:
    session = _async_session()
    winner = MagicMock(spec=MonitoringItem)
    session.scalar.side_effect = [None, winner]
    error = IntegrityError("x", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.missions.monitoring._constraint_name",
        lambda err: "uq_monitoring_items_monitoring_key",
    )

    result = asyncio.run(
        resolve_or_create_monitoring_item_async(session, _identity(), now=NOW)
    )

    assert result is winner


def test_resolve_or_create_monitoring_item_reraises_unrelated_integrity_error_async(
    monkeypatch,
) -> None:
    session = _async_session()
    session.scalar.side_effect = [None]
    error = IntegrityError("x", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.missions.monitoring._constraint_name", lambda err: "some_other_index"
    )

    with pytest.raises(IntegrityError):
        asyncio.run(
            resolve_or_create_monitoring_item_async(session, _identity(), now=NOW)
        )


def test_resolve_or_create_monitoring_item_reraises_when_requery_finds_nothing_async(
    monkeypatch,
) -> None:
    session = _async_session()
    session.scalar.side_effect = [None, None]
    error = IntegrityError("x", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.missions.monitoring._constraint_name",
        lambda err: "uq_monitoring_items_monitoring_key",
    )

    with pytest.raises(IntegrityError):
        asyncio.run(
            resolve_or_create_monitoring_item_async(session, _identity(), now=NOW)
        )


# ---------------------------------------------------------------------------
# _activate_item_stores (sync + async)
# ---------------------------------------------------------------------------


def test_activate_item_stores_executes_upsert_per_store_sync() -> None:
    session = _sync_session()
    store_ids = [uuid4(), uuid4()]

    _activate_item_stores(
        session,
        monitoring_item_id=uuid4(),
        store_ids=[store_ids[1], store_ids[0]],
        now=NOW,
    )

    assert session.execute.call_count == 2


def test_activate_item_stores_executes_upsert_per_store_async() -> None:
    session = _async_session()
    store_ids = [uuid4(), uuid4()]

    asyncio.run(
        _activate_item_stores_async(
            session,
            monitoring_item_id=uuid4(),
            store_ids=[store_ids[1], store_ids[0]],
            now=NOW,
        )
    )

    assert session.execute.await_count == 2


# ---------------------------------------------------------------------------
# _other_active_mission_needs_store (sync + async)
# ---------------------------------------------------------------------------


def test_other_active_mission_needs_store_true_sync() -> None:
    session = _sync_session()
    session.scalar.return_value = True

    result = _other_active_mission_needs_store(
        session,
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
        excluding_mission_id=uuid4(),
    )

    assert result is True


def test_other_active_mission_needs_store_false_sync() -> None:
    session = _sync_session()
    session.scalar.return_value = False

    result = _other_active_mission_needs_store(
        session,
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
        excluding_mission_id=uuid4(),
    )

    assert result is False


def test_other_active_mission_needs_store_true_async() -> None:
    session = _async_session()
    session.scalar.return_value = True

    result = asyncio.run(
        _other_active_mission_needs_store_async(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            excluding_mission_id=uuid4(),
        )
    )

    assert result is True


def test_other_active_mission_needs_store_false_async() -> None:
    session = _async_session()
    session.scalar.return_value = False

    result = asyncio.run(
        _other_active_mission_needs_store_async(
            session,
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            excluding_mission_id=uuid4(),
        )
    )

    assert result is False


# ---------------------------------------------------------------------------
# _deactivate_item_stores_if_unneeded (sync + async)
# ---------------------------------------------------------------------------


def test_deactivate_item_stores_if_unneeded_covers_all_branches_sync(
    monkeypatch,
) -> None:
    missing_store, disabled_store, still_needed_store, freed_store = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    store_ids = sorted([missing_store, disabled_store, still_needed_store, freed_store])
    session = _sync_session()
    rows_by_store = {
        missing_store: None,
        disabled_store: MagicMock(is_enabled=False, updated_at=None),
        still_needed_store: MagicMock(is_enabled=True, updated_at=None),
        freed_store: MagicMock(is_enabled=True, updated_at=None),
    }
    session.get.side_effect = lambda model, key, **kw: rows_by_store[key[1]]
    monkeypatch.setattr(
        "app.missions.monitoring._other_active_mission_needs_store",
        lambda session, *, monitoring_item_id, store_id, excluding_mission_id: (
            store_id == still_needed_store
        ),
    )

    _deactivate_item_stores_if_unneeded(
        session,
        monitoring_item_id=uuid4(),
        store_ids=store_ids,
        excluding_mission_id=uuid4(),
        now=NOW,
    )

    assert rows_by_store[disabled_store].updated_at is None
    assert rows_by_store[still_needed_store].updated_at is None
    assert rows_by_store[freed_store].is_enabled is False
    assert rows_by_store[freed_store].updated_at == NOW


def test_deactivate_item_stores_if_unneeded_covers_all_branches_async(
    monkeypatch,
) -> None:
    missing_store, disabled_store, still_needed_store, freed_store = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    store_ids = sorted([missing_store, disabled_store, still_needed_store, freed_store])
    session = _async_session()
    rows_by_store = {
        missing_store: None,
        disabled_store: MagicMock(is_enabled=False, updated_at=None),
        still_needed_store: MagicMock(is_enabled=True, updated_at=None),
        freed_store: MagicMock(is_enabled=True, updated_at=None),
    }
    session.get.side_effect = lambda model, key, **kw: rows_by_store[key[1]]
    monkeypatch.setattr(
        "app.missions.monitoring._other_active_mission_needs_store_async",
        AsyncMock(
            side_effect=lambda session, *, monitoring_item_id, store_id, excluding_mission_id: (
                store_id == still_needed_store
            )
        ),
    )

    asyncio.run(
        _deactivate_item_stores_if_unneeded_async(
            session,
            monitoring_item_id=uuid4(),
            store_ids=store_ids,
            excluding_mission_id=uuid4(),
            now=NOW,
        )
    )

    assert rows_by_store[disabled_store].updated_at is None
    assert rows_by_store[still_needed_store].updated_at is None
    assert rows_by_store[freed_store].is_enabled is False
    assert rows_by_store[freed_store].updated_at == NOW


# ---------------------------------------------------------------------------
# activate_monitoring_item_stores / deactivate_monitoring_item_stores_if_unneeded
# ---------------------------------------------------------------------------


def test_activate_monitoring_item_stores_returns_early_when_link_missing_sync() -> None:
    session = _sync_session()
    session.get.return_value = None

    activate_monitoring_item_stores(session, mission_id=uuid4(), now=NOW)

    session.scalars.assert_not_called()


def test_activate_monitoring_item_stores_delegates_when_link_found_sync(
    monkeypatch,
) -> None:
    session = _sync_session()
    monitoring_item_id = uuid4()
    session.get.return_value = MagicMock(monitoring_item_id=monitoring_item_id)
    session.scalars.return_value = _scalars_result([uuid4()])
    activate_inner = MagicMock()
    monkeypatch.setattr("app.missions.monitoring._activate_item_stores", activate_inner)

    activate_monitoring_item_stores(session, mission_id=uuid4(), now=NOW)

    activate_inner.assert_called_once()
    assert activate_inner.call_args.kwargs["monitoring_item_id"] == monitoring_item_id


def test_activate_monitoring_item_stores_returns_early_when_link_missing_async() -> (
    None
):
    session = _async_session()
    session.get.return_value = None

    asyncio.run(
        activate_monitoring_item_stores_async(session, mission_id=uuid4(), now=NOW)
    )

    session.scalars.assert_not_awaited()


def test_activate_monitoring_item_stores_delegates_when_link_found_async(
    monkeypatch,
) -> None:
    session = _async_session()
    monitoring_item_id = uuid4()
    session.get.return_value = MagicMock(monitoring_item_id=monitoring_item_id)
    session.scalars.return_value = _scalars_result([uuid4()])
    activate_inner = AsyncMock()
    monkeypatch.setattr(
        "app.missions.monitoring._activate_item_stores_async", activate_inner
    )

    asyncio.run(
        activate_monitoring_item_stores_async(session, mission_id=uuid4(), now=NOW)
    )

    activate_inner.assert_awaited_once()
    assert activate_inner.await_args.kwargs["monitoring_item_id"] == monitoring_item_id


def test_deactivate_monitoring_item_stores_returns_early_when_link_missing_sync() -> (
    None
):
    session = _sync_session()
    session.get.return_value = None

    deactivate_monitoring_item_stores_if_unneeded(session, mission_id=uuid4(), now=NOW)

    session.scalars.assert_not_called()


def test_deactivate_monitoring_item_stores_delegates_when_link_found_sync(
    monkeypatch,
) -> None:
    session = _sync_session()
    monitoring_item_id = uuid4()
    mission_id = uuid4()
    session.get.return_value = MagicMock(monitoring_item_id=monitoring_item_id)
    session.scalars.return_value = _scalars_result([uuid4()])
    deactivate_inner = MagicMock()
    monkeypatch.setattr(
        "app.missions.monitoring._deactivate_item_stores_if_unneeded", deactivate_inner
    )

    deactivate_monitoring_item_stores_if_unneeded(
        session, mission_id=mission_id, now=NOW
    )

    deactivate_inner.assert_called_once()
    assert deactivate_inner.call_args.kwargs["excluding_mission_id"] == mission_id


def test_deactivate_monitoring_item_stores_returns_early_when_link_missing_async() -> (
    None
):
    session = _async_session()
    session.get.return_value = None

    asyncio.run(
        deactivate_monitoring_item_stores_if_unneeded_async(
            session, mission_id=uuid4(), now=NOW
        )
    )

    session.scalars.assert_not_awaited()


def test_deactivate_monitoring_item_stores_delegates_when_link_found_async(
    monkeypatch,
) -> None:
    session = _async_session()
    monitoring_item_id = uuid4()
    mission_id = uuid4()
    session.get.return_value = MagicMock(monitoring_item_id=monitoring_item_id)
    session.scalars.return_value = _scalars_result([uuid4()])
    deactivate_inner = AsyncMock()
    monkeypatch.setattr(
        "app.missions.monitoring._deactivate_item_stores_if_unneeded_async",
        deactivate_inner,
    )

    asyncio.run(
        deactivate_monitoring_item_stores_if_unneeded_async(
            session, mission_id=mission_id, now=NOW
        )
    )

    deactivate_inner.assert_awaited_once()
    assert deactivate_inner.await_args.kwargs["excluding_mission_id"] == mission_id


# ---------------------------------------------------------------------------
# _selected_product_identity (sync + async)
# ---------------------------------------------------------------------------


def test_selected_product_identity_none_when_mode_not_selected_sync() -> None:
    session = _sync_session()
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.ALL)

    result = _selected_product_identity(session, mission_id=uuid4(), criteria=criteria)

    assert result is None
    session.scalars.assert_not_called()


def test_selected_product_identity_none_when_multiple_products_sync() -> None:
    session = _sync_session()
    session.scalars.return_value = _scalars_result([uuid4(), uuid4()])
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = _selected_product_identity(session, mission_id=uuid4(), criteria=criteria)

    assert result is None


def test_selected_product_identity_none_when_product_missing_sync() -> None:
    session = _sync_session()
    session.scalars.return_value = _scalars_result([uuid4()])
    session.get.return_value = None
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = _selected_product_identity(session, mission_id=uuid4(), criteria=criteria)

    assert result is None


def test_selected_product_identity_none_when_identity_key_missing_sync() -> None:
    session = _sync_session()
    session.scalars.return_value = _scalars_result([uuid4()])
    session.get.return_value = MagicMock(identity_key=None)
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = _selected_product_identity(session, mission_id=uuid4(), criteria=criteria)

    assert result is None


def test_selected_product_identity_resolves_from_product_sync(monkeypatch) -> None:
    session = _sync_session()
    session.scalars.return_value = _scalars_result([uuid4()])
    product = MagicMock(
        identity_key="k",
        category="gpu",
        brand="nvidia",
        family="rtx",
        model="rtx-4070",
        variant=None,
        attributes={},
    )
    session.get.return_value = product
    expected = _identity()
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_monitoring_identity_for_resolved_product",
        lambda **kw: expected,
    )
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = _selected_product_identity(session, mission_id=uuid4(), criteria=criteria)

    assert result is expected


def test_selected_product_identity_none_when_mode_not_selected_async() -> None:
    session = _async_session()
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.ALL)

    result = asyncio.run(
        _selected_product_identity_async(session, mission_id=uuid4(), criteria=criteria)
    )

    assert result is None
    session.scalars.assert_not_awaited()


def test_selected_product_identity_none_when_multiple_products_async() -> None:
    session = _async_session()
    session.scalars.return_value = _scalars_result([uuid4(), uuid4()])
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = asyncio.run(
        _selected_product_identity_async(session, mission_id=uuid4(), criteria=criteria)
    )

    assert result is None


def test_selected_product_identity_none_when_product_missing_async() -> None:
    session = _async_session()
    session.scalars.return_value = _scalars_result([uuid4()])
    session.get.return_value = None
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = asyncio.run(
        _selected_product_identity_async(session, mission_id=uuid4(), criteria=criteria)
    )

    assert result is None


def test_selected_product_identity_none_when_identity_key_missing_async() -> None:
    session = _async_session()
    session.scalars.return_value = _scalars_result([uuid4()])
    session.get.return_value = MagicMock(identity_key=None)
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = asyncio.run(
        _selected_product_identity_async(session, mission_id=uuid4(), criteria=criteria)
    )

    assert result is None


def test_selected_product_identity_resolves_from_product_async(monkeypatch) -> None:
    session = _async_session()
    session.scalars.return_value = _scalars_result([uuid4()])
    product = MagicMock(
        identity_key="k",
        category="gpu",
        brand="nvidia",
        family="rtx",
        model="rtx-4070",
        variant=None,
        attributes={},
    )
    session.get.return_value = product
    expected = _identity()
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_monitoring_identity_for_resolved_product",
        lambda **kw: expected,
    )
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.SELECTED)

    result = asyncio.run(
        _selected_product_identity_async(session, mission_id=uuid4(), criteria=criteria)
    )

    assert result is expected


# ---------------------------------------------------------------------------
# _effective_identity (sync + async)
# ---------------------------------------------------------------------------


def test_effective_identity_returns_selected_when_present_sync(monkeypatch) -> None:
    session = _sync_session()
    expected = _identity()
    monkeypatch.setattr(
        "app.missions.monitoring._selected_product_identity",
        lambda session, *, mission_id, criteria: expected,
    )

    result = _effective_identity(session, mission_id=uuid4(), criteria=_criteria())

    assert result is expected


def test_effective_identity_resolves_family_for_all_mode_sync(monkeypatch) -> None:
    session = _sync_session()
    monkeypatch.setattr(
        "app.missions.monitoring._selected_product_identity",
        lambda session, *, mission_id, criteria: None,
    )
    family_identity = _identity(scope=MonitoringScope.FAMILY)
    resolve_family = MagicMock(return_value=family_identity)
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_monitoring_identity_for_family",
        resolve_family,
    )
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.ALL)

    result = _effective_identity(session, mission_id=uuid4(), criteria=criteria)

    assert result is family_identity
    resolve_family.assert_called_once_with(_criteria_identity_text(criteria))


def test_effective_identity_resolves_text_identity_otherwise_sync(monkeypatch) -> None:
    session = _sync_session()
    monkeypatch.setattr(
        "app.missions.monitoring._selected_product_identity",
        lambda session, *, mission_id, criteria: None,
    )
    text_identity = _identity()
    resolve_text = MagicMock(return_value=text_identity)
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_monitoring_identity", resolve_text
    )
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.PENDING)

    result = _effective_identity(session, mission_id=uuid4(), criteria=criteria)

    assert result is text_identity


def test_effective_identity_returns_selected_when_present_async(monkeypatch) -> None:
    session = _async_session()
    expected = _identity()
    monkeypatch.setattr(
        "app.missions.monitoring._selected_product_identity_async",
        AsyncMock(return_value=expected),
    )

    result = asyncio.run(
        _effective_identity_async(session, mission_id=uuid4(), criteria=_criteria())
    )

    assert result is expected


def test_effective_identity_resolves_family_for_all_mode_async(monkeypatch) -> None:
    session = _async_session()
    monkeypatch.setattr(
        "app.missions.monitoring._selected_product_identity_async",
        AsyncMock(return_value=None),
    )
    family_identity = _identity(scope=MonitoringScope.FAMILY)
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_monitoring_identity_for_family",
        lambda text: family_identity,
    )
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.ALL)

    result = asyncio.run(
        _effective_identity_async(session, mission_id=uuid4(), criteria=criteria)
    )

    assert result is family_identity


def test_effective_identity_resolves_text_identity_otherwise_async(monkeypatch) -> None:
    session = _async_session()
    monkeypatch.setattr(
        "app.missions.monitoring._selected_product_identity_async",
        AsyncMock(return_value=None),
    )
    text_identity = _identity()
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_monitoring_identity",
        lambda text: text_identity,
    )
    criteria = _criteria(variant_selection_mode=VariantSelectionMode.PENDING)

    result = asyncio.run(
        _effective_identity_async(session, mission_id=uuid4(), criteria=criteria)
    )

    assert result is text_identity


# ---------------------------------------------------------------------------
# reconcile_mission_monitoring_item (sync + async)
# ---------------------------------------------------------------------------


def _patch_reconcile_collaborators_sync(monkeypatch, *, identity, target_item):
    monkeypatch.setattr(
        "app.missions.monitoring._effective_identity",
        lambda session, *, mission_id, criteria: identity,
    )
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_or_create_monitoring_item",
        lambda session, identity, *, now: target_item,
    )
    activate = MagicMock()
    deactivate = MagicMock()
    monkeypatch.setattr("app.missions.monitoring._activate_item_stores", activate)
    monkeypatch.setattr(
        "app.missions.monitoring._deactivate_item_stores_if_unneeded", deactivate
    )
    return activate, deactivate


def test_reconcile_same_item_active_reactivates_stores_sync(monkeypatch) -> None:
    session = _sync_session()
    mission_id = uuid4()
    item_id = uuid4()
    target_item = MagicMock(spec=MonitoringItem, id=item_id)
    session.get.return_value = MagicMock(monitoring_item_id=item_id)
    session.scalars.return_value = _scalars_result([uuid4()])
    activate, deactivate = _patch_reconcile_collaborators_sync(
        monkeypatch, identity=_identity(), target_item=target_item
    )

    result = reconcile_mission_monitoring_item(
        session,
        mission_id=mission_id,
        criteria=_criteria(),
        mission_is_active=True,
        now=NOW,
    )

    assert result is target_item
    activate.assert_called_once()
    deactivate.assert_not_called()
    session.delete.assert_not_called()


def test_reconcile_same_item_inactive_skips_reactivation_sync(monkeypatch) -> None:
    session = _sync_session()
    item_id = uuid4()
    target_item = MagicMock(spec=MonitoringItem, id=item_id)
    session.get.return_value = MagicMock(monitoring_item_id=item_id)
    session.scalars.return_value = _scalars_result([uuid4()])
    activate, deactivate = _patch_reconcile_collaborators_sync(
        monkeypatch, identity=_identity(), target_item=target_item
    )

    reconcile_mission_monitoring_item(
        session,
        mission_id=uuid4(),
        criteria=_criteria(),
        mission_is_active=False,
        now=NOW,
    )

    activate.assert_not_called()


def test_reconcile_none_to_none_is_noop_sync(monkeypatch) -> None:
    session = _sync_session()
    session.get.return_value = None
    session.scalars.return_value = _scalars_result([])
    activate, deactivate = _patch_reconcile_collaborators_sync(
        monkeypatch, identity=None, target_item=None
    )

    result = reconcile_mission_monitoring_item(
        session,
        mission_id=uuid4(),
        criteria=_criteria(),
        mission_is_active=True,
        now=NOW,
    )

    assert result is None
    activate.assert_not_called()
    deactivate.assert_not_called()
    session.delete.assert_not_called()


def test_reconcile_unresolved_to_resolved_creates_link_and_activates_sync(
    monkeypatch,
) -> None:
    session = _sync_session()
    session.get.return_value = None
    session.scalars.return_value = _scalars_result([uuid4()])
    target_item = MagicMock(spec=MonitoringItem, id=uuid4())
    activate, deactivate = _patch_reconcile_collaborators_sync(
        monkeypatch, identity=_identity(), target_item=target_item
    )

    result = reconcile_mission_monitoring_item(
        session,
        mission_id=uuid4(),
        criteria=_criteria(),
        mission_is_active=True,
        now=NOW,
    )

    assert result is target_item
    session.add.assert_called_once()
    activate.assert_called_once()
    deactivate.assert_not_called()


def test_reconcile_resolved_to_different_resolved_switches_link_sync(
    monkeypatch,
) -> None:
    session = _sync_session()
    old_item_id = uuid4()
    current_link = MagicMock(monitoring_item_id=old_item_id)
    session.get.return_value = current_link
    session.scalars.return_value = _scalars_result([uuid4()])
    target_item = MagicMock(spec=MonitoringItem, id=uuid4())
    activate, deactivate = _patch_reconcile_collaborators_sync(
        monkeypatch, identity=_identity(), target_item=target_item
    )

    result = reconcile_mission_monitoring_item(
        session,
        mission_id=uuid4(),
        criteria=_criteria(),
        mission_is_active=True,
        now=NOW,
    )

    assert result is target_item
    session.delete.assert_called_once_with(current_link)
    session.add.assert_called_once()
    activate.assert_called_once()
    deactivate.assert_called_once()
    assert deactivate.call_args.kwargs["monitoring_item_id"] == old_item_id


def test_reconcile_resolved_to_unresolved_removes_link_sync(monkeypatch) -> None:
    session = _sync_session()
    old_item_id = uuid4()
    current_link = MagicMock(monitoring_item_id=old_item_id)
    session.get.return_value = current_link
    session.scalars.return_value = _scalars_result([uuid4()])
    activate, deactivate = _patch_reconcile_collaborators_sync(
        monkeypatch, identity=None, target_item=None
    )

    result = reconcile_mission_monitoring_item(
        session,
        mission_id=uuid4(),
        criteria=_criteria(),
        mission_is_active=True,
        now=NOW,
    )

    assert result is None
    session.delete.assert_called_once_with(current_link)
    session.add.assert_not_called()
    activate.assert_not_called()
    deactivate.assert_called_once()
    assert deactivate.call_args.kwargs["monitoring_item_id"] == old_item_id


def _patch_reconcile_collaborators_async(monkeypatch, *, identity, target_item):
    monkeypatch.setattr(
        "app.missions.monitoring._effective_identity_async",
        AsyncMock(return_value=identity),
    )
    monkeypatch.setattr(
        "app.missions.monitoring.resolve_or_create_monitoring_item_async",
        AsyncMock(return_value=target_item),
    )
    activate = AsyncMock()
    deactivate = AsyncMock()
    monkeypatch.setattr("app.missions.monitoring._activate_item_stores_async", activate)
    monkeypatch.setattr(
        "app.missions.monitoring._deactivate_item_stores_if_unneeded_async", deactivate
    )
    return activate, deactivate


def test_reconcile_same_item_active_reactivates_stores_async(monkeypatch) -> None:
    session = _async_session()
    item_id = uuid4()
    target_item = MagicMock(spec=MonitoringItem, id=item_id)
    session.get.return_value = MagicMock(monitoring_item_id=item_id)
    session.scalars.return_value = _scalars_result([uuid4()])
    activate, deactivate = _patch_reconcile_collaborators_async(
        monkeypatch, identity=_identity(), target_item=target_item
    )

    result = asyncio.run(
        reconcile_mission_monitoring_item_async(
            session,
            mission_id=uuid4(),
            criteria=_criteria(),
            mission_is_active=True,
            now=NOW,
        )
    )

    assert result is target_item
    activate.assert_awaited_once()
    deactivate.assert_not_awaited()
    session.delete.assert_not_awaited()


def test_reconcile_none_to_none_is_noop_async(monkeypatch) -> None:
    session = _async_session()
    session.get.return_value = None
    session.scalars.return_value = _scalars_result([])
    activate, deactivate = _patch_reconcile_collaborators_async(
        monkeypatch, identity=None, target_item=None
    )

    result = asyncio.run(
        reconcile_mission_monitoring_item_async(
            session,
            mission_id=uuid4(),
            criteria=_criteria(),
            mission_is_active=True,
            now=NOW,
        )
    )

    assert result is None
    activate.assert_not_awaited()
    deactivate.assert_not_awaited()


def test_reconcile_unresolved_to_resolved_creates_link_and_activates_async(
    monkeypatch,
) -> None:
    session = _async_session()
    session.get.return_value = None
    session.scalars.return_value = _scalars_result([uuid4()])
    target_item = MagicMock(spec=MonitoringItem, id=uuid4())
    activate, deactivate = _patch_reconcile_collaborators_async(
        monkeypatch, identity=_identity(), target_item=target_item
    )

    result = asyncio.run(
        reconcile_mission_monitoring_item_async(
            session,
            mission_id=uuid4(),
            criteria=_criteria(),
            mission_is_active=True,
            now=NOW,
        )
    )

    assert result is target_item
    session.add.assert_called_once()
    activate.assert_awaited_once()
    deactivate.assert_not_awaited()


def test_reconcile_resolved_to_different_resolved_switches_link_async(
    monkeypatch,
) -> None:
    session = _async_session()
    old_item_id = uuid4()
    current_link = MagicMock(monitoring_item_id=old_item_id)
    session.get.return_value = current_link
    session.scalars.return_value = _scalars_result([uuid4()])
    target_item = MagicMock(spec=MonitoringItem, id=uuid4())
    activate, deactivate = _patch_reconcile_collaborators_async(
        monkeypatch, identity=_identity(), target_item=target_item
    )

    result = asyncio.run(
        reconcile_mission_monitoring_item_async(
            session,
            mission_id=uuid4(),
            criteria=_criteria(),
            mission_is_active=True,
            now=NOW,
        )
    )

    assert result is target_item
    session.delete.assert_awaited_once_with(current_link)
    session.add.assert_called_once()
    activate.assert_awaited_once()
    deactivate.assert_awaited_once()
    assert deactivate.await_args.kwargs["monitoring_item_id"] == old_item_id


def test_reconcile_resolved_to_unresolved_removes_link_async(monkeypatch) -> None:
    session = _async_session()
    old_item_id = uuid4()
    current_link = MagicMock(monitoring_item_id=old_item_id)
    session.get.return_value = current_link
    session.scalars.return_value = _scalars_result([uuid4()])
    activate, deactivate = _patch_reconcile_collaborators_async(
        monkeypatch, identity=None, target_item=None
    )

    result = asyncio.run(
        reconcile_mission_monitoring_item_async(
            session,
            mission_id=uuid4(),
            criteria=_criteria(),
            mission_is_active=True,
            now=NOW,
        )
    )

    assert result is None
    session.delete.assert_awaited_once_with(current_link)
    session.add.assert_not_called()
    activate.assert_not_awaited()
    deactivate.assert_awaited_once()
    assert deactivate.await_args.kwargs["monitoring_item_id"] == old_item_id
