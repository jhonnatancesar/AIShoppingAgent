"""Testes unitários (lógica pura, sem `AsyncSession`/rede) do TASK-113.

Cobre só as funções deliberadamente puras/quase-puras de
`app.market_research.service` (gatilho, TTL/snapshot puro, construção de
evidência, parsing de payload da IA e `finalize_market_research`) --
mesma disciplina do módulo: "para serem testáveis sem rede" (docstring
do próprio arquivo). O fluxo real com `AsyncSession`/`WebSearchManager`/
IA tem cobertura própria em `tests/integration/test_market_research.py`
contra Postgres real.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.market_research.models import (
    AssessmentConfidence,
    MarketAssessmentStatus,
    MarketPriceClassification,
)
from app.market_research.service import (
    AssessmentSnapshot,
    EvidenceItem,
    TriggerSignals,
    _ai_evidence_item,
    _bounded_ai_evidence_text,
    _distinct_domain_urls,
    _domain,
    _evidence_matches_identity,
    _finalize_confidence,
    _is_material_improvement,
    _parse_classification,
    _parse_confidence,
    _parse_optional_date,
    _parse_optional_decimal,
    _snapshot_from_row,
    build_history_query,
    build_market_query,
    finalize_market_research,
    needs_refresh,
    product_query_label,
    should_trigger_market_research,
)
from app.products.models import Product

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def _signals(**overrides: object) -> TriggerSignals:
    base = dict(
        current_amount=Decimal("100"),
        previous_amount=None,
        best_notified_amount=None,
        internal_historical_best=None,
        target_amount=None,
        rearmed_at=None,
        last_notified_at=None,
        has_valid_cached_assessment=False,
    )
    base.update(overrides)
    return TriggerSignals(**base)


# ---------------------------------------------------------------------------
# should_trigger_market_research / _is_material_improvement
# ---------------------------------------------------------------------------


def test_should_trigger_market_research_false_when_cache_valid():
    signals = _signals(has_valid_cached_assessment=True, previous_amount=Decimal("1"))
    assert (
        should_trigger_market_research(
            signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
        )
        is False
    )


def test_should_trigger_market_research_price_drop_signal():
    signals = _signals(
        current_amount=Decimal("90"),
        previous_amount=Decimal("100"),
    )
    assert should_trigger_market_research(
        signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
    )


def test_should_trigger_market_research_previous_amount_zero_never_divides():
    signals = _signals(current_amount=Decimal("0"), previous_amount=Decimal("0"))
    assert (
        should_trigger_market_research(
            signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
        )
        is False
    )


def test_should_trigger_market_research_internal_historical_best_material_improvement():
    signals = _signals(
        current_amount=Decimal("50"), internal_historical_best=Decimal("100")
    )
    assert should_trigger_market_research(
        signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
    )


def test_should_trigger_market_research_best_notified_amount_material_improvement():
    signals = _signals(
        current_amount=Decimal("50"), best_notified_amount=Decimal("100")
    )
    assert should_trigger_market_research(
        signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
    )


def test_should_trigger_market_research_rearmed_after_realert_window():
    signals = _signals(
        rearmed_at=NOW - timedelta(hours=2),
        last_notified_at=NOW - timedelta(hours=2),
    )
    assert should_trigger_market_research(
        signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
    )


def test_should_trigger_market_research_rearmed_before_realert_window_is_false():
    signals = _signals(
        rearmed_at=NOW - timedelta(minutes=10),
        last_notified_at=NOW - timedelta(minutes=10),
    )
    assert (
        should_trigger_market_research(
            signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
        )
        is False
    )


def test_should_trigger_market_research_target_reached_without_prior_notification():
    signals = _signals(
        current_amount=Decimal("80"),
        target_amount=Decimal("100"),
        best_notified_amount=None,
    )
    assert should_trigger_market_research(
        signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
    )


def test_should_trigger_market_research_target_reached_but_already_notified_is_false():
    # current > best_notified_amount (sem melhoria material sobre o já
    # notificado) -- isola o branch "alvo atingido" dos outros 4 sinais.
    signals = _signals(
        current_amount=Decimal("95"),
        target_amount=Decimal("100"),
        best_notified_amount=Decimal("90"),
    )
    assert (
        should_trigger_market_research(
            signals, settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
        )
        is False
    )


def test_should_trigger_market_research_no_signal_is_false():
    assert (
        should_trigger_market_research(
            _signals(), settings=_settings(), now=NOW, realert_window=timedelta(hours=1)
        )
        is False
    )


def test_is_material_improvement_delegates_to_shared_formula():
    assert _is_material_improvement(
        reference_amount=Decimal("100"),
        current_amount=Decimal("50"),
        settings=_settings(),
    )
    assert not _is_material_improvement(
        reference_amount=Decimal("100"),
        current_amount=Decimal("99.50"),
        settings=_settings(),
    )


# ---------------------------------------------------------------------------
# AssessmentSnapshot / needs_refresh / _snapshot_from_row
# ---------------------------------------------------------------------------


def _snapshot(**overrides: object) -> AssessmentSnapshot:
    base = dict(
        product_id=uuid4(),
        status=MarketAssessmentStatus.READY,
        classification=MarketPriceClassification.GOOD_DEAL,
        market_low=Decimal("10"),
        market_high=Decimal("20"),
        historical_low_external=None,
        historical_low_source=None,
        historical_low_observed_at=None,
        confidence=AssessmentConfidence.MEDIUM,
        reference_price=Decimal("15"),
        expires_at=NOW + timedelta(hours=1),
    )
    base.update(overrides)
    return AssessmentSnapshot(**base)


def test_assessment_snapshot_is_good_or_excellent():
    assert _snapshot(
        classification=MarketPriceClassification.EXCELLENT_DEAL
    ).is_good_or_excellent
    assert not _snapshot(
        classification=MarketPriceClassification.NORMAL_PRICE
    ).is_good_or_excellent
    assert not _snapshot(classification=None).is_good_or_excellent


def test_assessment_snapshot_is_valid_requires_ready_and_unexpired():
    assert _snapshot().is_valid(now=NOW)
    assert not _snapshot(status=MarketAssessmentStatus.FAILED).is_valid(now=NOW)
    assert not _snapshot(expires_at=None).is_valid(now=NOW)
    assert not _snapshot(expires_at=NOW - timedelta(minutes=1)).is_valid(now=NOW)


def test_snapshot_from_row_maps_every_field():
    row = _FakeAssessmentRow()
    snapshot = _snapshot_from_row(row)
    assert snapshot.product_id == row.product_id
    assert snapshot.status == row.status
    assert snapshot.classification == row.classification
    assert snapshot.market_low == row.market_low
    assert snapshot.reference_price == row.reference_price
    assert snapshot.expires_at == row.expires_at


class _FakeAssessmentRow:
    def __init__(self) -> None:
        self.product_id = uuid4()
        self.status = MarketAssessmentStatus.READY
        self.classification = MarketPriceClassification.GOOD_DEAL
        self.market_low = Decimal("10")
        self.market_high = Decimal("20")
        self.historical_low_external = None
        self.historical_low_source = None
        self.historical_low_observed_at = None
        self.confidence = AssessmentConfidence.HIGH
        self.reference_price = Decimal("15")
        self.expires_at = NOW + timedelta(hours=1)


def test_needs_refresh_true_when_no_snapshot():
    assert needs_refresh(
        None, current_amount=Decimal("10"), now=NOW, settings=_settings()
    )


def test_needs_refresh_true_when_snapshot_invalid():
    stale = _snapshot(expires_at=NOW - timedelta(minutes=1))
    assert needs_refresh(
        stale, current_amount=Decimal("10"), now=NOW, settings=_settings()
    )


def test_needs_refresh_false_when_reference_price_not_positive():
    zeroed = _snapshot(reference_price=Decimal("0"))
    assert not needs_refresh(
        zeroed, current_amount=Decimal("999"), now=NOW, settings=_settings()
    )


def test_needs_refresh_true_when_price_variation_exceeds_threshold():
    snapshot = _snapshot(reference_price=Decimal("100"))
    assert needs_refresh(
        snapshot,
        current_amount=Decimal("50"),
        now=NOW,
        settings=_settings(market_assessment_price_refresh_percent=0.05),
    )


def test_needs_refresh_false_when_price_stable():
    snapshot = _snapshot(reference_price=Decimal("100"))
    assert not needs_refresh(
        snapshot,
        current_amount=Decimal("101"),
        now=NOW,
        settings=_settings(market_assessment_price_refresh_percent=0.05),
    )


# ---------------------------------------------------------------------------
# _domain / _evidence_matches_identity / query builders / _distinct_domain_urls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.pichau.com.br/produto/1", "pichau.com.br"),
        ("https://terabyteshop.com.br/x", "terabyteshop.com.br"),
        ("not a url \t\n", ""),
    ],
)
def test_domain_strips_www_and_fails_closed(url, expected):
    assert _domain(url) == expected


def test_domain_fails_closed_when_urlparse_raises_value_error():
    # `urlparse` levanta `ValueError` para IPv6 malformado (confirmado
    # empiricamente) -- único jeito real de exercitar o `except ValueError`
    # de `_domain`, distinto do caso "não é uma URL" acima (que só produz
    # netloc vazio, sem levantar exceção).
    assert _domain("http://[::1") == ""


def _product(*, identity_key: str | None = "v1:asus-tuf-b650") -> Product:
    return Product(
        id=uuid4(),
        name="ASUS TUF Gaming B650-Plus WiFi",
        display_name="ASUS TUF Gaming B650-Plus WiFi",
        family_key="asus-tuf-b650",
        identity_key=identity_key,
    )


def test_evidence_matches_identity_false_when_product_has_no_identity_key():
    product = _product(identity_key=None)
    assert not _evidence_matches_identity(
        product, title="qualquer coisa", description=None
    )


def test_evidence_matches_identity_false_when_text_does_not_resolve(monkeypatch):
    monkeypatch.setattr(
        "app.market_research.service.resolve_product_variant", lambda _text: None
    )
    product = _product()
    assert not _evidence_matches_identity(
        product, title="irrelevante", description=None
    )


def test_evidence_matches_identity_true_when_resolved_identity_key_matches(monkeypatch):
    resolved = type("Resolved", (), {"identity_key": "v1:asus-tuf-b650"})()
    monkeypatch.setattr(
        "app.market_research.service.resolve_product_variant", lambda _text: resolved
    )
    product = _product(identity_key="v1:asus-tuf-b650")
    assert _evidence_matches_identity(
        product, title="ASUS TUF B650", description="ficha"
    )


def test_evidence_matches_identity_false_when_resolved_identity_key_diverges(
    monkeypatch,
):
    resolved = type("Resolved", (), {"identity_key": "v1:another-board"})()
    monkeypatch.setattr(
        "app.market_research.service.resolve_product_variant", lambda _text: resolved
    )
    product = _product(identity_key="v1:asus-tuf-b650")
    assert not _evidence_matches_identity(
        product, title="outra placa", description=None
    )


def test_product_query_label_prefers_display_name():
    assert product_query_label(_product()) == "ASUS TUF Gaming B650-Plus WiFi"
    only_name = Product(id=uuid4(), name="Nome cru", display_name=None)
    assert product_query_label(only_name) == "Nome cru"


def test_build_market_and_history_queries_include_product_label():
    product = _product()
    assert product_query_label(product) in build_market_query(product)
    assert "preço comprar loja" in build_market_query(product)
    assert product_query_label(product) in build_history_query(product)
    assert "histórico" in build_history_query(product)


class _FakeSearchResult:
    def __init__(self, url: str) -> None:
        self.url = url


def test_distinct_domain_urls_dedupes_by_domain_and_respects_limit():
    results = (
        _FakeSearchResult("https://www.pichau.com.br/a"),
        _FakeSearchResult("https://pichau.com.br/b"),
        _FakeSearchResult("https://terabyteshop.com.br/c"),
        _FakeSearchResult("https://kabum.com.br/d"),
    )
    urls = _distinct_domain_urls(results, limit=2)
    assert urls == (
        "https://www.pichau.com.br/a",
        "https://terabyteshop.com.br/c",
    )


def test_distinct_domain_urls_skips_unparseable_urls():
    results = (_FakeSearchResult("not a url \t\n"),)
    assert _distinct_domain_urls(results, limit=3) == ()


# ---------------------------------------------------------------------------
# Parsing tolerante do payload da IA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("10.50", Decimal("10.50")),
        (10, Decimal("10")),
        ("not-a-number", None),
        ({"unhashable": "shape"}, None),
    ],
)
def test_parse_optional_decimal(value, expected):
    result = _parse_optional_decimal(value)
    if expected is None:
        assert result is None
    else:
        assert result == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("2026-09-14", __import__("datetime").date(2026, 9, 14)),
        ("not-a-date", None),
        (42, None),
    ],
)
def test_parse_optional_date(value, expected):
    assert _parse_optional_date(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (123, None),
        ("good_deal", MarketPriceClassification.GOOD_DEAL),
        ("not-a-classification", None),
    ],
)
def test_parse_classification(value, expected):
    assert _parse_classification(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (123, None),
        ("high", AssessmentConfidence.HIGH),
        ("not-a-confidence", None),
    ],
)
def test_parse_confidence(value, expected):
    assert _parse_confidence(value) == expected


def test_bounded_ai_evidence_text_none_passthrough():
    assert _bounded_ai_evidence_text(None, limit=10) is None


def test_bounded_ai_evidence_text_short_text_unchanged():
    assert _bounded_ai_evidence_text("  curto  ", limit=100) == "curto"


def test_bounded_ai_evidence_text_truncates_preserving_head_and_tail():
    text = "A" * 30 + "B" * 30
    bounded = _bounded_ai_evidence_text(text, limit=50)
    assert len(bounded) <= 50 + len("\n[conteúdo intermediário omitido]\n")
    assert bounded.startswith("A")
    assert bounded.endswith("B")
    assert "[conteúdo intermediário omitido]" in bounded


def test_ai_evidence_item_bounds_title_and_description():
    item = EvidenceItem(
        url="https://pichau.com.br/x",
        domain="pichau.com.br",
        title="Título",
        description="Desc",
    )
    payload = _ai_evidence_item(item)
    assert payload == {"url": item.url, "title": "Título", "description": "Desc"}


# ---------------------------------------------------------------------------
# _finalize_confidence / finalize_market_research
# ---------------------------------------------------------------------------


def test_finalize_confidence_caps_at_medium_with_two_domains():
    assert (
        _finalize_confidence(ai_confidence=AssessmentConfidence.HIGH, domain_count=2)
        is AssessmentConfidence.MEDIUM
    )


def test_finalize_confidence_allows_high_with_three_domains():
    assert (
        _finalize_confidence(ai_confidence=AssessmentConfidence.HIGH, domain_count=3)
        is AssessmentConfidence.HIGH
    )


def test_finalize_confidence_never_promotes_beyond_what_ai_claimed():
    assert (
        _finalize_confidence(ai_confidence=AssessmentConfidence.LOW, domain_count=5)
        is AssessmentConfidence.LOW
    )


def test_finalize_confidence_defaults_to_medium_when_ai_silent():
    assert (
        _finalize_confidence(ai_confidence=None, domain_count=5)
        is AssessmentConfidence.MEDIUM
    )


def _evidence(url: str, domain: str) -> EvidenceItem:
    return EvidenceItem(url=url, domain=domain, title="t", description=None)


def test_finalize_market_research_insufficient_evidence_without_quorum():
    outcome = finalize_market_research(
        {"classification": "good_deal", "market_low": "10", "market_high": "20"},
        market_evidence=(_evidence("https://a.com/1", "a.com"),),
        history_evidence=(),
        settings=_settings(),
    )
    assert outcome.classification is MarketPriceClassification.INSUFFICIENT_EVIDENCE
    assert outcome.market_low is None
    assert outcome.confidence is None


def test_finalize_market_research_insufficient_evidence_when_payload_none():
    outcome = finalize_market_research(
        None,
        market_evidence=(
            _evidence("https://a.com/1", "a.com"),
            _evidence("https://b.com/1", "b.com"),
        ),
        history_evidence=(),
        settings=_settings(),
    )
    assert outcome.classification is MarketPriceClassification.INSUFFICIENT_EVIDENCE
    assert outcome.evidence["ai_payload"] is None


def test_finalize_market_research_accepts_valid_quorum_and_prices():
    outcome = finalize_market_research(
        {
            "classification": "good_deal",
            "market_low": "10",
            "market_high": "20",
            "confidence": "high",
        },
        market_evidence=(
            _evidence("https://a.com/1", "a.com"),
            _evidence("https://b.com/1", "b.com"),
        ),
        history_evidence=(),
        settings=_settings(),
    )
    assert outcome.classification is MarketPriceClassification.GOOD_DEAL
    assert outcome.market_low == Decimal("10")
    assert outcome.market_high == Decimal("20")
    # só 2 domínios -- confidence capado em MEDIUM mesmo IA dizendo "high".
    assert outcome.confidence is AssessmentConfidence.MEDIUM


def test_finalize_market_research_rejects_inverted_low_high():
    outcome = finalize_market_research(
        {"classification": "good_deal", "market_low": "20", "market_high": "10"},
        market_evidence=(
            _evidence("https://a.com/1", "a.com"),
            _evidence("https://b.com/1", "b.com"),
        ),
        history_evidence=(),
        settings=_settings(),
    )
    assert outcome.market_low is None
    assert outcome.market_high is None
    assert outcome.classification is MarketPriceClassification.INSUFFICIENT_EVIDENCE


def test_finalize_market_research_falls_back_to_insufficient_evidence_on_bad_classification():
    outcome = finalize_market_research(
        {
            "classification": "not-a-real-classification",
            "market_low": "10",
            "market_high": "20",
        },
        market_evidence=(
            _evidence("https://a.com/1", "a.com"),
            _evidence("https://b.com/1", "b.com"),
        ),
        history_evidence=(),
        settings=_settings(),
    )
    assert outcome.classification is MarketPriceClassification.INSUFFICIENT_EVIDENCE


def test_finalize_market_research_historical_low_requires_url_in_history_evidence():
    history = (_evidence("https://hist.com/1", "hist.com"),)
    outcome = finalize_market_research(
        {
            "historical_low_external": "50",
            "historical_low_source": "https://hist.com/1",
            "historical_low_observed_at": "2026-01-01",
        },
        market_evidence=(),
        history_evidence=history,
        settings=_settings(),
    )
    assert outcome.historical_low_external == Decimal("50")
    assert outcome.historical_low_source == "https://hist.com/1"
    assert outcome.historical_low_observed_at == __import__("datetime").date(2026, 1, 1)


def test_finalize_market_research_historical_low_rejected_when_source_not_in_evidence():
    history = (_evidence("https://hist.com/1", "hist.com"),)
    outcome = finalize_market_research(
        {
            "historical_low_external": "50",
            "historical_low_source": "https://not-in-evidence.com/1",
        },
        market_evidence=(),
        history_evidence=history,
        settings=_settings(),
    )
    assert outcome.historical_low_external is None
    assert outcome.historical_low_source is None


def test_finalize_market_research_no_history_evidence_skips_historical_low():
    outcome = finalize_market_research(
        {
            "historical_low_external": "50",
            "historical_low_source": "https://hist.com/1",
        },
        market_evidence=(),
        history_evidence=(),
        settings=_settings(),
    )
    assert outcome.historical_low_external is None


def test_finalize_market_research_records_evidence_for_audit():
    market = (_evidence("https://a.com/1", "a.com"),)
    history = (_evidence("https://hist.com/1", "hist.com"),)
    outcome = finalize_market_research(
        None, market_evidence=market, history_evidence=history, settings=_settings()
    )
    assert outcome.evidence["market_evidence"] == [
        {"url": "https://a.com/1", "domain": "a.com", "title": "t"}
    ]
    assert outcome.evidence["history_evidence"] == [
        {"url": "https://hist.com/1", "domain": "hist.com", "title": "t"}
    ]
