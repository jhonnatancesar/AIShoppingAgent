"""TASK-132 (Parte B) -- catálogo de nomenclaturas contra Postgres real:
consulta antes da IA, aprendizado automático por part number, recusa que não
volta, pré-lista e script de preenchimento/revisão."""

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
from app.products.identity_catalog import upsert_seed_entry
from app.products.identity_catalog_models import (
    ProductIdentityCatalogCode,
    ProductIdentityCatalogEntry,
)
from app.products.identity_catalog_seed import SEED_ENTRIES, SeedEntry
from app.products.identity_learning import resolve_or_learn_product_variant
from app.users.models import UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

_SCRIPTS = Path(__file__).resolve().parents[2] / "backend" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _AI:
    def __init__(self, content: str = "{}") -> None:
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


def _resolve(integration_database, title: str, ai: _AI):
    async def _run():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=ai, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    return asyncio.run(_run())


def _seed(integration_database, *entries: SeedEntry) -> None:
    async def _run():
        async with integration_database.async_sessions() as session:
            for entry in entries:
                await upsert_seed_entry(session, entry)
            await session.commit()

    asyncio.run(_run())


def _count(integration_database, model) -> int:
    with integration_database.sessions() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_catalog_name_resolves_before_any_ai_call(integration_database) -> None:
    _seed(
        integration_database,
        SeedEntry(
            "smartphone",
            "xiaomi",
            "redmi-note",
            "13",
            ("REDMI NOTE 13 PRO+", "REDMI NOTE 13 PRO PLUS"),
            variant="pro-plus",
            required_attributes=("storage_gb",),
        ),
        SeedEntry(
            "smartphone",
            "xiaomi",
            "redmi-note",
            "13",
            ("REDMI NOTE 13",),
            required_attributes=("storage_gb",),
        ),
    )
    ai = _AI()

    plus = _resolve(
        integration_database, "Smartphone Xiaomi Redmi Note 13 Pro+ 5G 512GB", ai
    )
    base = _resolve(integration_database, "Xiaomi Redmi Note 13 5G 256GB", ai)

    assert ai.calls == 0, "o catálogo resolve antes da IA"
    assert (plus.family, plus.variant, dict(plus.attributes)) == (
        "redmi-note",
        "pro-plus",
        {"storage_gb": "512"},
    )
    assert (base.variant, dict(base.attributes)) == ("base", {"storage_gb": "256"})
    assert plus.identity_key != base.identity_key


def test_approved_identity_with_part_number_is_learned_and_reused_without_ai(
    integration_database,
) -> None:
    ai = _AI(
        _extraction(
            brand="Kingston",
            family="Fury Beast",
            model="KF560C36BBE-8",
            manufacturer_part_number="KF560C36BBE-8",
        )
    )
    first = _resolve(
        integration_database,
        "Memória Kingston Fury Beast 8GB DDR5 6000MHz KF560C36BBE-8",
        ai,
    )
    assert ai.calls == 1
    assert _count(integration_database, ProductIdentityCatalogEntry) == 1
    with integration_database.sessions() as session:
        code = session.scalar(select(ProductIdentityCatalogCode))
        entry = session.scalar(select(ProductIdentityCatalogEntry))
    assert (code.kind, code.value_normalized) == ("part_number", "KF560C36BBE8")
    assert (entry.source, entry.status, entry.identity_key) == (
        "learned",
        "active",
        first.identity_key,
    )

    # outra loja, outro texto, mesmo part number: catálogo, sem nova chamada de IA
    second = _resolve(
        integration_database,
        "RAM Fury Beast Preta 8GB Desktop (KF560C36BBE8) Envio Rápido",
        ai,
    )
    assert ai.calls == 1
    assert second.identity_key == first.identity_key


