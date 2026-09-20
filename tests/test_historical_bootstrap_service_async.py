"""Testes unitários (`AsyncSession`/`AIProviderManager`/`WebSearchManager`
mockados) das funções `async def` de `app.historical_bootstrap.service`
(FASE F1) -- mesmo padrão já estabelecido em `tests/test_shared_claim_async.py`
e `tests/test_missions_monitoring.py`: `MagicMock`/`AsyncMock` configurado via
`side_effect`/`return_value`, verificando comportamento real (branches,
`side_effect` sequenciado, chamadas mockadas com `assert_awaited_once`,
exceções via `pytest.raises`/tratamento interno) -- nenhum teste raso de
execução de linha. `_source`/`_parse_candidate`/`_match` (lógica pura) já
estão cobertos em `tests/test_historical_bootstrap_service.py`; aqui eles são
monkeypatchados quando não são o alvo direto do teste, para isolar a lógica
de orquestração sob teste."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.historical_bootstrap.models import (
    ExternalPriceReference,
    HistoricalBootstrap,
    HistoricalBootstrapStatus,
)
from app.historical_bootstrap.service import (
    HistoricalCandidate,
    _collect_candidates,
    _interpret_ambiguous,
    _mark_bootstrap_failed,
    _Match,
    _run_historical_bootstrap,
    get_external_price_reference_evidence,
    internal_history_is_sufficient,
    run_historical_bootstrap,
)
from app.products.models import Product
from app.search.cesar_core_fetch import CesarCoreFetchError, CesarCoreFetchResult
from app.search.contracts import WebSearchResponse, WebSearchResult
from app.users.models import UserRole
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


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
    session.begin = MagicMock(side_effect=lambda: _async_cm())
    return session


def _session_factory(*sessions: MagicMock) -> MagicMock:
    return MagicMock(side_effect=list(sessions))


def _product(
    *,
    identity_key: str | None = "v1:asus-tuf-b650",
    family_key: str | None = "asus-tuf-b650",
    category: str = "gpu",
    variant: str | None = None,
    attributes: dict[str, str] | None = None,
    display_name: str | None = "ASUS TUF Gaming B650-Plus",
) -> Product:
    return Product(
        id=uuid4(),
        name="ASUS TUF Gaming B650-Plus",
        display_name=display_name,
        category=category,
        variant=variant,
        attributes=attributes or {},
        family_key=family_key,
        identity_key=identity_key,
    )


def _search_result(
    url: str, *, title: str = "Título", snippet: str = "Trecho"
) -> WebSearchResult:
    return WebSearchResult(title=title, url=url, snippet=snippet)


def _search_response(results: list[WebSearchResult]) -> WebSearchResponse:
    return WebSearchResponse(
        results=tuple(results),
        provider="cesar_core",
        source="search",
        correlation_id="c1",
    )


def _candidate(**overrides) -> HistoricalCandidate:
    base = {
        "source": "pichau.com.br",
        "amount": Decimal("199.90"),
        "historical_date": None,
        "safe_url": "https://pichau.com.br/x",
        "store_name": None,
        "identity_text": "ASUS TUF B650 texto",
    }
    base.update(overrides)
    return HistoricalCandidate(**base)


def _match_result(*, kind: str = "exact", attributes: tuple = ()) -> _Match:
    resolved = MagicMock()
    resolved.attributes = attributes
    return _Match(kind=kind, resolved=resolved)


# ---------------------------------------------------------------------------
# get_external_price_reference_evidence
# ---------------------------------------------------------------------------


def test_get_external_price_reference_evidence_returns_scalar_result():
    async def _scenario():
        session = _mock_async_session()
        reference = MagicMock(spec=ExternalPriceReference)
        session.scalar.return_value = reference

        result = await get_external_price_reference_evidence(
            session, product_id=uuid4(), currency="BRL"
        )

        assert result is reference
        session.scalar.assert_awaited_once()

    asyncio.run(_scenario())


def test_get_external_price_reference_evidence_returns_none_when_absent():
    async def _scenario():
        session = _mock_async_session()
        session.scalar.return_value = None

        result = await get_external_price_reference_evidence(
            session, product_id=uuid4(), currency="BRL"
        )

        assert result is None

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# internal_history_is_sufficient
# ---------------------------------------------------------------------------


def _history_row(first, last_seen, stores):
    result = MagicMock()
    result.one.return_value = (first, last_seen, stores)
    return result


def test_internal_history_is_sufficient_true_with_30_days_and_2_stores():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _history_row(
            NOW - timedelta(days=31), NOW - timedelta(days=1), 2
        )

        assert (
            await internal_history_is_sufficient(session, product_id=uuid4(), now=NOW)
            is True
        )

    asyncio.run(_scenario())


def test_internal_history_is_sufficient_false_when_no_data():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _history_row(None, None, 0)

        assert (
            await internal_history_is_sufficient(session, product_id=uuid4(), now=NOW)
            is False
        )

    asyncio.run(_scenario())


def test_internal_history_is_sufficient_false_when_only_one_store():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _history_row(
            NOW - timedelta(days=40), NOW - timedelta(days=1), 1
        )

        assert (
            await internal_history_is_sufficient(session, product_id=uuid4(), now=NOW)
            is False
        )

    asyncio.run(_scenario())


def test_internal_history_is_sufficient_false_when_coverage_below_30_days():
    async def _scenario():
        session = _mock_async_session()
        session.execute.return_value = _history_row(
            NOW - timedelta(days=10), NOW - timedelta(days=1), 2
        )

        assert (
            await internal_history_is_sufficient(session, product_id=uuid4(), now=NOW)
            is False
        )

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# _interpret_ambiguous
# ---------------------------------------------------------------------------


def _ai(content: dict | str) -> MagicMock:
    ai = MagicMock()
    payload = content if isinstance(content, str) else json.dumps(content)
    ai.generate = AsyncMock(return_value=MagicMock(content=payload))
    return ai


def test_interpret_ambiguous_returns_candidate_on_exact_match(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match",
            lambda _product, _text: _match_result(kind="exact"),
        )
        ai = _ai(
            {
                "product_identity_text": "ASUS TUF B650 Gigabyte",
                "price_brl": "1999.90",
                "date": "2026-01-15",
            }
        )

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="Título",
            content="Conteúdo",
            url="https://pichau.com.br/x",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is not None
        assert result.source == "pichau.com.br"
        assert result.amount == Decimal("1999.90")
        assert result.historical_date == date(2026, 1, 15)
        assert result.identity_text == "ASUS TUF B650 Gigabyte"
        assert result.match_kind == "exact"
        ai.generate.assert_awaited_once()
        request = ai.generate.call_args.args[0]
        assert request.purpose == "historical_price_bootstrap"
        assert request.profile == UserRole.USER
        assert request.requested_at == NOW
        assert len(request.messages) == 2

    asyncio.run(_scenario())


def test_interpret_ambiguous_returns_none_when_identity_not_string(monkeypatch):
    async def _scenario():
        called = MagicMock()
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match", lambda *a, **k: called(*a, **k)
        )
        ai = _ai({"product_identity_text": None, "price_brl": "10,00", "date": None})

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="t",
            content="c",
            url="https://x.com/y",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is None
        called.assert_not_called()

    asyncio.run(_scenario())


def test_interpret_ambiguous_returns_none_when_match_is_none(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match", lambda _product, _text: None
        )
        ai = _ai(
            {
                "product_identity_text": "Outro produto",
                "price_brl": "10.00",
                "date": "2026-01-01",
            }
        )

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="t",
            content="c",
            url="https://x.com/y",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is None

    asyncio.run(_scenario())


def test_interpret_ambiguous_returns_none_when_amount_negative(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match",
            lambda _product, _text: _match_result(),
        )
        ai = _ai(
            {
                "product_identity_text": "ASUS TUF B650",
                "price_brl": "-1.00",
                "date": "2026-01-01",
            }
        )

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="t",
            content="c",
            url="https://x.com/y",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is None

    asyncio.run(_scenario())


def test_interpret_ambiguous_returns_none_on_missing_price_key(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match",
            lambda _product, _text: _match_result(),
        )
        ai = _ai({"product_identity_text": "ASUS TUF B650", "date": "2026-01-01"})

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="t",
            content="c",
            url="https://x.com/y",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is None

    asyncio.run(_scenario())


def test_interpret_ambiguous_returns_none_on_invalid_date(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match",
            lambda _product, _text: _match_result(),
        )
        ai = _ai(
            {
                "product_identity_text": "ASUS TUF B650",
                "price_brl": "10.00",
                "date": "não é data",
            }
        )

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="t",
            content="c",
            url="https://x.com/y",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is None

    asyncio.run(_scenario())


def test_interpret_ambiguous_returns_none_on_invalid_decimal(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match",
            lambda _product, _text: _match_result(),
        )
        ai = _ai(
            {
                "product_identity_text": "ASUS TUF B650",
                "price_brl": "abc",
                "date": "2026-01-01",
            }
        )

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="t",
            content="c",
            url="https://x.com/y",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is None

    asyncio.run(_scenario())


def test_interpret_ambiguous_returns_none_on_invalid_json():
    async def _scenario():
        ai = _ai("isto não é json")

        result = await _interpret_ambiguous(
            ai,
            product=_product(),
            title="t",
            content="c",
            url="https://x.com/y",
            profile=UserRole.USER,
            now=NOW,
        )

        assert result is None

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# _collect_candidates
# ---------------------------------------------------------------------------


def _search_manager(*responses: WebSearchResponse) -> MagicMock:
    manager = MagicMock()
    manager.search = AsyncMock(side_effect=list(responses))
    return manager


def test_collect_candidates_issues_two_queries_for_special_category(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", lambda **_kwargs: None
        )
        search = _search_manager(_search_response([]), _search_response([]))

        result = await _collect_candidates(
            _product(category="gpu"),
            search=search,
            fetch=None,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()
        assert search.search.await_count == 2

    asyncio.run(_scenario())


def test_collect_candidates_issues_single_query_for_generic_category(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", lambda **_kwargs: None
        )
        search = _search_manager(_search_response([]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=None,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()
        assert search.search.await_count == 1

    asyncio.run(_scenario())


def test_collect_candidates_deduplicates_repeated_urls(monkeypatch):
    async def _scenario():
        parse = MagicMock(return_value=None)
        monkeypatch.setattr("app.historical_bootstrap.service._parse_candidate", parse)
        result1 = _search_result("https://a.com/1")
        result2 = _search_result("https://a.com/1")
        search = _search_manager(_search_response([result1, result2]))

        await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=None,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert parse.call_count == 1

    asyncio.run(_scenario())


def test_collect_candidates_skips_unsafe_urls(monkeypatch):
    async def _scenario():
        parse = MagicMock(return_value=None)
        monkeypatch.setattr("app.historical_bootstrap.service._parse_candidate", parse)
        unsafe_url = "https://a.com/1?signature=abc&x-amz-signature=abc"
        search = _search_manager(_search_response([_search_result(unsafe_url)]))

        await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=None,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        parse.assert_not_called()

    asyncio.run(_scenario())


def test_collect_candidates_accepts_search_only_match_without_fetch(monkeypatch):
    async def _scenario():
        candidate = _candidate(identity_text="texto")
        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate",
            lambda **_kwargs: candidate,
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match",
            lambda _product, _text: _match_result(
                kind="family", attributes=(("brand", "x"),)
            ),
        )
        fetch = MagicMock()
        fetch.scrape_basic = AsyncMock()
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=fetch,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert len(result) == 1
        assert result[0].match_kind == "family"
        assert result[0].evidence_attributes == (("brand", "x"),)
        fetch.scrape_basic.assert_not_awaited()

    asyncio.run(_scenario())


def test_collect_candidates_skips_when_no_match_and_fetch_unavailable(monkeypatch):
    async def _scenario():
        candidate = _candidate()
        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate",
            lambda **_kwargs: candidate,
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match", lambda *_a, **_k: None
        )
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=None,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()

    asyncio.run(_scenario())


def test_collect_candidates_skips_when_parse_fails_and_fetch_unavailable(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", lambda **_kwargs: None
        )
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=None,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()

    asyncio.run(_scenario())


def test_collect_candidates_treats_fetch_error_as_missing_page(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", lambda **_kwargs: None
        )
        fetch = MagicMock()
        fetch.scrape_basic = AsyncMock(side_effect=CesarCoreFetchError("fetch_failed"))
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=fetch,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()
        fetch.scrape_basic.assert_awaited_once()

    asyncio.run(_scenario())


def test_collect_candidates_accepts_match_found_after_fetch_enrichment(monkeypatch):
    async def _scenario():
        first_call = {"n": 0}

        def fake_parse(**kwargs):
            first_call["n"] += 1
            if first_call["n"] == 1:
                return None
            return _candidate(identity_text=kwargs["title"] + " " + kwargs["content"])

        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", fake_parse
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match",
            lambda _product, _text: _match_result(kind="exact"),
        )
        fetch = MagicMock()
        fetch.scrape_basic = AsyncMock(
            return_value=CesarCoreFetchResult(
                url="https://a.com/1",
                title="Título Fetch",
                markdown="Conteúdo enriquecido",
            )
        )
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))
        ai = MagicMock()
        ai.generate = AsyncMock()

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=fetch,
            ai=ai,
            profile=UserRole.USER,
            now=NOW,
        )

        assert len(result) == 1
        assert result[0].match_kind == "exact"
        ai.generate.assert_not_awaited()

    asyncio.run(_scenario())


def test_collect_candidates_skips_ambiguous_call_when_second_parse_fails(monkeypatch):
    async def _scenario():
        first_call = {"n": 0}

        def fake_parse(**_kwargs):
            first_call["n"] += 1
            return None

        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", fake_parse
        )
        fetch = MagicMock()
        fetch.scrape_basic = AsyncMock(
            return_value=CesarCoreFetchResult(
                url="https://a.com/1", title=None, markdown=None
            )
        )
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))
        ai = MagicMock()
        ai.generate = AsyncMock()

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=fetch,
            ai=ai,
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()
        ai.generate.assert_not_awaited()

    asyncio.run(_scenario())


def test_collect_candidates_falls_back_to_interpret_ambiguous(monkeypatch):
    async def _scenario():
        calls = {"n": 0}

        def fake_parse(**_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return _candidate()

        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", fake_parse
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match", lambda *_a, **_k: None
        )
        ambiguous_candidate = _candidate(identity_text="ambiguous")
        interpret = AsyncMock(return_value=ambiguous_candidate)
        monkeypatch.setattr(
            "app.historical_bootstrap.service._interpret_ambiguous", interpret
        )
        fetch = MagicMock()
        fetch.scrape_basic = AsyncMock(
            return_value=CesarCoreFetchResult(
                url="https://a.com/1", title="Título Fetch", markdown="Conteúdo"
            )
        )
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=fetch,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == (ambiguous_candidate,)
        interpret.assert_awaited_once()

    asyncio.run(_scenario())


def test_collect_candidates_discards_when_interpret_ambiguous_returns_none(monkeypatch):
    async def _scenario():
        calls = {"n": 0}

        def fake_parse(**_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return _candidate()

        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", fake_parse
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._match", lambda *_a, **_k: None
        )
        interpret = AsyncMock(return_value=None)
        monkeypatch.setattr(
            "app.historical_bootstrap.service._interpret_ambiguous", interpret
        )
        fetch = MagicMock()
        fetch.scrape_basic = AsyncMock(
            return_value=CesarCoreFetchResult(
                url="https://a.com/1", title="Título Fetch", markdown="Conteúdo"
            )
        )
        search = _search_manager(_search_response([_search_result("https://a.com/1")]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=fetch,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()

    asyncio.run(_scenario())


def test_collect_candidates_respects_fetch_budget(monkeypatch):
    async def _scenario():
        monkeypatch.setattr(
            "app.historical_bootstrap.service._parse_candidate", lambda **_kwargs: None
        )
        fetch = MagicMock()
        fetch.scrape_basic = AsyncMock(
            return_value=CesarCoreFetchResult(url="x", title=None, markdown=None)
        )
        urls = [f"https://a.com/{i}" for i in range(4)]
        search = _search_manager(_search_response([_search_result(u) for u in urls]))

        result = await _collect_candidates(
            _product(category="notebook"),
            search=search,
            fetch=fetch,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
        )

        assert result == ()
        assert fetch.scrape_basic.await_count == 3

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# _mark_bootstrap_failed
# ---------------------------------------------------------------------------


def test_mark_bootstrap_failed_increments_existing_failure_count():
    async def _scenario():
        session = _mock_async_session()
        row = MagicMock(failure_count=2)
        session.get.return_value = row
        session_factory = _session_factory(session)
        bootstrap_id = uuid4()

        await _mark_bootstrap_failed(
            session_factory,
            bootstrap_id=bootstrap_id,
            now=NOW,
            error=RuntimeError("boom"),
            failure_backoff_minutes=10.0,
            failure_backoff_max_minutes=1000.0,
        )

        session.get.assert_awaited_once_with(
            HistoricalBootstrap, bootstrap_id, with_for_update=True
        )
        session.execute.assert_awaited_once()
        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["status"] == HistoricalBootstrapStatus.FAILED
        assert params["failure_count"] == 3
        assert params["retry_after"] == NOW + timedelta(minutes=10.0 * (2**2))
        assert params["lease_until"] is None
        assert "boom" in params["last_error"]

    asyncio.run(_scenario())


def test_mark_bootstrap_failed_defaults_to_one_when_row_missing():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = None
        session_factory = _session_factory(session)

        await _mark_bootstrap_failed(
            session_factory,
            bootstrap_id=uuid4(),
            now=NOW,
            error=RuntimeError("boom"),
            failure_backoff_minutes=10.0,
            failure_backoff_max_minutes=1000.0,
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["failure_count"] == 1
        assert params["retry_after"] == NOW + timedelta(minutes=10.0)

    asyncio.run(_scenario())


def test_mark_bootstrap_failed_caps_backoff_at_max():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = MagicMock(failure_count=20)
        session_factory = _session_factory(session)

        await _mark_bootstrap_failed(
            session_factory,
            bootstrap_id=uuid4(),
            now=NOW,
            error=RuntimeError("boom"),
            failure_backoff_minutes=10.0,
            failure_backoff_max_minutes=60.0,
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["retry_after"] == NOW + timedelta(minutes=60.0)

    asyncio.run(_scenario())


def test_mark_bootstrap_failed_truncates_long_error_message():
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = MagicMock(failure_count=0)
        session_factory = _session_factory(session)

        await _mark_bootstrap_failed(
            session_factory,
            bootstrap_id=uuid4(),
            now=NOW,
            error=RuntimeError("x" * 3000),
            failure_backoff_minutes=10.0,
            failure_backoff_max_minutes=1000.0,
        )

        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert len(params["last_error"]) <= 2000

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# _run_historical_bootstrap
# ---------------------------------------------------------------------------


def _run_kwargs(**overrides):
    base = {
        "product_id": uuid4(),
        "search": MagicMock(return_value=MagicMock()),
        "fetch": None,
        "ai": MagicMock(),
        "profile": UserRole.USER,
        "now": NOW,
        "revalidation_days": 90,
        "lease_seconds": 300.0,
        "failure_backoff_minutes": 10.0,
        "failure_backoff_max_minutes": 1000.0,
    }
    base.update(overrides)
    return base


def test_run_historical_bootstrap_returns_none_when_product_missing(monkeypatch):
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = None
        session_factory = _session_factory(session)
        search_factory = MagicMock(return_value=MagicMock())
        collect = AsyncMock()
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates", collect
        )

        result = await _run_historical_bootstrap(
            session_factory, **_run_kwargs(search=search_factory)
        )

        assert result is None
        session.scalar.assert_not_awaited()
        search_factory.assert_not_called()
        collect.assert_not_awaited()

    asyncio.run(_scenario())


def test_run_historical_bootstrap_returns_none_when_identity_key_missing(monkeypatch):
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = _product(identity_key=None)
        session_factory = _session_factory(session)

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result is None
        session.scalar.assert_not_awaited()

    asyncio.run(_scenario())


def test_run_historical_bootstrap_returns_none_when_internal_history_sufficient(
    monkeypatch,
):
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = _product()
        session_factory = _session_factory(session)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=True),
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result is None
        session.scalar.assert_not_awaited()

    asyncio.run(_scenario())


def test_run_historical_bootstrap_returns_existing_status_on_claim_conflict(
    monkeypatch,
):
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = _product()
        session.scalar.side_effect = [
            None,
            HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES,
        ]
        session_factory = _session_factory(session)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        search_factory = MagicMock(return_value=MagicMock())

        result = await _run_historical_bootstrap(
            session_factory, **_run_kwargs(search=search_factory)
        )

        assert result == HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
        search_factory.assert_not_called()

    asyncio.run(_scenario())


def test_run_historical_bootstrap_returns_none_on_claim_conflict_without_existing_row(
    monkeypatch,
):
    async def _scenario():
        session = _mock_async_session()
        session.get.return_value = _product()
        session.scalar.side_effect = [None, None]
        session_factory = _session_factory(session)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result is None

    asyncio.run(_scenario())


def test_run_historical_bootstrap_marks_failed_when_collect_raises(monkeypatch):
    async def _scenario():
        product = _product()
        session_a = _mock_async_session()
        session_a.get.return_value = product
        bootstrap_id = uuid4()
        session_a.scalar.side_effect = [bootstrap_id, None]
        session_factory = _session_factory(session_a)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        error = RuntimeError("falha de rede")
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates",
            AsyncMock(side_effect=error),
        )
        mark_failed = AsyncMock()
        monkeypatch.setattr(
            "app.historical_bootstrap.service._mark_bootstrap_failed", mark_failed
        )
        search_factory = MagicMock(return_value=MagicMock())

        result = await _run_historical_bootstrap(
            session_factory, **_run_kwargs(search=search_factory)
        )

        assert result is None
        search_factory.assert_called_once()
        mark_failed.assert_awaited_once()
        assert mark_failed.call_args.kwargs["bootstrap_id"] == bootstrap_id
        assert mark_failed.call_args.kwargs["error"] is error

    asyncio.run(_scenario())


def test_run_historical_bootstrap_persists_exact_candidate_and_completes(monkeypatch):
    async def _scenario():
        product = _product()
        bootstrap_id = uuid4()
        session_a = _mock_async_session()
        session_a.get.return_value = product
        session_a.scalar.side_effect = [bootstrap_id, None]
        session_b = _mock_async_session()
        row = MagicMock(spec=HistoricalBootstrap)
        session_b.get.return_value = row
        session_factory = _session_factory(session_a, session_b)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        candidate = _candidate(match_kind="exact")
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates",
            AsyncMock(return_value=(candidate,)),
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result == HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
        session_b.execute.assert_awaited_once()
        insert_stmt = session_b.execute.call_args.args[0]
        params = insert_stmt.compile(dialect=postgresql.dialect()).params
        assert params["match_evidence"] == {"method": "identity_key_exact"}
        assert row.status == HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
        assert row.completed_at == NOW
        assert row.lease_until is None
        assert row.retry_after is None
        assert row.failure_count == 0
        assert row.last_error is None

    asyncio.run(_scenario())


def test_run_historical_bootstrap_persists_family_candidate_with_evidence_attributes(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        bootstrap_id = uuid4()
        session_a = _mock_async_session()
        session_a.get.return_value = product
        session_a.scalar.side_effect = [bootstrap_id, None]
        session_b = _mock_async_session()
        session_b.get.return_value = MagicMock(spec=HistoricalBootstrap)
        session_factory = _session_factory(session_a, session_b)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        candidate = _candidate(
            match_kind="family", evidence_attributes=(("board_brand", "Gigabyte"),)
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates",
            AsyncMock(return_value=(candidate,)),
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result == HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
        insert_stmt = session_b.execute.call_args.args[0]
        params = insert_stmt.compile(dialect=postgresql.dialect()).params
        assert params["match_evidence"] == {
            "method": "family_key_market",
            "evidence_attributes": {"board_brand": "Gigabyte"},
        }

    asyncio.run(_scenario())


def test_run_historical_bootstrap_completes_without_references_when_nothing_found(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        bootstrap_id = uuid4()
        session_a = _mock_async_session()
        session_a.get.return_value = product
        session_a.scalar.side_effect = [bootstrap_id, None]
        session_b = _mock_async_session()
        session_b.get.return_value = MagicMock(spec=HistoricalBootstrap)
        session_factory = _session_factory(session_a, session_b)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates",
            AsyncMock(return_value=()),
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result == HistoricalBootstrapStatus.COMPLETED_WITHOUT_REFERENCES
        session_b.execute.assert_not_awaited()

    asyncio.run(_scenario())


def test_run_historical_bootstrap_completes_with_references_on_revalidation_without_new_evidence(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        bootstrap_id = uuid4()
        session_a = _mock_async_session()
        session_a.get.return_value = product
        session_a.scalar.side_effect = [bootstrap_id, uuid4()]
        session_b = _mock_async_session()
        session_b.get.return_value = MagicMock(spec=HistoricalBootstrap)
        session_factory = _session_factory(session_a, session_b)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates",
            AsyncMock(return_value=()),
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result == HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES

    asyncio.run(_scenario())


def test_run_historical_bootstrap_skips_row_update_when_row_missing_after_persist(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        bootstrap_id = uuid4()
        session_a = _mock_async_session()
        session_a.get.return_value = product
        session_a.scalar.side_effect = [bootstrap_id, None]
        session_b = _mock_async_session()
        session_b.get.return_value = None
        session_factory = _session_factory(session_a, session_b)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates",
            AsyncMock(return_value=()),
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result == HistoricalBootstrapStatus.COMPLETED_WITHOUT_REFERENCES

    asyncio.run(_scenario())


def test_run_historical_bootstrap_marks_failed_when_persist_transaction_raises(
    monkeypatch,
):
    async def _scenario():
        product = _product()
        bootstrap_id = uuid4()
        session_a = _mock_async_session()
        session_a.get.return_value = product
        session_a.scalar.side_effect = [bootstrap_id, None]
        session_b = _mock_async_session()
        error = RuntimeError("db indisponível")
        session_b.execute.side_effect = error
        session_factory = _session_factory(session_a, session_b)
        monkeypatch.setattr(
            "app.historical_bootstrap.service.internal_history_is_sufficient",
            AsyncMock(return_value=False),
        )
        candidate = _candidate(match_kind="exact")
        monkeypatch.setattr(
            "app.historical_bootstrap.service._collect_candidates",
            AsyncMock(return_value=(candidate,)),
        )
        mark_failed = AsyncMock()
        monkeypatch.setattr(
            "app.historical_bootstrap.service._mark_bootstrap_failed", mark_failed
        )

        result = await _run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result is None
        mark_failed.assert_awaited_once()
        assert mark_failed.call_args.kwargs["bootstrap_id"] == bootstrap_id
        assert mark_failed.call_args.kwargs["error"] is error

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# run_historical_bootstrap
# ---------------------------------------------------------------------------


def test_run_historical_bootstrap_public_forwards_result(monkeypatch):
    async def _scenario():
        inner = AsyncMock(
            return_value=HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
        )
        monkeypatch.setattr(
            "app.historical_bootstrap.service._run_historical_bootstrap", inner
        )
        session_factory = MagicMock()

        result = await run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result == HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
        inner.assert_awaited_once()

    asyncio.run(_scenario())


def test_run_historical_bootstrap_public_swallows_unexpected_exception(
    monkeypatch, caplog
):
    async def _scenario():
        inner = AsyncMock(side_effect=RuntimeError("inesperado"))
        monkeypatch.setattr(
            "app.historical_bootstrap.service._run_historical_bootstrap", inner
        )
        session_factory = MagicMock()

        with caplog.at_level("WARNING"):
            result = await run_historical_bootstrap(session_factory, **_run_kwargs())

        assert result is None
        assert "historical_bootstrap_unexpected_failure" in caplog.text

    asyncio.run(_scenario())
