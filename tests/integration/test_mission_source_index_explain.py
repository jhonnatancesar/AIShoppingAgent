"""EXPLAIN ANALYZE da seleção legada de `MissionSource` due -- TASK-112,
fase 3B, auditoria de índice (correção estrutural: a unidade de
scheduling do caminho legado passou de `MissionSchedule`, por Mission
inteira, para `MissionSource`, por `(mission, store)` -- não "no escuro";
ver `docs/tasks/TASK-112.md`).

Achado real (registrado aqui, não assumido): ao contrário do caminho
compartilhado (`MonitoringItemStore`, que ganhou `ix_monitoring_item_
stores_due`), este teste prova que a query real do caminho legado NÃO
precisa de índice dedicado -- o planejador ancora em `missions` (tabela
pequena, já filtrada por `status='active'`) e alcança `mission_sources`
pelos índices que já existiam (`pk_mission_sources` por `mission_id`,
`uq_mission_schedules_mission_id`), zero sequential scan. Um índice extra
em `next_run_at` (coluna que muda a cada claim bem-sucedida -- escrita
frequente) só teria custo, sem benefício de leitura comprovado.

Volume: 5000 `MissionSource` (bem acima da escala real inicial da V1.2),
2% due (~100 linhas) -- mesma proporção do teste irmão do caminho
compartilhado.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.missions.models import Mission, MissionSchedule, MissionSource, MissionStatus
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select, text

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
_TOTAL_ROWS = 5000
_DUE_FRACTION = 0.02


def test_explain_mission_source_due_query_has_no_sequential_scan(integration_database, capsys) -> None:
    with integration_database.sessions() as session:
        amazon_id = session.scalar(select(Store.id).where(Store.code == "amazon"))

    with integration_database.sessions.begin() as session:
        user = User(display_name="Usuário sintético EXPLAIN", role=UserRole.USER)
        session.add(user)
        session.flush()

        missions = [
            Mission(
                user_id=user.id,
                title=f"Synthetic explain mission {i}",
                status=MissionStatus.ACTIVE,
                state_version=1,
            )
            for i in range(_TOTAL_ROWS)
        ]
        session.add_all(missions)
        session.flush()

        schedules = [
            MissionSchedule(
                mission_id=mission.id,
                interval_minutes=60,
                next_run_at=NOW,
                is_enabled=True,
            )
            for mission in missions
        ]
        session.add_all(schedules)

        due_count = int(_TOTAL_ROWS * _DUE_FRACTION)
        sources = [
            MissionSource(
                mission_id=mission.id,
                store_id=amazon_id,
                next_run_at=(NOW - timedelta(minutes=1))
                if index < due_count
                else (NOW + timedelta(hours=2)),
            )
            for index, mission in enumerate(missions)
        ]
        session.add_all(sources)

    async def explain():
        async with integration_database.async_sessions() as session:
            result = await session.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                    "SELECT mission_sources.mission_id, mission_sources.store_id, stores.code, missions.user_id "
                    "FROM mission_sources "
                    "JOIN missions ON missions.id = mission_sources.mission_id "
                    "JOIN stores ON stores.id = mission_sources.store_id "
                    "JOIN mission_schedules ON mission_schedules.mission_id = missions.id "
                    "WHERE missions.status = 'active' "
                    "AND mission_schedules.is_enabled "
                    "AND stores.is_active "
                    "AND (mission_sources.next_run_at IS NULL OR mission_sources.next_run_at <= :due_at) "
                    "AND (mission_sources.next_eligible_at IS NULL OR mission_sources.next_eligible_at <= :due_at) "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM mission_monitoring_items"
                    "  WHERE mission_monitoring_items.mission_id = missions.id"
                    ") "
                    "ORDER BY mission_sources.next_run_at NULLS FIRST, missions.id, stores.id "
                    "LIMIT :scan_limit"
                ),
                {"due_at": NOW, "scan_limit": 1000},
            )
            return "\n".join(row[0] for row in result.all())

    plan = asyncio.run(explain())
    with capsys.disabled():
        print("\n----- EXPLAIN ANALYZE: seleção de MissionSource due (legado) -----")
        print(f"Volume sintético: {_TOTAL_ROWS} MissionSource, {due_count} due (~{_DUE_FRACTION:.0%}).")
        print(plan)
        print("--------------------------------------------------------------------\n")

    # `Seq Scan on missions` é esperado e aceitável -- tabela pequena, já
    # filtrada por `status='active'`, sem relação com o achado desta
    # auditoria (que é sobre `mission_sources`, a tabela que cresce com
    # usuário × loja selecionada).
    assert "Seq Scan on mission_sources" not in plan, (
        "Sequential scan em mission_sources -- plano real:\n" + plan
    )
