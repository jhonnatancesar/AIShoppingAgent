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


def test_api_reaches_cesar_core_via_host_docker_internal() -> None:
    """DEC-121: dentro do container `api`, `127.0.0.1` aponta para o
    próprio container, não para o Windows Server que hospeda o César
    Core -- precisa do mesmo mecanismo host.docker.internal/host-gateway
    já usado por `ops_controller` (DEC-103), agora também aqui."""
    content = COMPOSE.read_text(encoding="utf-8")

    assert (
        "AISHOPPING_CESAR_CORE_BASE_URL: "
        "${AISHOPPING_CESAR_CORE_BASE_URL:-http://host.docker.internal:8100}"
        in content
    )
    assert "AISHOPPING_CESAR_CORE_API_KEY_FILE: /run/secrets/cesar_core_api_key" in content
    assert "- cesar_core_api_key" in content
    assert content.count('"host.docker.internal:host-gateway"') == 2
