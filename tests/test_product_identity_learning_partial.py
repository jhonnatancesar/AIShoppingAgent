"""TASK-128 (etapa 1) -- vínculo parcial em `app.products.identity_learning`,
testes puros/sem banco: critério do backlog, vínculo a partir do cache,
aplicação no Product, despacho por tipo de resultado da IA e o registro
no cache (inclusive corrida). O comportamento contra Postgres real fica
em `tests/integration/test_product_identity_partial_link.py`."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.products import identity_learning
from app.products.identity_ai import (
    AIIdentityExtraction,
    AIPartialExtraction,
    AIUnrecognizedTitle,
    PartialProductLink,
)
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import (
    _apply_resolution,
    _link_from_candidate,
    _PreparedResolution,
    _record_uncertain_extraction,
    _resolve_from_extraction,
    apply_partial_link,
    reprocess_unresolved_products,
    resolve_or_learn_product_variant,
    unlinked_product_criteria,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError


@asynccontextmanager
async def _noop_cm():
    yield


def _session() -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.scalars = AsyncMock()
    session.begin_nested = MagicMock(side_effect=lambda: _noop_cm())
    return session


def _candidate(status: str, **fields) -> ProductIdentityCandidate:
    return ProductIdentityCandidate(
        raw_title=fields.pop("raw_title", "Processador AMD Ryzen 7 Box"),
        normalized_title_hash="h",
        status=status,
        **fields,
    )


def _product(**fields) -> SimpleNamespace:
    base = {"identity_key": None, "category": None, "brand": None, "family": None}
    base.update(fields)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# critério do backlog
# ---------------------------------------------------------------------------


def test_unlinked_criteria_requires_no_exact_identity_and_no_category() -> None:
    sql = str(unlinked_product_criteria().compile(dialect=postgresql.dialect()))
    assert "products.identity_key IS NULL" in sql
    assert "products.category IS NULL" in sql


# ---------------------------------------------------------------------------
# _link_from_candidate -- o que o cache garante de vínculo
# ---------------------------------------------------------------------------


def test_partial_candidate_returns_its_stored_link() -> None:
    link = _link_from_candidate(
        _candidate("partial", category="cpu", brand="AMD", family=None)
    )
    assert link == PartialProductLink(category="cpu", brand="AMD", family=None)


def test_review_candidate_links_only_what_the_title_grounds() -> None:
    """`pending_review`/`rejected` guardam uma proposta completa que não
    passou no grounding -- só a parte presente no título vira vínculo."""
    for status in ("pending_review", "rejected"):
        link = _link_from_candidate(
            _candidate(status, category="CPU", brand="AMD", family="Threadripper")
        )
        assert link == PartialProductLink(category="cpu", brand="amd", family=None)


def test_awaiting_page_and_approved_candidates_have_no_partial_link() -> None:
    assert _link_from_candidate(_candidate("awaiting_page")) is None
    assert _link_from_candidate(_candidate("approved", category="cpu")) is None


# ---------------------------------------------------------------------------
# apply_partial_link
# ---------------------------------------------------------------------------


def test_partial_link_fills_only_empty_fields() -> None:
    product = _product(brand="Marca Anterior")
    link = PartialProductLink(category="cpu", brand="AMD", family="Ryzen")

    assert apply_partial_link(product, link) is True
    assert (product.category, product.brand, product.family) == (
        "cpu",
        "Marca Anterior",
        "Ryzen",
    )
    assert product.identity_key is None


def test_partial_link_keeps_absent_brand_and_family_empty() -> None:
    product = _product()
    assert apply_partial_link(product, PartialProductLink("cadeira gamer", None, None))
    assert (product.brand, product.family) == (None, None)


def test_partial_link_never_touches_a_product_with_exact_identity() -> None:
    product = _product(identity_key="v1:x", category="gpu")
    assert apply_partial_link(product, PartialProductLink("cpu", "AMD", None)) is False
    assert product.category == "gpu"
    assert product.brand is None


# ---------------------------------------------------------------------------
# _apply_resolution -- ponto único do backfill
# ---------------------------------------------------------------------------


def test_apply_resolution_skips_missing_or_already_resolved_product() -> None:
    link = PartialProductLink("cpu", None, None)
    for found in (None, _product(identity_key="v1:x")):
        session = _session()
        session.get.return_value = found
        applied = asyncio.run(
            _apply_resolution(
                session,
                product_id=uuid4(),
                resolution=link,
                apply=True,
                outcome_sink={},
            )
        )
        assert applied is False
        session.commit.assert_not_awaited()


def test_apply_resolution_with_partial_link_reports_and_commits_only_on_apply(
    monkeypatch,
) -> None:
    exact = AsyncMock()
    monkeypatch.setattr(identity_learning, "apply_learned_identity", exact)
    link = PartialProductLink("cpu", "AMD", None)
    for apply in (True, False):
        session = _session()
        product = _product()
        session.get.return_value = product
        product_id = uuid4()
        sink: dict[object, str] = {}

        applied = asyncio.run(
            _apply_resolution(
                session,
                product_id=product_id,
                resolution=link,
                apply=apply,
                outcome_sink=sink,
            )
        )

        assert applied is True
        assert product.category == "cpu"
        assert sink[product_id] == (
            "VINCULO PARCIAL -> category=cpu brand=AMD family=-"
        )
        assert session.commit.await_count == (1 if apply else 0)
    exact.assert_not_awaited()


def test_apply_resolution_with_exact_identity_keeps_previous_behaviour(
    monkeypatch,
) -> None:
    resolved = SimpleNamespace(
        category="monitor",
        brand="lg",
        family="ultragear",
        model="27gp850",
        variant="base",
    )
    product_id = uuid4()

    blocked = _session()
    blocked.get.return_value = _product()
    monkeypatch.setattr(
        identity_learning, "apply_learned_identity", AsyncMock(return_value=None)
    )
    sink: dict[object, str] = {}
    assert (
        asyncio.run(
            _apply_resolution(
                blocked,
                product_id=product_id,
                resolution=resolved,
                apply=True,
                outcome_sink=sink,
            )
        )
        is False
    )
    assert sink[product_id].startswith("BLOQUEADO")
    blocked.commit.assert_not_awaited()

    promoted = _session()
    promoted.get.return_value = _product()
    monkeypatch.setattr(
        identity_learning,
        "apply_learned_identity",
        AsyncMock(return_value=SimpleNamespace(id=product_id)),
    )
    sink = {}
    assert (
        asyncio.run(
            _apply_resolution(
                promoted,
                product_id=product_id,
                resolution=resolved,
                apply=True,
                outcome_sink=sink,
            )
        )
        is True
    )
    assert sink[product_id].startswith("RESOLVIDO -> category=monitor")
    promoted.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# _resolve_from_extraction -- despacho pelo tipo do resultado da IA
# ---------------------------------------------------------------------------


def _prepared() -> _PreparedResolution:
    return _PreparedResolution(done=False, resolved=None, title_hash="hash")


def test_partial_extraction_is_grounded_before_going_to_the_cache(monkeypatch) -> None:
    record = AsyncMock(return_value="gravado")
    monkeypatch.setattr(identity_learning, "_record_uncertain_extraction", record)

    result = asyncio.run(
        _resolve_from_extraction(
            _session(),
            raw_title="Mouse Gamer RGB 7200 DPI",
            prepared=_prepared(),
            extraction=AIPartialExtraction(
                category="Mouse",
                brand="Logitech",
                family=None,
                ai_provider="stub",
                ai_model="m",
            ),
            ai_manager=MagicMock(),
            arbiter_ai_manager=None,
        )
    )

    assert result == "gravado"
    kwargs = record.await_args.kwargs
    assert kwargs["link"] == PartialProductLink("mouse", None, None)
    assert kwargs["title_hash"] == "hash"
    assert (kwargs["ai_provider"], kwargs["ai_model"]) == ("stub", "m")


def test_unrecognized_title_goes_to_the_cache_without_a_link(monkeypatch) -> None:
    record = AsyncMock(return_value=None)
    monkeypatch.setattr(identity_learning, "_record_uncertain_extraction", record)

    result = asyncio.run(
        _resolve_from_extraction(
            _session(),
            raw_title="Kit Promo 3 em 1",
            prepared=_prepared(),
            extraction=AIUnrecognizedTitle(ai_provider="stub", ai_model="m"),
            ai_manager=MagicMock(),
            arbiter_ai_manager=None,
        )
    )

    assert result is None
    assert record.await_args.kwargs["link"] is None


def test_exact_extraction_keeps_the_grounding_and_arbiter_path(monkeypatch) -> None:
    finish = AsyncMock(return_value="exata")
    monkeypatch.setattr(identity_learning, "_finish_resolution_with_extraction", finish)
    extraction = AIIdentityExtraction(
        category="monitor",
        brand="LG",
        family="UltraGear",
        model="27GP850",
        variant=None,
        store_sku=None,
        manufacturer_part_number=None,
        attributes={},
        ai_provider="stub",
        ai_model="m",
    )

    result = asyncio.run(
        _resolve_from_extraction(
            _session(),
            raw_title="Monitor LG UltraGear 27GP850",
            prepared=_prepared(),
            extraction=extraction,
            ai_manager=MagicMock(),
            arbiter_ai_manager=None,
        )
    )

    assert result == "exata"
    assert finish.await_args.kwargs["extraction"] is extraction


# ---------------------------------------------------------------------------
# _record_uncertain_extraction -- cache, inclusive corrida
# ---------------------------------------------------------------------------


def _record(session, link):
    return asyncio.run(
        _record_uncertain_extraction(
            session,
            raw_title="Cadeira Gamer",
            title_hash="hash",
            link=link,
            ai_provider="stub",
            ai_model="m",
        )
    )


def test_record_writes_partial_or_awaiting_page_row() -> None:
    link = PartialProductLink("cadeira gamer", None, None)
    for given, status in ((link, "partial"), (None, "awaiting_page")):
        session = _session()
        assert _record(session, given) == given
        [row] = [call.args[0] for call in session.add.call_args_list]
        assert row.status == status
        assert row.category == (given.category if given else None)
        assert row.identity_key is None


def test_record_race_rereads_the_winner(monkeypatch) -> None:
    """Outra coleta gravou o MESMO título entre a consulta e o INSERT --
    nunca duplica nem propaga o erro, devolve o que venceu."""
    approved = _candidate(
        "approved",
        category="monitor",
        brand="lg",
        family="ultragear",
        model="27gp850",
        variant="base",
        family_key="lg-ultragear-27gp850",  # gitleaks:allow -- slug de teste, não é segredo
        identity_key="lg-ultragear-27gp850-base",
        attributes={},
    )
    partial = _candidate("partial", category="cpu", brand=None, family=None)
    for winner, expected in (
        (None, None),
        (partial, PartialProductLink("cpu", None, None)),
    ):
        session = _session()
        session.flush.side_effect = IntegrityError("insert", {}, Exception("dup"))
        monkeypatch.setattr(
            identity_learning, "_find_candidate", AsyncMock(return_value=winner)
        )
        assert _record(session, PartialProductLink("x", None, None)) == expected

    session = _session()
    session.flush.side_effect = IntegrityError("insert", {}, Exception("dup"))
    monkeypatch.setattr(
        identity_learning, "_find_candidate", AsyncMock(return_value=approved)
    )
    resolved = _record(session, None)
    assert resolved.identity_key == "lg-ultragear-27gp850-base"


# ---------------------------------------------------------------------------
# resolve_or_learn_product_variant / backfill item a item
# ---------------------------------------------------------------------------


def test_transient_ai_failure_is_never_cached(monkeypatch) -> None:
    monkeypatch.setattr(
        identity_learning,
        "_prepare_resolution",
        AsyncMock(return_value=_prepared()),
    )
    monkeypatch.setattr(
        identity_learning,
        "extract_product_identity_via_ai",
        AsyncMock(return_value=None),
    )
    resolve = AsyncMock()
    monkeypatch.setattr(identity_learning, "_resolve_from_extraction", resolve)
    session = _session()

    result = asyncio.run(
        resolve_or_learn_product_variant(
            session, raw_title="Cadeira Gamer", ai_manager=MagicMock()
        )
    )

    assert result is None
    session.rollback.assert_awaited_once()
    resolve.assert_not_awaited()


def test_one_by_one_backfill_commits_awaiting_page_before_the_next_title(
    monkeypatch,
) -> None:
    """Sem vínculo (`awaiting_page`) + `apply=True`: commita na hora, senão
    o rollback antes da IA do PRÓXIMO título apagaria o cache."""
    products = [SimpleNamespace(id=uuid4(), name=f"título {n}") for n in range(2)]
    session = _session()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=products))
    link = PartialProductLink("cadeira gamer", None, None)
    monkeypatch.setattr(
        identity_learning,
        "resolve_or_learn_product_variant",
        AsyncMock(side_effect=[None, link]),
    )
    apply_resolution = AsyncMock(return_value=True)
    monkeypatch.setattr(identity_learning, "_apply_resolution", apply_resolution)

    count = asyncio.run(
        reprocess_unresolved_products(session, ai_manager=MagicMock(), apply=True)
    )

    assert count == 1
    session.commit.assert_awaited_once()
    assert apply_resolution.await_args.kwargs["resolution"] is link
