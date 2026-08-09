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
    assert {constraint.name for constraint in table.constraints} >= {
        "ck_collection_runs_terminal_finished",
        "ck_collection_runs_time_order",
    }
    assert {index.name for index in table.indexes} == {
        "ix_collection_runs_mission_started_at",
        "ix_collection_runs_store_started_at",
        "uq_collection_runs_running_mission_store",
    }
