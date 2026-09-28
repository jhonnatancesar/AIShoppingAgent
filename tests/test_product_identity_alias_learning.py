"""TASK-129 -- a tabela de grafias aprende sozinha, só com prova (unitário,
sessão falsa; o comportamento contra Postgres real está em
`tests/integration/test_identity_alias_learning.py`)."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.products.identity import build_resolved_variant_from_fields
from app.products.identity_ai import (
    AIIdentityExtraction,
    build_partial_link,
    evaluate_ai_identity_extraction,
)
from app.products.identity_alias_learning import (
    find_same_part_number_candidate,
    record_part_number_aliases,
    suggest_brand_aliases,
)
from app.products.identity_vocabulary import IdentityVocabulary
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

_FURY = IdentityVocabulary(aliases=(("ram", "brand", "fury", "kingston"),))


class _Rows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, *, scalars=(), rows=(), fail: bool = False) -> None:
        self._scalars = scalars
        self._rows = rows
        self._fail = fail
        self.statements: list = []

    async def scalars(self, _query):
        return _Rows(self._scalars)

    async def execute(self, statement):
        if getattr(statement, "is_insert", False):
            if self._fail:
                raise SQLAlchemyError("boom")
            self.statements.append(statement)
            return None
        return _Rows(self._rows)

    @asynccontextmanager
    async def begin_nested(self):
        yield


def _params(statement) -> dict:
    return statement.compile(dialect=postgresql.dialect()).params


def _resolved(brand: str, family: str, model: str = "PVV532G600C36K"):
    return build_resolved_variant_from_fields(
        category="ram",
        brand=brand,
        family=family,
        model=model,
        variant=None,
        attributes={},
    )


def test_same_part_number_is_found_ignoring_punctuation() -> None:
    other = SimpleNamespace(manufacturer_part_number="PVV5-32G600C36K")
    unrelated = SimpleNamespace(manufacturer_part_number="XYZ")
    session = _Session(scalars=[unrelated, other])
    found = asyncio.run(
        find_same_part_number_candidate(
            session, _resolved("viper", "venom"), "PVV532G600C36K"
        )
    )
    assert found is other
    assert (
        asyncio.run(
            find_same_part_number_candidate(session, _resolved("viper", "venom"), " ")
        )
        is None
    )
    assert (
        asyncio.run(
            find_same_part_number_candidate(
                _Session(scalars=[unrelated]), _resolved("viper", "venom"), "ABC"
            )
        )
        is None
    )


def test_part_number_proof_records_active_brand_and_family_aliases() -> None:
    session = _Session()
    asyncio.run(
        record_part_number_aliases(
            session,
            resolved=_resolved("viper", "venom"),
            approved_brand="patriot",
            approved_family="viper-venom",
        )
    )
    recorded = [_params(statement) for statement in session.statements]
    assert [
        (p["attribute_name"], p["raw_value_normalized"], p["canonical_value"])
        for p in recorded
    ] == [("brand", "viper", "patriot"), ("family", "venom", "viper-venom")]
    assert {p["status"] for p in recorded} == {"active"}


def test_part_number_proof_skips_spelling_that_already_matches() -> None:
    session = _Session()
    asyncio.run(
        record_part_number_aliases(
            session,
            resolved=_resolved("patriot", "venom"),
            approved_brand="patriot",
            approved_family="viper-venom",
        )
    )
    assert [_params(s)["attribute_name"] for s in session.statements] == ["family"]


def test_line_written_as_brand_becomes_a_suggestion_both_ways() -> None:
    # a marca nova é a linha de outra marca já aprovada
    session = _Session(rows=[("patriot", "viper-venom"), ("corsair", "vengeance")])
    asyncio.run(suggest_brand_aliases(session, _resolved("viper", "steel")))
    assert [
        (_params(s)["raw_value_normalized"], _params(s)["canonical_value"])
        for s in session.statements
    ] == [("viper", "patriot")]
    assert _params(session.statements[0])["status"] == "candidate"
    # a marca antiga era a linha; agora veio o fabricante
    reverse = _Session(rows=[("viper", "venom")])
    asyncio.run(suggest_brand_aliases(reverse, _resolved("patriot", "viper-steel")))
    assert [
        (_params(s)["raw_value_normalized"], _params(s)["canonical_value"])
        for s in reverse.statements
    ] == [("viper", "patriot")]
    # nada parecido: nenhuma sugestão
    none = _Session(rows=[("corsair", "vengeance"), ("adata", None)])
    asyncio.run(suggest_brand_aliases(none, _resolved("viper", "steel")))
    assert none.statements == []


def test_alias_learning_failure_never_breaks_the_resolution() -> None:
    session = _Session(rows=[("patriot", "viper-venom")], fail=True)
    asyncio.run(suggest_brand_aliases(session, _resolved("viper", "steel")))
    asyncio.run(
        record_part_number_aliases(
            session,
            resolved=_resolved("viper", "venom"),
            approved_brand="patriot",
            approved_family="viper-venom",
        )
    )
    assert session.statements == []


# --- grounding pelo alias ------------------------------------------------


def _extraction(**fields) -> AIIdentityExtraction:
    base = {
        "category": "ram",
        "brand": "Kingston",
        "family": "Fury Beast",
        "model": "KF432C16BB/8",
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": None,
        "attributes": {},
        "ai_provider": "stub",
        "ai_model": "m",
    }
    base.update(fields)
    return AIIdentityExtraction(**base)


def test_manufacturer_is_grounded_through_the_line_written_in_the_title() -> None:
    """A IA seguiu o prompt ("brand = fabricante") num título que só traz a
    linha: aprovado porque o alias ativo fury -> kingston está no título.
    Sem o alias, reprova normalmente -- nunca inventa."""
    title = "Memória Fury Beast 8GB DDR4 3200MHz KF432C16BB/8"
    with_alias = evaluate_ai_identity_extraction(title, _extraction(), vocabulary=_FURY)
    assert with_alias.status == "approved"
    assert with_alias.resolved.brand == "kingston"
    assert evaluate_ai_identity_extraction(title, _extraction()).status == (
        "pending_review"
    )
    other_title = "Memória Beast 8GB DDR4 KF432C16BB/8"
    assert (
        evaluate_ai_identity_extraction(
            other_title, _extraction(), vocabulary=_FURY
        ).status
        == "pending_review"
    ), "a linha também precisa estar no título"
    link = build_partial_link(
        title, category="ram", brand="Kingston", family=None, vocabulary=_FURY
    )
    assert link.brand == "kingston"
    assert (
        build_partial_link(title, category="ram", brand="Kingston", family=None).brand
        is None
    )
