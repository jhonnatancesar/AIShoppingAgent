"""Testes da proteção e do provisionamento local de secrets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.scripts import manage_secrets


def test_write_and_validate_secret_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {"database_password": (tmp_path / ".env", "IGNORED")}
    monkeypatch.setattr(manage_secrets, "SECRET_SOURCES", sources)

    manage_secrets._write_secret(tmp_path / "database_password", "safe-test-value")
    manage_secrets.validate_directory(tmp_path)

    assert (tmp_path / "database_password").read_text() == "safe-test-value"
    if os.name != "nt":
        assert (tmp_path / "database_password").stat().st_mode & 0o077 == 0


def test_write_secret_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "database_password"
    manage_secrets._write_secret(path, "first-test-value")

    with pytest.raises(manage_secrets.SecretProvisioningError, match="already exists"):
        manage_secrets._write_secret(path, "second-test-value")

    assert path.read_text() == "first-test-value"


@pytest.mark.parametrize("value", ["", "   ", "line-one\nline-two", "bad\x00value"])
def test_write_secret_rejects_invalid_value(tmp_path: Path, value: str) -> None:
    with pytest.raises(manage_secrets.SecretProvisioningError, match="invalid value"):
        manage_secrets._write_secret(tmp_path / "secret", value)


def test_migrate_reads_env_without_printing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.env"
    source.write_text("AISHOPPING_TEST_SECRET=private-test-value\n", encoding="utf-8")
    destination = tmp_path / "destination"
    monkeypatch.setattr(
        manage_secrets,
        "SECRET_SOURCES",
        {"test_secret": (source, "AISHOPPING_TEST_SECRET")},
    )

    manage_secrets.migrate_from_env_files(destination)

    output = capsys.readouterr().out
    assert "private-test-value" not in output
    assert (destination / "test_secret").read_text() == "private-test-value"


def test_validate_directory_never_reports_secret_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_value = "multi-line-private-value"
    (tmp_path / "test_secret").write_text(
        f"{secret_value}\nsecond-line", encoding="utf-8"
    )
    monkeypatch.setattr(
        manage_secrets,
        "SECRET_SOURCES",
        {"test_secret": (tmp_path / "unused", "UNUSED")},
    )

    with pytest.raises(manage_secrets.SecretProvisioningError) as captured:
        manage_secrets.validate_directory(tmp_path)

    assert secret_value not in str(captured.value)
