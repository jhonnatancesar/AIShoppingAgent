"""Prova, contra PostgreSQL real, que `20260901_0002` (uq_users_email) é
segura para um banco já existente com dados legados representativos.

Roda fora da suíte de integração normal (`tests/integration/`) de propósito:
`migrations/env.py` recusa (fail-closed) apontar o Alembic para qualquer
banco que não seja o template de uma corrida de integração quando
`AISHOPPING_INTEGRATION_RUN_ID` está setada -- não dá para fazer downgrade
seguido de upgrade num clone individual por teste sem violar essa trava.
Este script cria seu próprio banco descartável (fora do fluxo de
integração) na instância PostgreSQL de DEV já em execução
(`docker compose up -d database`), sem tocar no banco `aishoppingagent`
real nem em PROD.

Cobre exatamente os pontos pedidos na revisão de segurança: e-mails
duplicados de verdade, diferença só de maiúsculas/minúsculas, espaço nas
pontas, NULL (contas de origem Telegram sem e-mail) -- e prova as duas
metades do comportamento: (1) a migration recusa aplicar e aponta o
problema com clareza quando sobra uma duplicata real após normalizar; (2)
a migration normaliza sozinha e aplica com sucesso quando a única
diferença é maiúscula/minúscula ou espaço.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "backend" / "alembic.ini"
ENV_FILE = PROJECT_ROOT / ".env"
DATABASE_NAME = "email_uniqueness_migration_check"

PRE_MIGRATION_REVISION = "20260901_0001"


class ValidationError(RuntimeError):
    pass


def _read_dev_credentials() -> dict[str, str | int]:
    """Lê usuário/senha/porta do `.env` local (fora do Git, nunca lido
    para dentro deste arquivo) -- as mesmas credenciais que
    `docker compose up -d database` já usa em DEV."""
    if not ENV_FILE.is_file():
        raise ValidationError(
            f"{ENV_FILE} não encontrado -- rode a partir da raiz do "
            "projeto com o ambiente DEV configurado (docker compose up -d database)"
        )
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    missing = [
        name
        for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PORT")
        if not values.get(name)
    ]
    if missing:
        raise ValidationError(f"{ENV_FILE} não define: {', '.join(missing)}")
    return {
        "host": "127.0.0.1",
        "port": int(values["POSTGRES_PORT"]),
        "user": values["POSTGRES_USER"],
        "password": values["POSTGRES_PASSWORD"],
    }


_ADMIN_CONNECTION = _read_dev_credentials()


def _admin_connect(*, dbname: str = "postgres") -> psycopg.Connection:
    return psycopg.connect(autocommit=True, dbname=dbname, **_ADMIN_CONNECTION)


def _scratch_connect() -> psycopg.Connection:
    return psycopg.connect(autocommit=True, dbname=DATABASE_NAME, **_ADMIN_CONNECTION)


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AISHOPPING_")
    }
    environment.update(
        {
            "AISHOPPING_ENVIRONMENT": "test",
            "AISHOPPING_DATABASE_HOST": str(_ADMIN_CONNECTION["host"]),
            "AISHOPPING_DATABASE_PORT": str(_ADMIN_CONNECTION["port"]),
            "AISHOPPING_DATABASE_NAME": DATABASE_NAME,
            "AISHOPPING_DATABASE_USER": str(_ADMIN_CONNECTION["user"]),
            "AISHOPPING_DATABASE_PASSWORD": str(_ADMIN_CONNECTION["password"]),
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_CONFIG), *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _recreate_scratch_database() -> None:
    with _admin_connect() as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{DATABASE_NAME}"')
        connection.execute(
            f'CREATE DATABASE "{DATABASE_NAME}" OWNER "{_ADMIN_CONNECTION["user"]}"'
        )


def _drop_scratch_database() -> None:
    with _admin_connect() as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{DATABASE_NAME}"')


def _seed_legacy_users(rows: list[tuple[str, str | None, str | None]]) -> None:
    with _scratch_connect() as connection:
        for display_name, email, username in rows:
            connection.execute(
                "INSERT INTO users (id, display_name, role, email, username, "
                "created_at, updated_at) VALUES "
                "(gen_random_uuid(), %s, 'USER', %s, %s, now(), now())",
                (display_name, email, username),
            )


def main() -> None:
    results: dict[str, object] = {}

    _recreate_scratch_database()
    try:
        pre = _run_alembic("upgrade", PRE_MIGRATION_REVISION)
        if pre.returncode != 0:
            raise ValidationError(
                f"não foi possível preparar o banco em {PRE_MIGRATION_REVISION}: "
                f"{pre.stdout}\n{pre.stderr}"
            )

        # Dados legados representativos, inseridos via SQL bruto (bypass da
        # validação da aplicação -- simula o que já pode existir num banco
        # anterior a esta subtask).
        _seed_legacy_users(
            [
                ("Duplicata Exata A", "dup@example.com", "legacy_dup_a"),
                ("Duplicata Exata B", "dup@example.com", "legacy_dup_b"),
                ("Case Variant Upper", "CaseVariant@Example.com", "legacy_case_a"),
                ("Case Variant Lower", "casevariant@example.com", "legacy_case_b"),
                ("Com Espaco nas Pontas", "  padded@example.com  ", "legacy_padded"),
                ("Telegram Origem 1", None, None),
                ("Telegram Origem 2", None, None),
            ]
        )

        # 1) Com duplicata VERDADEIRA presente, a migration deve recusar
        # aplicar e apontar o problema com clareza -- nunca um
        # UniqueViolation cru do Postgres, nunca mesclar/apagar contas
        # sozinha.
        blocked = _run_alembic("upgrade", "head")
        combined_output = blocked.stdout + blocked.stderr
        if blocked.returncode == 0:
            raise ValidationError(
                "a migration deveria ter recusado aplicar com duplicata real "
                "presente, mas retornou sucesso"
            )
        if "UniqueViolation" in combined_output and "RuntimeError" not in combined_output:
            raise ValidationError(
                "a migration falhou com um UniqueViolation cru do Postgres em "
                "vez do erro claro e acionável esperado"
            )
        if "dup@example.com" not in combined_output:
            raise ValidationError(
                "o erro da migration não apontou a duplicata real "
                "(dup@example.com) com clareza"
            )
        if "casevariant@example.com" not in combined_output:
            raise ValidationError(
                "o erro da migration não apontou a duplicata de "
                "maiúscula/minúscula (CaseVariant@Example.com) com clareza"
            )
        results["blocks_on_real_duplicates_with_clear_error"] = "passed"

        with _scratch_connect() as connection:
            version = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        if version[0] != PRE_MIGRATION_REVISION:
            raise ValidationError(
                "a tentativa recusada não deveria ter avançado o head Alembic"
            )
        results["rejected_upgrade_leaves_head_unchanged"] = "passed"

        # 2) Resolvida a duplicata verdadeira (mantendo só as variações de
        # maiúscula/minúscula e espaço), a migration deve normalizar
        # sozinha e aplicar com sucesso.
        with _scratch_connect() as connection:
            connection.execute(
                "DELETE FROM users WHERE username IN "
                "('legacy_dup_b', 'legacy_case_b')"
            )

        applied = _run_alembic("upgrade", "head")
        if applied.returncode != 0:
            raise ValidationError(
                "a migration deveria ter aplicado com sucesso após normalizar "
                f"maiúscula/minúscula e espaço, mas falhou: "
                f"{applied.stdout}\n{applied.stderr}"
            )
        results["succeeds_after_resolving_real_duplicates"] = "passed"

        with _scratch_connect() as connection:
            rows = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT username, email FROM users WHERE username IN "
                    "('legacy_dup_a', 'legacy_case_a', 'legacy_padded')"
                ).fetchall()
            }
            null_email_count = connection.execute(
                "SELECT count(*) FROM users WHERE email IS NULL"
            ).fetchone()[0]
            constraint_exists = connection.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = 'uq_users_email'"
            ).fetchone()

        if rows["legacy_case_a"] != "casevariant@example.com":
            raise ValidationError(
                "e-mail com maiúscula não foi normalizado para minúsculas: "
                f"{rows['legacy_case_a']!r}"
            )
        if rows["legacy_padded"] != "padded@example.com":
            raise ValidationError(
                "e-mail com espaço nas pontas não foi normalizado: "
                f"{rows['legacy_padded']!r}"
            )
        if rows["legacy_dup_a"] != "dup@example.com":
            raise ValidationError(
                "e-mail já normalizado foi alterado indevidamente: "
                f"{rows['legacy_dup_a']!r}"
            )
        if null_email_count != 2:
            raise ValidationError(
                "contas de origem Telegram sem e-mail (NULL) foram afetadas "
                f"pela normalização/constraint: esperado 2, encontrado "
                f"{null_email_count}"
            )
        if constraint_exists is None:
            raise ValidationError("uq_users_email não foi criada")
        results["case_and_whitespace_normalized_null_untouched"] = "passed"

        checked = _run_alembic("check")
        if checked.returncode != 0:
            raise ValidationError(
                "alembic check detectou drift entre modelos e migration: "
                f"{checked.stdout}\n{checked.stderr}"
            )
        results["no_autogenerate_drift"] = "passed"
    finally:
        _drop_scratch_database()

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except ValidationError as error:
        print(f"FALHOU: {error}", file=sys.stderr)
        raise SystemExit(1) from error
