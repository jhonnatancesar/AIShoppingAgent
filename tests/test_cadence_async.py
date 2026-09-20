"""Testes unitários (`AsyncSession` mockada) de `app.collection.cadence`
(TASK-112 fase 3B / TASK-113 / TASK-116) -- mesmo padrão já estabelecido
em `tests/test_shared_collection_async.py`: `MagicMock`/`AsyncMock`
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
from app.collection.cadence import (
    STALE_GRACE_MULTIPLIER,
    CadenceConfig,
    CadenceDecision,
    OfferFreshnessStatus,
    _is_high_activity,
    _is_promo_calendar_active,
    _resolve_market_mode_scopes,
    _resolve_mode_window_start,
    _resolve_mode_window_starts_batch,
    resolve_collection_cadence,
    resolve_offer_freshness,
    resolve_offers_freshness_batch,
    resolve_product_market_mode,
    sample_next_run_at,
)
from app.collection.models import CollectionRunStatus
from app.collection.normalization import Availability

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _mock_async_session() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    return session


def _fake_execute_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    result.first.return_value = rows[0] if rows else None
    return result


def _fake_scalars_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


# ---------------------------------------------------------------------------
# CadenceConfig
# ---------------------------------------------------------------------------


def test_cadence_config_accepts_valid_defaults() -> None:
    config = CadenceConfig()

    assert config.normal_min_minutes == 45
    assert config.promo_min_minutes == 30


def test_cadence_config_rejects_non_positive_normal_min() -> None:
    with pytest.raises(ValueError, match="intervalos mínimos"):
        CadenceConfig(normal_min_minutes=0)


def test_cadence_config_rejects_non_positive_promo_min() -> None:
    with pytest.raises(ValueError, match="intervalos mínimos"):
        CadenceConfig(promo_min_minutes=0, promo_max_minutes=45)


def test_cadence_config_rejects_normal_max_below_min() -> None:
    with pytest.raises(ValueError, match="normal_max_minutes"):
        CadenceConfig(normal_min_minutes=50, normal_max_minutes=40)


def test_cadence_config_rejects_promo_max_below_min() -> None:
    with pytest.raises(ValueError, match="promo_max_minutes"):
        CadenceConfig(promo_min_minutes=40, promo_max_minutes=35)


def test_cadence_config_rejects_promo_min_below_floor() -> None:
    with pytest.raises(ValueError, match="piso de 30 minutos"):
        CadenceConfig(promo_min_minutes=20, promo_max_minutes=45)


def test_cadence_config_rejects_non_positive_high_activity_window() -> None:
    with pytest.raises(ValueError, match="high_activity_window_minutes"):
        CadenceConfig(high_activity_window_minutes=0)


def test_cadence_config_rejects_non_positive_high_activity_threshold() -> None:
    with pytest.raises(ValueError, match="high_activity_change_threshold"):
        CadenceConfig(high_activity_change_threshold=0)


def test_cadence_config_rejects_non_positive_high_activity_duration() -> None:
    with pytest.raises(ValueError, match="high_activity_duration_minutes"):
        CadenceConfig(high_activity_duration_minutes=0)


# ---------------------------------------------------------------------------
# resolve_collection_cadence
# ---------------------------------------------------------------------------


def test_resolve_collection_cadence_returns_promo_when_calendar_active(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.collection.cadence._is_promo_calendar_active", AsyncMock(return_value=True)
    )
    is_high_activity = AsyncMock(return_value=True)
    monkeypatch.setattr("app.collection.cadence._is_high_activity", is_high_activity)
    session = _mock_async_session()
    config = CadenceConfig()

    decision = asyncio.run(
        resolve_collection_cadence(
            session,
            store_id=uuid4(),
            scope_id=uuid4(),
            mission_ids=[uuid4()],
            now=NOW,
            config=config,
        )
    )

    assert decision == CadenceDecision(
        config.promo_min_minutes, config.promo_max_minutes, "promo_calendar"
    )
    is_high_activity.assert_not_awaited()


def test_resolve_collection_cadence_returns_high_activity_when_active(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.collection.cadence._is_promo_calendar_active",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.collection.cadence._is_high_activity", AsyncMock(return_value=True)
    )
    session = _mock_async_session()
    config = CadenceConfig()

    decision = asyncio.run(
        resolve_collection_cadence(
            session,
            store_id=uuid4(),
            scope_id=uuid4(),
            mission_ids=[uuid4()],
            now=NOW,
            config=config,
        )
    )

    assert decision == CadenceDecision(
        config.promo_min_minutes, config.promo_max_minutes, "high_activity"
    )


def test_resolve_collection_cadence_returns_normal_otherwise(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.collection.cadence._is_promo_calendar_active",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.collection.cadence._is_high_activity", AsyncMock(return_value=False)
    )
    session = _mock_async_session()
    config = CadenceConfig()

    decision = asyncio.run(
        resolve_collection_cadence(
            session,
            store_id=uuid4(),
            scope_id=uuid4(),
            mission_ids=[uuid4()],
            now=NOW,
            config=config,
        )
    )

    assert decision == CadenceDecision(
        config.normal_min_minutes, config.normal_max_minutes, "normal"
    )


# ---------------------------------------------------------------------------
# resolve_product_market_mode
# ---------------------------------------------------------------------------


def test_resolve_product_market_mode_returns_promo_when_calendar_active(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.collection.cadence._is_promo_calendar_active", AsyncMock(return_value=True)
    )
    session = _mock_async_session()
    config = CadenceConfig()

    decision = asyncio.run(
        resolve_product_market_mode(session, product_id=uuid4(), now=NOW, config=config)
    )

    assert decision.mode == "promo_calendar"
    session.scalars.assert_not_awaited()


def test_resolve_product_market_mode_returns_high_activity_when_any_scope_active(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.collection.cadence._is_promo_calendar_active",
        AsyncMock(return_value=False),
    )
    store_id_1, store_id_2 = uuid4(), uuid4()
    session = _mock_async_session()
    session.scalars.return_value = _fake_scalars_result([store_id_1, store_id_2])
    scopes_by_store = {
        store_id_1: [(uuid4(), [uuid4()])],
        store_id_2: [(uuid4(), [uuid4()])],
    }
    resolve_scopes = AsyncMock(
        side_effect=lambda session, *, product_id, store_id: scopes_by_store[store_id]
    )
    monkeypatch.setattr(
        "app.collection.cadence._resolve_market_mode_scopes", resolve_scopes
    )
    is_high_activity = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr("app.collection.cadence._is_high_activity", is_high_activity)
    config = CadenceConfig()

    decision = asyncio.run(
        resolve_product_market_mode(session, product_id=uuid4(), now=NOW, config=config)
    )

    assert decision.mode == "high_activity"
    assert is_high_activity.await_count == 2


def test_resolve_product_market_mode_returns_normal_when_no_scope_active(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.collection.cadence._is_promo_calendar_active",
        AsyncMock(return_value=False),
    )
    session = _mock_async_session()
    session.scalars.return_value = _fake_scalars_result([])
    config = CadenceConfig()

    decision = asyncio.run(
        resolve_product_market_mode(session, product_id=uuid4(), now=NOW, config=config)
    )

    assert decision.mode == "normal"


# ---------------------------------------------------------------------------
# _resolve_market_mode_scopes
# ---------------------------------------------------------------------------


def test_resolve_market_mode_scopes_groups_by_monitoring_item_when_linked() -> None:
    session = _mock_async_session()
    mission_id = uuid4()
    monitoring_item_id = uuid4()
    session.execute.return_value = _fake_execute_result(
        [(mission_id, monitoring_item_id)]
    )

    scopes = asyncio.run(
        _resolve_market_mode_scopes(session, product_id=uuid4(), store_id=uuid4())
    )

    assert scopes == [(monitoring_item_id, [mission_id])]


def test_resolve_market_mode_scopes_groups_by_mission_when_unlinked() -> None:
    session = _mock_async_session()
    mission_id = uuid4()
    session.execute.return_value = _fake_execute_result([(mission_id, None)])

    scopes = asyncio.run(
        _resolve_market_mode_scopes(session, product_id=uuid4(), store_id=uuid4())
    )

    assert scopes == [(mission_id, [mission_id])]


# ---------------------------------------------------------------------------
# sample_next_run_at
# ---------------------------------------------------------------------------


def test_sample_next_run_at_uses_uniform_within_decision_range(monkeypatch) -> None:
    monkeypatch.setattr("app.collection.cadence.uniform", lambda low, high: low)
    decision = CadenceDecision(45, 75, "normal")

    result = sample_next_run_at(NOW, decision)

    assert result == NOW + timedelta(minutes=45)


# ---------------------------------------------------------------------------
# _is_promo_calendar_active
# ---------------------------------------------------------------------------


def test_is_promo_calendar_active_true_when_window_found() -> None:
    session = _mock_async_session()
    session.scalar.return_value = uuid4()

    assert asyncio.run(_is_promo_calendar_active(session, now=NOW)) is True


def test_is_promo_calendar_active_false_when_no_window() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None

    assert asyncio.run(_is_promo_calendar_active(session, now=NOW)) is False


# ---------------------------------------------------------------------------
# _is_high_activity
# ---------------------------------------------------------------------------


def test_is_high_activity_true_when_state_still_within_window() -> None:
    session = _mock_async_session()
    session.get.return_value = SimpleNamespace(
        high_activity_until=NOW + timedelta(minutes=10)
    )
    config = CadenceConfig()

    result = asyncio.run(
        _is_high_activity(
            session,
            store_id=uuid4(),
            scope_id=uuid4(),
            mission_ids=[uuid4()],
            now=NOW,
            config=config,
        )
    )

    assert result is True
    session.execute.assert_not_awaited()


def test_is_high_activity_false_when_no_mission_ids() -> None:
    session = _mock_async_session()
    session.get.return_value = None
    config = CadenceConfig()

    result = asyncio.run(
        _is_high_activity(
            session,
            store_id=uuid4(),
            scope_id=uuid4(),
            mission_ids=[],
            now=NOW,
            config=config,
        )
    )

    assert result is False


def test_is_high_activity_false_when_change_count_below_threshold() -> None:
    session = _mock_async_session()
    session.get.return_value = None
    session.scalar.return_value = 1
    config = CadenceConfig(high_activity_change_threshold=3)

    result = asyncio.run(
        _is_high_activity(
            session,
            store_id=uuid4(),
            scope_id=uuid4(),
            mission_ids=[uuid4()],
            now=NOW,
            config=config,
        )
    )

    assert result is False
    session.execute.assert_not_awaited()


def test_is_high_activity_true_and_persists_state_when_threshold_reached() -> None:
    session = _mock_async_session()
    session.get.return_value = None
    session.scalar.return_value = 5
    config = CadenceConfig(high_activity_change_threshold=3)

    result = asyncio.run(
        _is_high_activity(
            session,
            store_id=uuid4(),
            scope_id=uuid4(),
            mission_ids=[uuid4()],
            now=NOW,
            config=config,
        )
    )

    assert result is True
    session.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# resolve_offer_freshness
# ---------------------------------------------------------------------------


def test_resolve_offer_freshness_never_confirmed_without_any_data() -> None:
    session = _mock_async_session()
    session.execute.side_effect = [
        _fake_execute_result([]),  # confirmation (shared_collection_offers)
        _fake_execute_result([]),  # fallback (price_observation)
    ]
    config = CadenceConfig()

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=config,
        )
    )

    assert status == OfferFreshnessStatus.NEVER_CONFIRMED


def test_resolve_offer_freshness_unavailable_from_run_confirmation() -> None:
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result(
        [(uuid4(), NOW - timedelta(hours=1), Availability.UNAVAILABLE)]
    )
    config = CadenceConfig()

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=config,
        )
    )

    assert status == OfferFreshnessStatus.UNAVAILABLE


def test_resolve_offer_freshness_unavailable_from_fallback_with_last_seen_at() -> None:
    session = _mock_async_session()
    observed_at = NOW - timedelta(hours=2)
    session.execute.side_effect = [
        _fake_execute_result([]),
        _fake_execute_result([(observed_at, Availability.UNAVAILABLE)]),
    ]
    session.scalar.return_value = NOW - timedelta(hours=1)
    config = CadenceConfig()

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=config,
        )
    )

    assert status == OfferFreshnessStatus.UNAVAILABLE


def test_resolve_offer_freshness_collection_failed_when_later_run_failed() -> None:
    session = _mock_async_session()
    confirmed_at = NOW - timedelta(hours=3)
    session.execute.return_value = _fake_execute_result(
        [(uuid4(), confirmed_at, Availability.AVAILABLE)]
    )
    session.scalar.return_value = uuid4()  # later_failed run found

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert status == OfferFreshnessStatus.COLLECTION_FAILED


def test_resolve_offer_freshness_missing_no_confirmation_when_run_succeeded_without_offer(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    confirmed_at = NOW - timedelta(hours=3)
    session.execute.return_value = _fake_execute_result(
        [(uuid4(), confirmed_at, Availability.AVAILABLE)]
    )
    session.scalar.side_effect = [None, uuid4()]

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert status == OfferFreshnessStatus.MISSING_NO_CONFIRMATION


def test_resolve_offer_freshness_confirmed_recent_under_normal_mode(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    confirmed_at = NOW - timedelta(minutes=30)
    session.execute.return_value = _fake_execute_result(
        [(uuid4(), confirmed_at, Availability.AVAILABLE)]
    )
    session.scalar.side_effect = [None, None]
    monkeypatch.setattr(
        "app.collection.cadence.resolve_product_market_mode",
        AsyncMock(return_value=CadenceDecision(45, 75, "normal")),
    )

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert status == OfferFreshnessStatus.CONFIRMED_RECENT


def test_resolve_offer_freshness_confirmed_stale_beyond_grace(monkeypatch) -> None:
    session = _mock_async_session()
    confirmed_at = NOW - timedelta(minutes=200)
    session.execute.return_value = _fake_execute_result(
        [(uuid4(), confirmed_at, Availability.AVAILABLE)]
    )
    session.scalar.side_effect = [None, None]
    monkeypatch.setattr(
        "app.collection.cadence.resolve_product_market_mode",
        AsyncMock(return_value=CadenceDecision(45, 75, "normal")),
    )

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert status == OfferFreshnessStatus.CONFIRMED_STALE
    assert STALE_GRACE_MULTIPLIER == 2


def test_resolve_offer_freshness_reacts_to_current_cadence_mode_not_frozen(
    monkeypatch,
) -> None:
    """Confirmação feita há 100min sob NORMAL (stale_after=150min, ainda
    recente); mas a janela acelerada (HIGH_ACTIVITY, stale_after=90min)
    começou DEPOIS da confirmação -- vira antiga porque o relógio do
    limiar mais curto conta a partir do início da janela, nunca da
    confirmação original."""
    session = _mock_async_session()
    confirmed_at = NOW - timedelta(minutes=100)
    session.execute.return_value = _fake_execute_result(
        [(uuid4(), confirmed_at, Availability.AVAILABLE)]
    )
    session.scalar.side_effect = [None, None]
    monkeypatch.setattr(
        "app.collection.cadence.resolve_product_market_mode",
        AsyncMock(return_value=CadenceDecision(30, 45, "high_activity")),
    )
    window_start = NOW - timedelta(minutes=80)
    monkeypatch.setattr(
        "app.collection.cadence._resolve_mode_window_start",
        AsyncMock(return_value=window_start),
    )

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    # stale_after = 2*45 = 90min; now - window_start(80min) = 80 <= 90 -> recent
    assert status == OfferFreshnessStatus.CONFIRMED_RECENT


def test_resolve_offer_freshness_uses_confirmed_at_when_window_start_is_none(
    monkeypatch,
) -> None:
    """Modo acelerado ativo mas sem janela agendada resolvível
    (`_resolve_mode_window_start` retorna `None`) -- cai no `else` e usa a
    própria confirmação como referência de "desde quando", sem cair no
    caminho de correção de início de janela."""
    session = _mock_async_session()
    confirmed_at = NOW - timedelta(minutes=200)
    session.execute.return_value = _fake_execute_result(
        [(uuid4(), confirmed_at, Availability.AVAILABLE)]
    )
    session.scalar.side_effect = [None, None]
    monkeypatch.setattr(
        "app.collection.cadence.resolve_product_market_mode",
        AsyncMock(return_value=CadenceDecision(30, 45, "high_activity")),
    )
    monkeypatch.setattr(
        "app.collection.cadence._resolve_mode_window_start",
        AsyncMock(return_value=None),
    )

    status = asyncio.run(
        resolve_offer_freshness(
            session,
            offer_id=uuid4(),
            product_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    # stale_after = 2*45 = 90min; now - confirmed_at(200min) > 90 -> stale
    assert status == OfferFreshnessStatus.CONFIRMED_STALE


# ---------------------------------------------------------------------------
# _resolve_mode_window_start
# ---------------------------------------------------------------------------


def test_resolve_mode_window_start_returns_promo_start_when_active() -> None:
    session = _mock_async_session()
    promo_start = NOW - timedelta(minutes=10)
    session.scalar.side_effect = [promo_start]

    result = asyncio.run(
        _resolve_mode_window_start(
            session, store_id=uuid4(), now=NOW, config=CadenceConfig()
        )
    )

    assert result == promo_start


def test_resolve_mode_window_start_returns_high_activity_start_when_active() -> None:
    session = _mock_async_session()
    high_activity_until = NOW + timedelta(minutes=20)
    session.scalar.side_effect = [None, high_activity_until]
    config = CadenceConfig(high_activity_duration_minutes=60)

    result = asyncio.run(
        _resolve_mode_window_start(session, store_id=uuid4(), now=NOW, config=config)
    )

    assert result == high_activity_until - timedelta(minutes=60)


def test_resolve_mode_window_start_returns_none_when_neither() -> None:
    session = _mock_async_session()
    session.scalar.side_effect = [None, None]

    result = asyncio.run(
        _resolve_mode_window_start(
            session, store_id=uuid4(), now=NOW, config=CadenceConfig()
        )
    )

    assert result is None


# ---------------------------------------------------------------------------
# _resolve_mode_window_starts_batch
# ---------------------------------------------------------------------------


def test_resolve_mode_window_starts_batch_returns_promo_for_all_when_active() -> None:
    session = _mock_async_session()
    promo_start = NOW - timedelta(minutes=5)
    session.scalar.return_value = promo_start
    store_id_1, store_id_2 = uuid4(), uuid4()

    result = asyncio.run(
        _resolve_mode_window_starts_batch(
            session, store_ids=[store_id_1, store_id_2], now=NOW, config=CadenceConfig()
        )
    )

    assert result == {store_id_1: promo_start, store_id_2: promo_start}


def test_resolve_mode_window_starts_batch_returns_per_store_high_activity_starts() -> (
    None
):
    session = _mock_async_session()
    store_id_1, store_id_2 = uuid4(), uuid4()
    high_activity_until_1 = NOW + timedelta(minutes=10)
    session.scalar.return_value = None
    session.execute.return_value = _fake_execute_result(
        [(store_id_1, high_activity_until_1)]
    )
    config = CadenceConfig(high_activity_duration_minutes=60)

    result = asyncio.run(
        _resolve_mode_window_starts_batch(
            session, store_ids=[store_id_1, store_id_2], now=NOW, config=config
        )
    )

    assert result == {
        store_id_1: high_activity_until_1 - timedelta(minutes=60),
        store_id_2: None,
    }


def test_resolve_mode_window_starts_batch_keeps_latest_when_multiple_rows() -> None:
    session = _mock_async_session()
    store_id = uuid4()
    earlier = NOW + timedelta(minutes=5)
    later = NOW + timedelta(minutes=20)
    session.scalar.return_value = None
    session.execute.return_value = _fake_execute_result(
        [(store_id, earlier), (store_id, later)]
    )
    config = CadenceConfig(high_activity_duration_minutes=60)

    result = asyncio.run(
        _resolve_mode_window_starts_batch(
            session, store_ids=[store_id], now=NOW, config=config
        )
    )

    assert result == {store_id: later - timedelta(minutes=60)}


# ---------------------------------------------------------------------------
# resolve_offers_freshness_batch
# ---------------------------------------------------------------------------


def test_resolve_offers_freshness_batch_returns_empty_for_no_offers() -> None:
    session = _mock_async_session()

    result = asyncio.run(
        resolve_offers_freshness_batch(
            session, offers=[], product_id=uuid4(), now=NOW, config=CadenceConfig()
        )
    )

    assert result == {}
    session.execute.assert_not_awaited()


def test_resolve_offers_freshness_batch_classifies_unavailable_from_confirmed() -> None:
    session = _mock_async_session()
    offer_id, store_id = uuid4(), uuid4()
    session.execute.return_value = _fake_execute_result(
        [(offer_id, NOW - timedelta(hours=1), Availability.UNAVAILABLE, 1)]
    )

    result = asyncio.run(
        resolve_offers_freshness_batch(
            session,
            offers=[(offer_id, store_id)],
            product_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert result == {offer_id: OfferFreshnessStatus.UNAVAILABLE}


def test_resolve_offers_freshness_batch_classifies_never_confirmed() -> None:
    session = _mock_async_session()
    offer_id, store_id = uuid4(), uuid4()
    session.execute.side_effect = [
        _fake_execute_result([]),  # confirmation_rows
        _fake_execute_result([]),  # obs_rows (fallback)
        _fake_execute_result([]),  # last_seen_rows
    ]

    result = asyncio.run(
        resolve_offers_freshness_batch(
            session,
            offers=[(offer_id, store_id)],
            product_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert result == {offer_id: OfferFreshnessStatus.NEVER_CONFIRMED}


def test_resolve_offers_freshness_batch_classifies_unavailable_from_fallback_observation() -> (
    None
):
    """Oferta sem confirmação por run (`confirmation_rows` vazio) cai no
    fallback bruto de `PriceObservation`; `Offer.last_seen_at` é mais
    recente que a observação e por isso vence o `max(...)`."""
    session = _mock_async_session()
    offer_id, store_id = uuid4(), uuid4()
    observed_at = NOW - timedelta(hours=3)
    last_seen_at = NOW - timedelta(hours=1)
    session.execute.side_effect = [
        _fake_execute_result([]),  # confirmation_rows
        _fake_execute_result(
            [(offer_id, observed_at, Availability.UNAVAILABLE, 1)]
        ),  # obs_rows (fallback)
        _fake_execute_result([(offer_id, last_seen_at)]),  # last_seen_rows
    ]

    result = asyncio.run(
        resolve_offers_freshness_batch(
            session,
            offers=[(offer_id, store_id)],
            product_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert result == {offer_id: OfferFreshnessStatus.UNAVAILABLE}


def test_resolve_offers_freshness_batch_classifies_collection_failed(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    offer_id, store_id = uuid4(), uuid4()
    confirmed_at = NOW - timedelta(hours=3)
    run_id = uuid4()
    session.execute.side_effect = [
        _fake_execute_result([(offer_id, confirmed_at, Availability.AVAILABLE, 1)]),
        _fake_execute_result(
            [
                (
                    store_id,
                    confirmed_at + timedelta(hours=1),
                    CollectionRunStatus.FAILED,
                    run_id,
                )
            ]
        ),
        _fake_execute_result([]),
    ]

    result = asyncio.run(
        resolve_offers_freshness_batch(
            session,
            offers=[(offer_id, store_id)],
            product_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert result == {offer_id: OfferFreshnessStatus.COLLECTION_FAILED}


def test_resolve_offers_freshness_batch_classifies_missing_no_confirmation() -> None:
    session = _mock_async_session()
    offer_id, store_id = uuid4(), uuid4()
    confirmed_at = NOW - timedelta(hours=3)
    run_id = uuid4()
    session.execute.side_effect = [
        _fake_execute_result([(offer_id, confirmed_at, Availability.AVAILABLE, 1)]),
        _fake_execute_result(
            [
                (
                    store_id,
                    confirmed_at + timedelta(hours=1),
                    CollectionRunStatus.SUCCEEDED,
                    run_id,
                )
            ]
        ),
        _fake_execute_result([]),  # confirmed_pairs -- run/offer não confirmado
    ]

    result = asyncio.run(
        resolve_offers_freshness_batch(
            session,
            offers=[(offer_id, store_id)],
            product_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert result == {offer_id: OfferFreshnessStatus.MISSING_NO_CONFIRMATION}


def test_resolve_offers_freshness_batch_classifies_confirmed_recent_and_stale(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    recent_offer, stale_offer, store_id = uuid4(), uuid4(), uuid4()
    recent_confirmed_at = NOW - timedelta(minutes=30)
    stale_confirmed_at = NOW - timedelta(minutes=200)
    session.execute.side_effect = [
        _fake_execute_result(
            [
                (recent_offer, recent_confirmed_at, Availability.AVAILABLE, 1),
                (stale_offer, stale_confirmed_at, Availability.AVAILABLE, 1),
            ]
        ),
        _fake_execute_result([]),  # nenhuma run FAILED/SUCCEEDED posterior
    ]
    monkeypatch.setattr(
        "app.collection.cadence.resolve_product_market_mode",
        AsyncMock(return_value=CadenceDecision(45, 75, "normal")),
    )

    result = asyncio.run(
        resolve_offers_freshness_batch(
            session,
            offers=[(recent_offer, store_id), (stale_offer, store_id)],
            product_id=uuid4(),
            now=NOW,
            config=CadenceConfig(),
        )
    )

    assert result[recent_offer] == OfferFreshnessStatus.CONFIRMED_RECENT
    assert result[stale_offer] == OfferFreshnessStatus.CONFIRMED_STALE
