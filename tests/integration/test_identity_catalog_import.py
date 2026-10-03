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


# --- Passo A: pedido do usuario -> item compartilhado ----------------------


def _mission_item(integration_database, **criteria):
    from app.missions.models import MissionMonitoringItem, MonitoringItem
    from app.missions.service import create_mission_from_criteria_async
    from app.users.models import User

    with integration_database.sessions.begin() as session:
        user = User(display_name="TASK-137 pedido", role=UserRole.USER)
        session.add(user)
        session.flush()
        user_id = user.id

    async def _create():
        async with integration_database.async_sessions.begin() as session:
            mission, _ = await create_mission_from_criteria_async(
                session,
                user_id=user_id,
                target_amount=None,
                target_currency=None,
                source_codes=("kabum",),
                requested_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
                actor_type="test",
                **criteria,
            )
            return mission

    mission = asyncio.run(_create())
    with integration_database.sessions() as session:
        link = session.get(MissionMonitoringItem, mission.id)
        if link is None:
            return None
        return session.get(MonitoringItem, link.monitoring_item_id)


def test_board_request_found_in_the_catalog_gets_a_shared_item(
    integration_database,
) -> None:
    from app.products.identity import canonical_collection_criteria

    _import(integration_database, _entries())
    item = _mission_item(integration_database, search_query="MSI B650M PRO")
    assert item is not None
    assert item.canonical_identity["category"] == "motherboard"
    criteria = canonical_collection_criteria(item.canonical_identity)
    assert criteria.search_query == "msi b650m pro"
    assert criteria.model == "B650M PRO"


def test_two_wordings_of_the_same_board_share_one_item(integration_database) -> None:
    _import(integration_database, _entries())
    first = _mission_item(integration_database, search_query="MSI B650M PRO")
    second = _mission_item(
        integration_database, search_query="placa mae msi", model="B650M PRO"
    )
    assert first is not None and second is not None
    assert first.id == second.id


def test_request_not_in_the_catalog_keeps_the_old_path(integration_database) -> None:
    _import(integration_database, _entries())
    assert _mission_item(integration_database, search_query="MSI B650M PRO-A") is None
    assert _mission_item(integration_database, search_query="cadeira gamer") is None


# --- Passo C: dúvida resolvida pela IA entre candidatos do banco ------------


