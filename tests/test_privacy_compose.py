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


def test_operational_ports_bind_to_loopback_by_default() -> None:
    content = COMPOSE.read_text(encoding="utf-8")

    assert "${API_BIND_ADDRESS:-127.0.0.1}:${API_PORT:-8000}:8000" in content
    assert "${POSTGRES_BIND_ADDRESS:-127.0.0.1}:${POSTGRES_PORT:-5432}:5432" in content
    assert (
        "${PROMETHEUS_BIND_ADDRESS:-127.0.0.1}:${PROMETHEUS_PORT:-9090}:9090" in content
    )
    assert "${JAEGER_BIND_ADDRESS:-127.0.0.1}:${JAEGER_UI_PORT:-16686}:16686" in content
    assert '"127.0.0.1:${OTEL_HEALTH_PORT:-13133}:13133"' in content
