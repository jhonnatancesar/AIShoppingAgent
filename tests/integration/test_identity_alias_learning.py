"""TASK-129 -- tabela de grafias contra Postgres real: lista inicial da
migration, aprendizado com prova (mesmo part number), sugestão para
revisão, recusa que nunca volta e o script de revisão."""

import asyncio
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.products.identity import build_resolved_variant_from_fields
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import resolve_or_learn_product_variant
from app.products.models import ProductIdentityAlias
from app.users.models import UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "review_identity_aliases.py"
)


def _extraction(**fields) -> str:
    base = {
        "category": "ram",
        "brand": "",
        "family": "",
        "model": "",
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": None,
        "attributes": {},
    }
    base.update(fields)
    return json.dumps(base)


class _AI:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


def _approved(
    integration_database,
    *,
    brand: str,
    family: str,
    model: str,
    part_number: str | None = None,
) -> str:
    resolved = build_resolved_variant_from_fields(
        category="ram",
        brand=brand,
        family=family,
        model=model,
        variant=None,
        attributes={},
    )
    with integration_database.sessions.begin() as session:
        session.add(
            ProductIdentityCandidate(
                id=uuid4(),
                raw_title=f"Memória {brand} {family} {model}",
                normalized_title_hash=uuid4().hex,
                category=resolved.category,
                brand=resolved.brand,
                family=resolved.family,
                model=resolved.model,
                variant=resolved.variant,
                attributes={},
                manufacturer_part_number=part_number,
                family_key=resolved.family_key,
                identity_key=resolved.identity_key,
                status="approved",
                grounded=True,
            )
        )
    return resolved.identity_key


def _resolve(integration_database, title: str, ai: _AI):
    async def _run():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=ai, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    return asyncio.run(_run())


def _aliases(integration_database, raw: str) -> list[tuple[str, str, str, str]]:
    with integration_database.sessions() as session:
        rows = session.scalars(
            select(ProductIdentityAlias).where(
                ProductIdentityAlias.raw_value_normalized == raw
            )
        ).all()
        return sorted(
            (row.category, row.attribute_name, row.canonical_value, row.status)
            for row in rows
        )


def test_migration_seeds_the_initial_spellings(integration_database) -> None:
    with integration_database.sessions() as session:
        seeded = {
            (row.category, row.raw_value_normalized, row.canonical_value)
            for row in session.scalars(
                select(ProductIdentityAlias).where(
                    ProductIdentityAlias.status == "active"
                )
            )
        }
    assert {
        ("ram", "fury", "kingston"),
        ("*", "rog", "asus"),
        ("*", "aorus", "gigabyte"),
        ("*", "wd", "western-digital"),
    } <= seeded


def test_same_part_number_learns_the_spelling_and_keeps_one_product(
    integration_database,
) -> None:
    """Um anúncio escreve "Viper Venom" sem a Patriot; o outro, já aprovado,
    é patriot/viper-venom -- mesmo part number: é o mesmo produto."""
    existing_key = _approved(
        integration_database,
        brand="Patriot",
        family="Viper Venom",
        model="PVV532G600C36K",
        part_number="PVV532G600C36K",
    )
    title = "Memória Viper Venom 32GB DDR5 6000MHz PVV5-32G600C36K"
    ai = _AI(
        _extraction(
            brand="Viper",
            family="Venom",
            model="PVV5-32G600C36K",
            manufacturer_part_number="PVV5-32G600C36K",
        )
    )

    resolved = _resolve(integration_database, title, ai)

    assert resolved.identity_key == existing_key
    assert _aliases(integration_database, "viper") == [
        ("ram", "brand", "patriot", "active")
    ]
    assert _aliases(integration_database, "venom") == [
        ("ram", "family", "viper-venom", "active")
    ]
    with integration_database.sessions() as session:
        stored = session.scalar(
            select(ProductIdentityCandidate).where(
                ProductIdentityCandidate.raw_title == title
            )
        )
    assert (stored.identity_key, stored.reviewer_note) == (
        existing_key,
        "same_part_number",
    )


def test_line_written_as_brand_is_only_a_suggestion(integration_database) -> None:
    _approved(
        integration_database,
        brand="Patriot",
        family="Viper Venom",
        model="PVV532G600C36K",
    )
    ai = _AI(_extraction(brand="Viper", family="Steel", model="PVS416G320C6"))

    resolved = _resolve(
        integration_database, "Memória Viper Steel 16GB DDR4 PVS416G320C6", ai
    )

    assert resolved.brand == "viper", "sugestão não muda nada até ser aprovada"
    assert _aliases(integration_database, "viper") == [
        ("ram", "brand", "patriot", "candidate")
    ]


def test_rejected_suggestion_never_comes_back(integration_database) -> None:
    _approved(
        integration_database,
        brand="Patriot",
        family="Viper Venom",
        model="PVV532G600C36K",
    )
    with integration_database.sessions.begin() as session:
        session.add(
            ProductIdentityAlias(
                category="ram",
                attribute_name="brand",
                raw_value_normalized="viper",
                canonical_value="patriot",
                status="rejected",
            )
        )
    ai = _AI(_extraction(brand="Viper", family="Steel", model="PVS416G320C6"))

    _resolve(integration_database, "Memória Viper Steel 16GB DDR4 PVS416G320C6", ai)

    assert _aliases(integration_database, "viper") == [
        ("ram", "brand", "patriot", "rejected")
    ]


def test_manufacturer_is_accepted_through_the_seeded_line(
    integration_database,
) -> None:
    """A IA seguiu o prompt e escreveu o fabricante num título que só traz
    a linha -- aprovado pela grafia "fury" da lista inicial."""
    ai = _AI(_extraction(brand="Kingston", family="Fury Beast", model="KF432C16BB/8"))

    resolved = _resolve(
        integration_database, "Memória Fury Beast 8GB DDR4 3200MHz KF432C16BB/8", ai
    )

    assert (resolved.category, resolved.brand, resolved.family) == (
        "ram",
        "kingston",
        "fury-beast",
    )


def _load_script_module():
    spec = importlib.util.spec_from_file_location("review_identity_aliases", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_script_lists_approves_and_rejects(integration_database, capsys) -> None:
    ids = {}
    with integration_database.sessions.begin() as session:
        for raw in ("viper", "venom"):
            alias = ProductIdentityAlias(
                id=uuid4(),
                category="ram",
                attribute_name="brand",
                raw_value_normalized=raw,
                canonical_value="patriot",
                status="candidate",
            )
            session.add(alias)
            ids[raw] = str(alias.id)
    module = _load_script_module()

    assert asyncio.run(module.run())
    listed = capsys.readouterr().out
    assert "viper -> patriot" in listed and "fury" not in listed

    assert asyncio.run(module.run(approve=ids["viper"][:8]))
    assert asyncio.run(module.run(reject=ids["venom"]))
    assert _aliases(integration_database, "viper") == [
        ("ram", "brand", "patriot", "active")
    ]
    assert _aliases(integration_database, "venom") == [
        ("ram", "brand", "patriot", "rejected")
    ]
    capsys.readouterr()

    assert not asyncio.run(module.run(approve=ids["viper"])), "já revisada"
    assert not asyncio.run(module.run(approve="zzzzzzzz")), "ID inexistente"
    assert not asyncio.run(module.run(approve=" ")), "ID vazio"
    assert asyncio.run(module.run())
    assert "Nenhuma sugestão pendente." in capsys.readouterr().out
    assert asyncio.run(module.run(list_all=True))
    assert "[rejected]" in capsys.readouterr().out