class _Chooser:
    def __init__(self, content=None, error=None) -> None:
        self.content, self.error, self.calls = content, error, 0

    async def generate(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-choose",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


def _doubt_catalog(integration_database) -> None:
    report = MappingReport()
    _import(
        integration_database,
        [
            map_buildcores_motherboard(
                _board("c1", "MSI B650M GAMING PLUS AM5 DDR5 Micro ATX", ["7E01-001R"]),
                report,
            ),
            map_buildcores_motherboard(
                _board(
                    "c2", "MSI B650M GAMING PLUS WIFI AM5 DDR5 Micro ATX", ["7E01-002R"]
                ),
                report,
            ),
        ],
    )


def _resolve_requests(integration_database, manager):
    from app.products.identity_catalog_resolution_worker import resolve_pending_requests

    async def _run():
        return await resolve_pending_requests(
            integration_database.async_sessions,
            manager,
            UserRole.ADMIN,
            now=datetime(2026, 10, 3, 13, 0, tzinfo=UTC),
        )

    return asyncio.run(_run())


def _rows(integration_database):
    from app.products.identity_catalog_models import CatalogRequestResolution

    with integration_database.sessions() as session:
        return list(session.scalars(select(CatalogRequestResolution)))


def test_doubtful_request_is_registered_then_ai_picks_among_database_candidates(
    integration_database,
) -> None:
    _doubt_catalog(integration_database)
    # Dúvida: "MSI B650M GAMING" não é nenhum nome exato, mas parece dois produtos.
    assert _mission_item(integration_database, search_query="MSI B650M GAMING") is None
    rows = _rows(integration_database)
    assert [(r.status, len(r.candidates)) for r in rows] == [("pending", 2)]

    chooser = _Chooser(json.dumps({"choice": "A"}))
    summary = _resolve_requests(integration_database, chooser)
    assert (summary.examined, summary.resolved, summary.relinked_missions) == (1, 1, 1)
    assert chooser.calls == 1
    row = _rows(integration_database)[0]
    assert row.status == "resolved" and row.chosen_entry_id is not None
    assert row.ai_provider == "stub"

    # A mesma pergunta nunca gasta IA de novo: o pedido já decidido liga na hora.
    again = _mission_item(integration_database, search_query="MSI B650M GAMING")
    assert again is not None
    assert (
        again.canonical_identity["collection"]["search_query"]
        == "msi b650m gaming plus"
    )
    assert _resolve_requests(integration_database, chooser).examined == 0
    assert chooser.calls == 1


def test_ai_saying_none_leaves_the_mission_as_it_was_and_is_not_asked_again(
    integration_database,
) -> None:
    _doubt_catalog(integration_database)
    assert _mission_item(integration_database, search_query="MSI B650M GAMING") is None
    chooser = _Chooser(json.dumps({"choice": None}))
    summary = _resolve_requests(integration_database, chooser)
    assert (summary.resolved, summary.none) == (0, 1)
    assert _rows(integration_database)[0].status == "none"
    assert _mission_item(integration_database, search_query="MSI B650M GAMING") is None
    _resolve_requests(integration_database, chooser)
    assert chooser.calls == 1


def test_ai_failure_is_recorded_with_a_retry_time_and_pauses_the_ai(
    integration_database,
) -> None:
    from app.ai_provider import AIProviderQuotaExceeded

    _doubt_catalog(integration_database)
    _mission_item(integration_database, search_query="MSI B650M GAMING")
    chooser = _Chooser(error=AIProviderQuotaExceeded())
    summary = _resolve_requests(integration_database, chooser)
    assert summary.failed == 1
    row = _rows(integration_database)[0]
    assert row.status == "ai_failed"
    assert row.ai_error_kind == "quota"
    assert row.attempts == 1 and row.next_retry_at is not None
    # Com a IA em pausa (disjuntor compartilhado), o ciclo seguinte nem chama.
    second = _resolve_requests(integration_database, chooser)
    assert second.breaker_open is True
    assert chooser.calls == 1


def test_incoherent_ai_choice_is_rejected_by_the_deterministic_check(
    integration_database,
) -> None:
    _doubt_catalog(integration_database)
    _mission_item(integration_database, search_query="ASUS B650M GAMING")
    # O pedido cita ASUS, mas os candidatos são MSI: nem vira candidato.
    assert _rows(integration_database) == []


# --- Pedido por especificação ("quero uma b650", "memória ddr5 de 8gb") -----


def test_loose_board_request_becomes_one_shared_family_item(
    integration_database,
) -> None:
    from app.products.identity import canonical_collection_criteria

    _import(integration_database, _entries())  # catálogo conhece o chipset b650
    first = _mission_item(integration_database, search_query="quero uma placa mae b650")
    second = _mission_item(integration_database, search_query="Placa-mãe", model="B650")
    assert first is not None and second is not None
    assert first.id == second.id
    assert first.canonical_identity["scope"] == "family"
    criteria = canonical_collection_criteria(first.canonical_identity)
    assert criteria.search_query == "placa mae b650"
    assert criteria.required_terms == ("chipset:b650",)


def test_loose_memory_request_needs_no_catalog_at_all(integration_database) -> None:
    from app.products.identity import canonical_collection_criteria

    item = _mission_item(
        integration_database, search_query="quero uma memoria ddr5 de 8 Gb"
    )
    assert item is not None
    assert item.canonical_identity["category"] == "ram"
    criteria = canonical_collection_criteria(item.canonical_identity)
    assert criteria.search_query == "memoria ram ddr5 8gb"
    assert criteria.required_terms == ("type:ddr5", "capacity_gb:8")
    other = _mission_item(integration_database, search_query="memoria ddr4 8gb")
    assert other is not None and other.id != item.id


def test_family_request_is_never_a_doubt_for_the_ai(integration_database) -> None:
    _import(integration_database, _entries())
    _mission_item(integration_database, search_query="quero uma b650")
    assert _rows(integration_database) == []
