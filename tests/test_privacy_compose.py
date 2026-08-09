"""Contratos de retenção operacional declarados no Docker Compose."""

from pathlib import Path

COMPOSE = Path(__file__).parents[1] / "compose.yaml"


def test_compose_bounds_logs_metrics_and_in_memory_traces() -> None:
    content = COMPOSE.read_text(encoding="utf-8")

    assert 'max-size: "10m"' in content
    assert 'max-file: "5"' in content
    assert "--storage.tsdb.retention.time=15d" in content
    assert "--storage.tsdb.retention.size=2GB" in content
    assert "memory.max_traces=10000" in content
    assert "mem_limit: 512m" in content
    assert "jaeger_data" not in content
