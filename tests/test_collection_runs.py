from app.collection.models import CollectionRun, CollectionRunStatus
from app.database.model_registry import REGISTERED_MODELS


def test_collection_run_schema_and_statuses() -> None:
    table = CollectionRun.__table__
    assert CollectionRun in REGISTERED_MODELS
    assert {item.value for item in CollectionRunStatus} == {
        "running",
        "succeeded",
        "failed",
    }
    assert table.c.mission_id.nullable is True
    assert table.c.store_id.nullable is False
    # TASK-112 fase 3A/3B: execução compartilhada por (MonitoringItem, store)
    # e trilha de auditoria de fairness -- ambas opcionais no schema, só
    # preenchidas no caminho compartilhado.
    assert table.c.monitoring_item_id.nullable is True
    assert table.c.fairness_owner_user_id.nullable is True
    assert {constraint.name for constraint in table.constraints} >= {
        "ck_collection_runs_terminal_finished",
        "ck_collection_runs_time_order",
        "ck_collection_runs_ownership_xor",
        "ck_collection_runs_fairness_owner_requires_shared",
    }
    assert {index.name for index in table.indexes} == {
        "ix_collection_runs_mission_started_at",
        "ix_collection_runs_store_started_at",
        "uq_collection_runs_running_mission_store",
        # TASK-112 fase 3A: execução compartilhada por (MonitoringItem, store).
        "uq_collection_runs_running_monitoring_item_store",
    }
