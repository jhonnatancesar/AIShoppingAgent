"""Provisionamento local de secrets sem exibir seus valores."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import stat
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIRECTORY = PROJECT_ROOT / ".secrets"
SECRET_SOURCES = {
    "postgres_password": (PROJECT_ROOT / ".env", "POSTGRES_PASSWORD"),
    "telegram_bot_token": (
        PROJECT_ROOT / "backend" / ".env",
        "AISHOPPING_TELEGRAM_BOT_TOKEN",
    ),
    "telegram_webhook_secret": (
        PROJECT_ROOT / "backend" / ".env",
        "AISHOPPING_TELEGRAM_WEBHOOK_SECRET",
    ),
    "ops_controller_secret": (
        PROJECT_ROOT / "backend" / ".env",
        "AISHOPPING_OPS_CONTROLLER_SECRET",
    ),
    # TASK-109 (fechamento): nunca gerado aqui -- precisa ser exatamente o
    # mesmo valor já gerado pelo Windows Ops Agent em
    # C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret (ver
    # docs/architecture/windows-collection-worker.md). `init` sempre
    # pede para colar o valor (input oculto), nunca gera um novo.
    "windows_ops_agent_secret": (
        PROJECT_ROOT / "backend" / ".env",
        "AISHOPPING_WINDOWS_OPS_AGENT_SECRET",
    ),
}
_MAX_SECRET_BYTES = 16 * 1024


class SecretProvisioningError(RuntimeError):
    """Configuração local de secret inválida, sem carregar o valor no erro."""


def _dotenv_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        current_key, value = line.split("=", 1)
        if current_key.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def _write_secret(path: Path, value: str, *, overwrite: bool = False) -> None:
    if not value.strip() or "\n" in value or "\r" in value or "\x00" in value:
        raise SecretProvisioningError(f"invalid value for {path.name}")
    if len(value.encode("utf-8")) > _MAX_SECRET_BYTES:
        raise SecretProvisioningError(f"value too large for {path.name}")
    if path.exists() and not overwrite:
        raise SecretProvisioningError(f"{path.name} already exists")

    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_from_env_files(directory: Path, *, overwrite: bool = False) -> None:
    """Migra os valores atuais sem remover nem imprimir os arquivos de origem."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for name, (source, key) in SECRET_SOURCES.items():
        value = _dotenv_value(source, key)
        if value is None or not value.strip():
            missing.append(name)
        else:
            values[name] = value
    if missing:
        raise SecretProvisioningError(
            "missing source values for: " + ", ".join(sorted(missing))
        )
    for name, value in values.items():
        _write_secret(directory / name, value, overwrite=overwrite)
    print(f"Provisioned {len(values)} secret files without displaying values.")


def initialize_interactively(directory: Path) -> None:
    """Gera segredos internos e lê credenciais externas sem eco no terminal."""
    generated = {
        "postgres_password": secrets.token_urlsafe(48),
        "telegram_webhook_secret": secrets.token_urlsafe(48),
        "ops_controller_secret": secrets.token_urlsafe(48),
    }
    for name in SECRET_SOURCES:
        path = directory / name
        if path.exists():
            continue
        value = generated.get(name)
        if value is None:
            value = getpass.getpass(f"Enter {name} (input hidden): ")
        _write_secret(path, value)
    print("Secret files initialized without displaying values.")


def validate_directory(directory: Path) -> None:
    """Valida presença, tipo, tamanho, conteúdo e permissão POSIX."""
    failures: list[str] = []
    for name in SECRET_SOURCES:
        path = directory / name
        try:
            if path.is_symlink() or not path.is_file():
                raise SecretProvisioningError("missing or not a regular file")
            size = path.stat().st_size
            if size == 0 or size > _MAX_SECRET_BYTES:
                raise SecretProvisioningError("invalid file size")
            value = path.read_text(encoding="utf-8")
            normalized = value.removesuffix("\n").removesuffix("\r")
            if (
                not normalized.strip()
                or "\n" in normalized
                or "\r" in normalized
                or "\x00" in normalized
            ):
                raise SecretProvisioningError("invalid content")
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise SecretProvisioningError("permissions must be 0600 or stricter")
        except (OSError, UnicodeError, SecretProvisioningError) as error:
            failures.append(f"{name}: {error}")
    if failures:
        raise SecretProvisioningError("; ".join(failures))
    print(f"Validated {len(SECRET_SOURCES)} secret files without displaying values.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("init", "migrate", "check"), help="operation to execute"
    )
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.action == "init":
            initialize_interactively(args.directory)
        elif args.action == "migrate":
            migrate_from_env_files(args.directory, overwrite=args.overwrite)
        else:
            validate_directory(args.directory)
    except SecretProvisioningError as error:
        print(f"Secret provisioning failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
