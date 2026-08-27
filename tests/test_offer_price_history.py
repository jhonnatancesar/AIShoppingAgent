"""Aritmética de período e métricas de mercado do histórico de preço (TASK-098)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.offers.query import (
    _compute_metrics,
    _subtract_calendar_months,
    resolve_period_range,
)

_SAO_PAULO_OFFSET_HOURS = 3


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class TestSubtractCalendarMonths:
    def test_simple_subtraction_within_same_year(self) -> None:
        result = _subtract_calendar_months(_utc(2026, 8, 15), 1)
        assert result == _utc(2026, 7, 15)

    def test_crosses_year_boundary(self) -> None:
        result = _subtract_calendar_months(_utc(2026, 1, 10), 1)
        assert result == _utc(2025, 12, 10)

    def test_clamps_day_to_shorter_month_non_leap_year(self) -> None:
        result = _subtract_calendar_months(_utc(2026, 8, 31), 6)
        assert result == _utc(2026, 2, 28)

    def test_clamps_day_to_shorter_month_leap_year(self) -> None:
        result = _subtract_calendar_months(_utc(2024, 8, 31), 6)
        assert result == _utc(2024, 2, 29)

    def test_twelve_months_returns_same_month_previous_year(self) -> None:
        result = _subtract_calendar_months(_utc(2026, 8, 27), 12)
        assert result == _utc(2025, 8, 27)


class TestResolvePeriodRange:
    def test_all_period_has_no_lower_bound(self) -> None:
        now = _utc(2026, 8, 27, 18, 0)
        result = resolve_period_range("all", now=now)
        assert result.start_utc is None
        assert result.end_utc == now

    def test_naive_now_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_period_range("1d", now=datetime(2026, 8, 27, 18, 0))

    def test_one_day_starts_at_local_commercial_day_start(self) -> None:
        # 18h UTC == 15h em America/Sao_Paulo, mesmo dia local.
        now = _utc(2026, 8, 27, 18, 0)
        result = resolve_period_range("1d", now=now)
        assert result.start_utc == _utc(2026, 8, 27, _SAO_PAULO_OFFSET_HOURS, 0)
        assert result.end_utc == now

    def test_day_turnover_edge_case_utc_ahead_of_sao_paulo(self) -> None:
        """02h UTC de 27/08 == 23h de 26/08 em America/Sao_Paulo -- o dia
        comercial ainda é 26/08, nunca 27/08 (correção pós-plano, item 5)."""
        now = _utc(2026, 8, 27, 2, 0)
        result = resolve_period_range("1d", now=now)
        expected_start = _utc(2026, 8, 26, _SAO_PAULO_OFFSET_HOURS, 0)
        assert result.start_utc == expected_start
        assert result.start_utc < now

    def test_seven_days_includes_today_as_the_seventh(self) -> None:
        now = _utc(2026, 8, 27, 18, 0)
        result = resolve_period_range("7d", now=now)
        local_day_start = _utc(2026, 8, 21, _SAO_PAULO_OFFSET_HOURS, 0)
        assert result.start_utc == local_day_start
        span_days = (now - result.start_utc).days
        assert span_days == 6  # 21..27 inclusive == 7 dias comerciais

    def test_one_month_uses_real_calendar_arithmetic(self) -> None:
        now = _utc(2026, 8, 31, 12, 0)
        result = resolve_period_range("1m", now=now)
        assert result.start_utc == _utc(2026, 7, 31, _SAO_PAULO_OFFSET_HOURS, 0)

    def test_six_months_clamps_day_across_leap_boundary(self) -> None:
        now = _utc(2024, 8, 31, 12, 0)
        result = resolve_period_range("6m", now=now)
        assert result.start_utc == _utc(2024, 2, 29, _SAO_PAULO_OFFSET_HOURS, 0)

    def test_one_year_is_real_calendar_year_not_365_days(self) -> None:
        now = _utc(2024, 8, 31, 12, 0)  # ano bissexto, 2024 tem 366 dias
        result = resolve_period_range("1a", now=now)
        assert result.start_utc == _utc(2023, 8, 31, _SAO_PAULO_OFFSET_HOURS, 0)

    def test_unknown_period_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_period_range("2y", now=_utc(2026, 8, 27))  # type: ignore[arg-type]


def _row(
    day: date, amount: str
) -> tuple[object, str, str, date, Decimal, object, object]:
    return (uuid4(), "store", "Store", day, Decimal(amount), uuid4(), uuid4())


class TestComputeMetrics:
    def test_no_rows_returns_none_metrics_but_keeps_current_amount(self) -> None:
        metrics = _compute_metrics([], current_amount=Decimal("100.00"))
        assert metrics.current_amount == Decimal("100.00")
        assert metrics.min_amount is None
        assert metrics.max_amount is None
        assert metrics.average_amount is None
        assert metrics.variation_percent is None

    def test_average_uses_daily_market_low_never_raw_point_average(self) -> None:
        day1, day2 = date(2026, 7, 1), date(2026, 7, 2)
        rows = [
            _row(day1, "100.00"),
            _row(day1, "90.00"),  # dia 1: 2 lojas, mínimo do dia = 90
            _row(day2, "95.00"),  # dia 2: só 1 loja coletada
        ]
        metrics = _compute_metrics(rows, current_amount=Decimal("95.00"))
        # média ingênua de todos os pontos seria (100+90+95)/3 = 95.0 --
        # a média correta é sobre o mínimo diário: (90+95)/2 = 92.5
        assert metrics.average_amount == Decimal("92.5000")
        assert metrics.min_amount == Decimal("90.00")
        assert metrics.max_amount == Decimal("95.00")

    def test_variation_percent_uses_first_daily_low_as_baseline(self) -> None:
        day1, day2 = date(2026, 7, 1), date(2026, 7, 2)
        rows = [_row(day1, "90.00"), _row(day2, "95.00")]
        metrics = _compute_metrics(rows, current_amount=Decimal("100.00"))
        # (100 - 90) / 90 * 100 = 11.111... -> 11.11
        assert metrics.variation_percent == Decimal("11.11")

    def test_variation_percent_is_none_without_current_amount(self) -> None:
        rows = [_row(date(2026, 7, 1), "90.00")]
        metrics = _compute_metrics(rows, current_amount=None)
        assert metrics.current_amount is None
        assert metrics.variation_percent is None

    def test_variation_percent_is_none_when_baseline_is_zero_never_div_by_zero(
        self,
    ) -> None:
        rows = [_row(date(2026, 7, 1), "0.00")]
        metrics = _compute_metrics(rows, current_amount=Decimal("10.00"))
        assert metrics.variation_percent is None
