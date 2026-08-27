"""EXPLAIN ANALYZE da query de seleção compartilhada contra volume
sintético representativo -- TASK-112, fase 3B, auditoria do índice
`ix_monitoring_item_stores_due` (não "no escuro"; ver `docs/tasks/
TASK-112.md`).

Volume: 5000 `MonitoringItemStore` (bem acima da escala real inicial da
V1.2 -- dezenas de usuários, quotas de TASK-107 limitando cada um a
poucas dezenas de slots -- com boa margem para crescimento, inclusive
para uma futura diferenciação de plano pago que aumente quotas), 2% due
(~100 linhas), o resto não devido -- proporção realista de "poucos itens
due a qualquer instante dado" que o índice precisa servir bem.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.missions.models import MonitoringItem, MonitoringItemStore
from app.stores.models import Store
from sqlalchemy import select, text

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
_TOTAL_ROWS = 5000
_DUE_FRACTION = 0.02


def test_explain_monitoring_item_store_due_query_uses_index(integration_database, capsys) -> None:
    amazon_id = None
    with integration_database.sessions() as session:
        amazon_id = session.scalar(select(Store.id).where(Store.code == "amazon"))

    with integration_database.sessions.begin() as session:
        items = [MonitoringItem(
            monitoring_key=f"synthetic-explain-{i}",
            identity_version=1,
            canonical_identity={"category": "gpu", "model": f"synthetic-{i}"},
        ) for i in range(_TOTAL_ROWS)]
        session.add_all(items)
        session.flush()
        due_count = int(_TOTAL_ROWS * _DUE_FRACTION)
        stores = [
            MonitoringItemStore(
                monitoring_item_id=item.id,
                store_id=amazon_id,
                is_enabled=True,
                next_run_at=(NOW - timedelta(minutes=1)) if index < due_count else (NOW + timedelta(hours=2)),
            )
            for index, item in enumerate(items)
        ]
        session.add_all(stores)

    async def explain():
        async with integration_database.async_sessions() as session:
            result = await session.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                    "SELECT monitoring_item_id, store_id FROM monitoring_item_stores "
                    "WHERE is_enabled AND (next_run_at IS NULL OR next_run_at <= :due_at) "
                    "AND (next_eligible_at IS NULL OR next_eligible_at <= :due_at) "
                    "ORDER BY next_run_at, monitoring_item_id, store_id LIMIT :scan_limit"
                ),
                {"due_at": NOW, "scan_limit": 1000},
            )
            return "\n".join(row[0] for row in result.all())

    plan = asyncio.run(explain())
    with capsys.disabled():
        print("\n----- EXPLAIN ANALYZE: seleção de candidatos compartilhados -----")
        print(f"Volume sintético: {_TOTAL_ROWS} MonitoringItemStore, {due_count} due (~{_DUE_FRACTION:.0%}).")
        print(plan)
        print("-------------------------------------------------------------------\n")

    assert "ix_monitoring_item_stores_due" in plan, (
        "Índice não usado -- plano real:\n" + plan
    )
    assert "Seq Scan" not in plan, "Sequential scan completo -- plano real:\n" + plan
