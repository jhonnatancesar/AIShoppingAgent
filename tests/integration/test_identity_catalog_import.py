"""TASK-137 -- importação de fontes abertas contra Postgres real: idempotência,
conflito de código e resolução do título pelo catálogo, sem IA."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from app.ai_provider import AIResponse
from app.products.identity_catalog_import import (
    MappingReport,
    import_open_entries,
    map_buildcores_motherboard,
    map_buildcores_ram,
)
from app.products.identity_catalog_models import (
    ProductIdentityCatalogCode,
    ProductIdentityCatalogEntry,
)
from app.products.identity_learning import resolve_or_learn_product_variant
from app.users.models import UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


class _AI:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=json.dumps(
                {"category": "", "brand": "", "family": "", "model": ""}
            ),
            finished_at=datetime.now(UTC),
        )


def _board(opendb_id, name, part_numbers):
    return {
        "opendb_id": opendb_id,
        "socket": "AM5",
        "form_factor": "Micro ATX",
        "chipset": "AMD B650",
        "memory": {"ram_type": "DDR5"},
        "metadata": {
            "name": name,
            "manufacturer": "MSI",
            "part_numbers": part_numbers,
            "series": "B650M",
        },
    }


def _entries():
    report = MappingReport()
    return [
        map_buildcores_motherboard(
            _board(
                "o1", "MSI B650M PRO AM5 DDR5 Micro ATX", ["B650M PRO", "7E28-001R"]
            ),
            report,
        ),
        map_buildcores_ram(
            {
                "opendb_id": "o2",
                "ram_type": "DDR5",
                "capacity": 32,
                "speed": 6000,
                "modules": {"quantity": 2, "capacity_gb": 16},
                "cas_latency": 30,
                "metadata": {
                    "name": "Kingston FURY Beast DDR5-6000 CL30 32GB (2x16GB)",
                    "manufacturer": "Kingston",
                    "part_numbers": ["KF560C30BBK2-32"],
                    "series": "FURY Beast",
                },
            },
            report,
        ),
    ]


def _import(integration_database, entries):
    async def _run():
        async with integration_database.async_sessions() as session:
            stats = await import_open_entries(session, entries)
            await session.commit()
            return stats

    return asyncio.run(_run())


def _count(integration_database, model):
    with integration_database.sessions() as session:
        return session.scalar(select(func.count()).select_from(model))


def _resolve(integration_database, title, ai):
    async def _run():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=ai, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    return asyncio.run(_run())


def test_import_is_idempotent_and_records_the_source(integration_database) -> None:
    first = _import(integration_database, _entries())
    assert (first.entries_new, first.codes_new) == (2, 4)
    second = _import(integration_database, _entries())
    assert (second.entries_new, second.entries_existing, second.codes_new) == (0, 2, 0)
    assert second.codes_conflict == 0
    assert _count(integration_database, ProductIdentityCatalogEntry) == 2
    with integration_database.sessions() as session:
        sources = {
            (row.source, row.source_ref)
            for row in session.scalars(select(ProductIdentityCatalogEntry))
        }
    assert sources == {("buildcores", "o1"), ("buildcores", "o2")}


def test_code_owned_by_another_entry_is_never_taken_over(integration_database) -> None:
    _import(integration_database, _entries())
    rival = map_buildcores_motherboard(
        _board("o3", "MSI B650M OTHER AM5 DDR5 Micro ATX", ["7E28-001R"]),
        MappingReport(),
    )
    stats = _import(integration_database, [rival])
    assert stats.entries_new == 1
    assert stats.codes_conflict == 1
    with integration_database.sessions() as session:
        owner = session.scalar(
            select(ProductIdentityCatalogEntry.source_ref)
            .join(
                ProductIdentityCatalogCode,
                ProductIdentityCatalogCode.entry_id == ProductIdentityCatalogEntry.id,
            )
            .where(ProductIdentityCatalogCode.value_normalized == "7E28001R")
        )
    assert owner == "o1"


def test_imported_board_resolves_the_title_without_calling_the_ai(
    integration_database,
) -> None:
    _import(integration_database, _entries())
    ai = _AI()
    resolved = _resolve(
        integration_database, "Placa-mae MSI B650M PRO, AM5, DDR5, mATX", ai
    )
    assert resolved is not None
    assert resolved.category == "motherboard"
    assert resolved.brand == "msi"
    assert "DDR5" in dict(resolved.attributes).values()
    assert ai.calls == 0


def test_imported_ram_resolves_by_part_number_without_the_ai(
    integration_database,
) -> None:
    _import(integration_database, _entries())
    ai = _AI()
    resolved = _resolve(
        integration_database,
        "Memoria Kingston Fury Beast 32GB (2x16GB) DDR5 6000MHz KF560C30BBK2-32",
        ai,
    )
    assert resolved is not None
    assert resolved.category == "ram"
    assert "DDR5" in dict(resolved.attributes).values()
    assert ai.calls == 0


def test_a_longer_model_name_never_resolves_to_the_shorter_imported_board(
    integration_database,
) -> None:
    _import(integration_database, _entries())
    ai = _AI()
    resolved = _resolve(
        integration_database, "Placa-mae MSI B650M PRO-A AM5 DDR5 mATX", ai
    )
    assert resolved is None or resolved.model != "b650m-pro-ddr5"
    assert ai.calls >= 1
