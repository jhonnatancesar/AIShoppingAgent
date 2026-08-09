"""Garantias locais do orquestrador de integração."""

import importlib.util
import sys
from pathlib import Path

import pytest

_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "run_integration_tests.py"
_SPEC = importlib.util.spec_from_file_location("integration_runner", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
integration_runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = integration_runner
_SPEC.loader.exec_module(integration_runner)

POSTGRES_IMAGE = integration_runner.POSTGRES_IMAGE
IntegrationRunnerError = integration_runner.IntegrationRunnerError
RuntimeResources = integration_runner.RuntimeResources
_assert_no_external_database_configuration = (
    integration_runner._assert_no_external_database_configuration
)
_parse_loopback_port = integration_runner._parse_loopback_port
_sanitized = integration_runner._sanitized


def test_postgres_integration_image_is_patch_and_digest_pinned() -> None:
    assert POSTGRES_IMAGE.startswith("postgres:18.4-alpine@sha256:")
    assert len(POSTGRES_IMAGE.rsplit("sha256:", 1)[1]) == 64


def test_runner_rejects_external_database_configuration() -> None:
    with pytest.raises(IntegrationRunnerError, match="execução recusada"):
        _assert_no_external_database_configuration(
            {"DATABASE_URL": "postgresql://external.invalid/database"}
        )


def test_runner_rejects_production_environment() -> None:
    with pytest.raises(IntegrationRunnerError, match="produção"):
        _assert_no_external_database_configuration(
            {"AISHOPPING_ENVIRONMENT": "production"}
        )


def test_runner_accepts_only_loopback_random_port_mapping() -> None:
    assert _parse_loopback_port("127.0.0.1:55432\n") == 55432
    with pytest.raises(IntegrationRunnerError, match="loopback"):
        _parse_loopback_port("0.0.0.0:55432\n")


def test_runner_redacts_synthetic_credentials_from_diagnostics() -> None:
    resources = RuntimeResources(
        run_id="run",
        container_name="container",
        volume_name="volume",
        database_name="database",
        database_user="user",
        database_password="synthetic-password-canary",
        guard_token="synthetic-guard-canary",
    )
    sanitized = _sanitized(
        "synthetic-password-canary synthetic-guard-canary", resources
    )
    assert sanitized == "[REDACTED] [REDACTED]"
