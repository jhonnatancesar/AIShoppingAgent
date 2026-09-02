"""Executa a suíte de integração contra PostgreSQL 18 descartável e isolado."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "backend" / "alembic.ini"
INTEGRATION_TESTS = PROJECT_ROOT / "tests" / "integration"
POSTGRES_IMAGE = (
    "postgres:18.4-alpine@"
    "sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
)
READINESS_TIMEOUT_SECONDS = 60
_LABEL_SUITE = "com.aishopping.integration.suite"
_LABEL_RUN_ID = "com.aishopping.integration.run-id"
_FORBIDDEN_DATABASE_ENV = {
    "DATABASE_URL",
    "AISHOPPING_DATABASE_HOST",
    "AISHOPPING_DATABASE_PORT",
    "AISHOPPING_DATABASE_NAME",
    "AISHOPPING_DATABASE_USER",
    "AISHOPPING_DATABASE_PASSWORD",
    "AISHOPPING_DATABASE_PASSWORD_FILE",
}


class IntegrationRunnerError(RuntimeError):
    """Falha segura de preparação ou execução da suíte."""


@dataclass(frozen=True, slots=True)
class RuntimeResources:
    run_id: str
    container_name: str
    volume_name: str
    database_name: str
    database_user: str
    database_password: str
    guard_token: str


def _resolve_docker() -> str:
    command = shutil.which("docker")
    if command:
        return command
    if os.name == "nt":
        candidate = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "DockerDesktop"
            / "resources"
            / "bin"
            / "docker.exe"
        )
        if candidate.is_file():
            return str(candidate)
    raise IntegrationRunnerError("Docker CLI não encontrado; integração é FAIL")


def _assert_no_external_database_configuration(environment: dict[str, str]) -> None:
    environment_name = environment.get("AISHOPPING_ENVIRONMENT", "").strip().lower()
    if environment_name in {"prod", "production"}:
        raise IntegrationRunnerError(
            "ambiente de produção detectado; execução de integração recusada"
        )
    configured = sorted(_FORBIDDEN_DATABASE_ENV.intersection(environment))
    if configured:
        joined = ", ".join(configured)
        raise IntegrationRunnerError(
            "configuração externa de banco detectada; execução recusada: " + joined
        )


def _current_alembic_head() -> str:
    config = Config(str(ALEMBIC_CONFIG))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise IntegrationRunnerError(
            f"esperado exatamente um head Alembic; encontrados: {len(heads)}"
        )
    return heads[0]


def _new_resources() -> RuntimeResources:
    run_id = secrets.token_hex(8)
    return RuntimeResources(
        run_id=run_id,
        container_name=f"aishopping-integration-{run_id}",
        volume_name=f"aishopping-integration-{run_id}",
        database_name=f"aishopping_template_{run_id}",
        database_user=f"aishop_it_{run_id}",
        database_password=secrets.token_urlsafe(32),
        guard_token=secrets.token_urlsafe(32),
    )


def _docker(
    docker: str,
    *arguments: str,
    check: bool = True,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [docker, *arguments],
        cwd=PROJECT_ROOT,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_loopback_port(output: str) -> int:
    match = re.fullmatch(r"127\.0\.0\.1:(\d+)\s*", output)
    if match is None:
        raise IntegrationRunnerError("PostgreSQL não foi publicado apenas no loopback")
    return int(match.group(1))


def _sanitized(value: str, resources: RuntimeResources) -> str:
    sanitized = value.replace(resources.database_password, "[REDACTED]")
    return sanitized.replace(resources.guard_token, "[REDACTED]")


def _child_environment(
    resources: RuntimeResources,
    *,
    port: int,
    head: str,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AISHOPPING_")
        and key not in _FORBIDDEN_DATABASE_ENV
        and not key.startswith("POSTGRES_")
    }
    environment.update(
        {
            "AISHOPPING_ENVIRONMENT": "test",
            "AISHOPPING_DATABASE_HOST": "127.0.0.1",
            "AISHOPPING_DATABASE_PORT": str(port),
            "AISHOPPING_DATABASE_NAME": resources.database_name,
            "AISHOPPING_DATABASE_USER": resources.database_user,
            "AISHOPPING_DATABASE_PASSWORD": resources.database_password,
            "AISHOPPING_GEMINI_API_KEY_USER": "integration-local-boundary",
            "AISHOPPING_GEMINI_API_KEY_ADMIN_DEV": "integration-local-boundary",
            "AISHOPPING_GROQ_API_KEY": "integration-local-boundary",
            "AISHOPPING_TELEGRAM_BOT_TOKEN": "integration-local-boundary",
            "AISHOPPING_TELEGRAM_WEBHOOK_SECRET": "integration-local-boundary",
            "AISHOPPING_VERIFICATION_CODE_PEPPER": "integration-local-boundary",
            "AISHOPPING_OBSERVABILITY_ENABLED": "false",
            "AISHOPPING_INTEGRATION_RUN_ID": resources.run_id,
            "AISHOPPING_INTEGRATION_TEMPLATE_DATABASE": resources.database_name,
            "AISHOPPING_INTEGRATION_GUARD_TOKEN": resources.guard_token,
            "AISHOPPING_INTEGRATION_EXPECTED_HEAD": head,
        }
    )
    return environment


def _wait_for_postgres(resources: RuntimeResources, *, port: int) -> None:
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                host="127.0.0.1",
                port=port,
                dbname=resources.database_name,
                user=resources.database_user,
                password=resources.database_password,
                connect_timeout=2,
            ) as connection:
                connection.execute("SELECT 1").fetchone()
            return
        except psycopg.Error as error:
            last_error = type(error).__name__
            time.sleep(0.5)
    raise IntegrationRunnerError(
        "PostgreSQL não ficou pronto no timeout; erro=" + last_error
    )


def _run_alembic(environment: dict[str, str], *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_CONFIG), *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        diagnostic = (result.stdout + "\n" + result.stderr).strip()
        raise IntegrationRunnerError(
            "Alembic falhou em " + " ".join(arguments) + ": " + diagnostic
        )


def _validate_migrations(
    resources: RuntimeResources,
    *,
    port: int,
    expected_head: str,
    environment: dict[str, str],
) -> None:
    _run_alembic(environment, "upgrade", "head")
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        dbname=resources.database_name,
        user=resources.database_user,
        password=resources.database_password,
    ) as connection:
        revisions = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
        if revisions != [(expected_head,)]:
            raise IntegrationRunnerError(
                "banco não terminou no único head Alembic atual"
            )
    _run_alembic(environment, "downgrade", "-1")
    _run_alembic(environment, "upgrade", expected_head)
    _run_alembic(environment, "check")


def _install_guard(
    resources: RuntimeResources,
    *,
    port: int,
    expected_head: str,
) -> None:
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        dbname=resources.database_name,
        user=resources.database_user,
        password=resources.database_password,
    ) as connection:
        connection.execute(
            "CREATE TABLE integration_test_guard ("
            "run_id text PRIMARY KEY, guard_token text NOT NULL, "
            "expected_head text NOT NULL)"
        )
        connection.execute(
            "INSERT INTO integration_test_guard "
            "(run_id, guard_token, expected_head) VALUES (%s, %s, %s)",
            (resources.run_id, resources.guard_token, expected_head),
        )


def _run_pytest(environment: dict[str, str], pytest_targets: list[str]) -> None:
    targets = pytest_targets or [str(INTEGRATION_TESTS)]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "-m",
            "integration",
            *targets,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
    )
    if result.returncode:
        raise IntegrationRunnerError(
            f"pytest de integração falhou com código {result.returncode}"
        )


def _diagnose(docker: str, resources: RuntimeResources, stage: str) -> None:
    revision = _docker(
        docker,
        "exec",
        resources.container_name,
        "sh",
        "-c",
        'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
        "--tuples-only --no-align "
        '--command="SELECT version_num FROM alembic_version" 2>/dev/null',
        check=False,
        timeout=20,
    )
    reached_revision = (
        revision.stdout.strip() if revision.returncode == 0 else "unavailable"
    )
    try:
        result = _docker(
            docker,
            "logs",
            "--tail",
            "40",
            resources.container_name,
            check=False,
            timeout=20,
        )
    except OSError, subprocess.SubprocessError:
        print(f"diagnóstico: etapa={stage}; logs_indisponíveis", file=sys.stderr)
        return
    logs = _sanitized(result.stdout + result.stderr, resources)
    print(
        f"diagnóstico: etapa={stage}; container={resources.container_name}; "
        f"alembic_revision={reached_revision}",
        file=sys.stderr,
    )
    if logs.strip():
        print(logs.strip(), file=sys.stderr)


def _cleanup(docker: str, resources: RuntimeResources) -> None:
    _docker(
        docker,
        "rm",
        "--force",
        resources.container_name,
        check=False,
        timeout=30,
    )
    _docker(
        docker,
        "volume",
        "rm",
        "--force",
        resources.volume_name,
        check=False,
        timeout=30,
    )
    container_exists = (
        _docker(
            docker,
            "inspect",
            resources.container_name,
            check=False,
            timeout=10,
        ).returncode
        == 0
    )
    volume_exists = (
        _docker(
            docker,
            "volume",
            "inspect",
            resources.volume_name,
            check=False,
            timeout=10,
        ).returncode
        == 0
    )
    if container_exists or volume_exists:
        raise IntegrationRunnerError("cleanup não removeu os recursos exclusivos")


def run(pytest_targets: list[str] | None = None) -> None:
    _assert_no_external_database_configuration(dict(os.environ))
    docker = _resolve_docker()
    head = _current_alembic_head()
    resources = _new_resources()
    stage = "docker_preflight"
    try:
        _docker(docker, "info", timeout=30)
        stage = "volume_create"
        _docker(
            docker,
            "volume",
            "create",
            "--label",
            f"{_LABEL_SUITE}=task-052",
            "--label",
            f"{_LABEL_RUN_ID}={resources.run_id}",
            resources.volume_name,
        )
        stage = "container_start"
        _docker(
            docker,
            "run",
            "--detach",
            "--name",
            resources.container_name,
            "--label",
            f"{_LABEL_SUITE}=task-052",
            "--label",
            f"{_LABEL_RUN_ID}={resources.run_id}",
            "--publish",
            "127.0.0.1::5432",
            "--mount",
            f"type=volume,source={resources.volume_name},target=/var/lib/postgresql",
            "--env",
            f"POSTGRES_DB={resources.database_name}",
            "--env",
            f"POSTGRES_USER={resources.database_user}",
            "--env",
            f"POSTGRES_PASSWORD={resources.database_password}",
            POSTGRES_IMAGE,
            timeout=240,
        )
        stage = "resource_identity"
        label = _docker(
            docker,
            "inspect",
            "--format",
            f'{{{{index .Config.Labels "{_LABEL_RUN_ID}"}}}}',
            resources.container_name,
        ).stdout.strip()
        if label != resources.run_id:
            raise IntegrationRunnerError(
                "label do container não corresponde à execução"
            )
        port = _parse_loopback_port(
            _docker(docker, "port", resources.container_name, "5432/tcp").stdout
        )
        stage = "postgres_readiness"
        _wait_for_postgres(resources, port=port)
        environment = _child_environment(resources, port=port, head=head)
        stage = "alembic_upgrade_and_check"
        _validate_migrations(
            resources,
            port=port,
            expected_head=head,
            environment=environment,
        )
        stage = "integration_guard"
        _install_guard(resources, port=port, expected_head=head)
        stage = "pytest_integration"
        print(
            f"PostgreSQL integration: image=18.4-alpine, head={head}, "
            f"container={resources.container_name}"
        )
        _run_pytest(environment, list(pytest_targets or []))
        print("Suíte de integração PostgreSQL aprovada.")
    except KeyboardInterrupt:
        _diagnose(docker, resources, stage)
        raise
    except (IntegrationRunnerError, OSError, subprocess.SubprocessError) as error:
        _diagnose(docker, resources, stage)
        raise IntegrationRunnerError(
            f"etapa={stage}; erro={type(error).__name__}: {error}"
        ) from error
    finally:
        _cleanup(docker, resources)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pytest_targets",
        nargs="*",
        help="Alvos pytest opcionais; sem valor executa tests/integration.",
    )
    arguments = parser.parse_args()
    try:
        run(arguments.pytest_targets)
    except IntegrationRunnerError as error:
        print(f"Integration suite failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