def test_rejected_entry_stops_resolving_and_is_never_relearned(
    integration_database,
) -> None:
    ai = _AI(
        _extraction(
            brand="Kingston",
            family="Fury Beast",
            model="KF560C36BBE-8",
            manufacturer_part_number="KF560C36BBE-8",
        )
    )
    _resolve(
        integration_database,
        "Memória Kingston Fury Beast 8GB DDR5 6000MHz KF560C36BBE-8",
        ai,
    )
    review = _load("review_identity_catalog")
    with integration_database.sessions() as session:
        entry_id = str(session.scalar(select(ProductIdentityCatalogEntry.id)))
    assert asyncio.run(review.run(reject=entry_id[:8]))

    # recusada: a consulta ignora e a IA volta a ser chamada. O título só traz o
    # part number (sem marca/família), para o reuso por palavras não responder.
    _resolve(
        integration_database,
        "Módulo de memória KF560C36BBE8 8GB DDR5 Preto",
        ai,
    )
    assert ai.calls == 2
    # e o mesmo código nunca é reaprendido (nenhum código/entrada novos)
    assert _count(integration_database, ProductIdentityCatalogCode) == 1
    assert _count(integration_database, ProductIdentityCatalogEntry) == 1

    assert asyncio.run(review.run(activate=entry_id[:8]))
    third = _resolve(
        integration_database, "Terceiro anúncio Fury Beast KF560C36BBE-8 8GB", ai
    )
    assert ai.calls == 2, "reativada, volta a resolver pelo catálogo"
    assert third.model == "kf560c36bbe-8"


def test_seed_script_loads_the_pre_list_and_the_approved_part_numbers(
    integration_database, capsys
) -> None:
    def _approved(model: str, part_number: str | None, brand: str = "corsair"):
        resolved = build_resolved_variant_from_fields(
            category="ram",
            brand=brand,
            family="vengeance",
            model=model,
            variant=None,
            attributes={},
        )
        return ProductIdentityCandidate(
            id=uuid4(),
            raw_title=f"Memória {brand} {model}",
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

    with integration_database.sessions.begin() as session:
        session.add_all(
            [
                _approved("cmk16gx5m1b5200c40", "CMK16GX5M1B5200C40"),
                _approved("cmk16gx5m1b5200z40", "CMK16GX5M1B5200Z40"),
                # o MESMO part number em duas identidades: conflito, não entra
                _approved("conflito-a", "CONFLICT123ABC"),
                _approved("conflito-b", "CONFLICT123ABC"),
                _approved("sem-codigo", None),
            ]
        )
    script = _load("seed_identity_catalog")

    asyncio.run(script.run(apply=False))
    assert "Dry-run" in capsys.readouterr().out
    assert _count(integration_database, ProductIdentityCatalogEntry) == 0

    asyncio.run(script.run(apply=True))
    out = capsys.readouterr().out
    assert "CONFLITO CONFLICT123ABC" in out
    entries = _count(integration_database, ProductIdentityCatalogEntry)
    assert entries == len(SEED_ENTRIES) + 2, "pré-lista + 2 part numbers sem conflito"

    asyncio.run(script.run(apply=True))
    assert _count(integration_database, ProductIdentityCatalogEntry) == entries, (
        "idempotente"
    )


def test_review_script_lists_and_validates_ids(integration_database, capsys) -> None:
    _seed(integration_database, SEED_ENTRIES[0])
    review = _load("review_identity_catalog")

    assert asyncio.run(review.run())
    assert "apple/iphone/16/e" in capsys.readouterr().out
    assert not asyncio.run(review.run(reject="zzzzzzzz")), "ID inexistente"
    assert not asyncio.run(review.run(reject=" ")), "ID vazio"
    with integration_database.sessions() as session:
        entry_id = str(session.scalar(select(ProductIdentityCatalogEntry.id)))
    assert not asyncio.run(review.run(activate=entry_id)), "já ativa"
    assert asyncio.run(review.run(reject=entry_id))
    capsys.readouterr()
    assert asyncio.run(review.run())
    assert "Catálogo vazio." in capsys.readouterr().out
    assert asyncio.run(review.run(list_all=True))
    assert "[rejected/seed]" in capsys.readouterr().out
