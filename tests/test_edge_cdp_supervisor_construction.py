"""Testes unitários puros de `edge_cdp_supervisor.py`: funções de módulo e
validação síncrona do construtor de `EdgeCdpSupervisor` -- sem Edge/CDP/
subprocess reais. Complementa `tests/test_edge_cdp_supervisor.py` (máquina
de estados assíncrona de lease/idle-timeout), que já cobre o resto do
arquivo."""

import shutil
import tempfile
from pathlib import Path

import pytest
from app.collection.providers.edge_cdp_supervisor import (
    EdgeCdpSupervisor,
    EdgeCdpSupervisorError,
    default_edge_profile_dir,
    discover_edge_executable,
)


@pytest.fixture
def tmp_path():
    """Mesma justificativa de `tests/test_edge_cdp_supervisor.py`: `tmp_path`
    padrão do pytest pode ficar com ACL travada neste ambiente."""
    path = Path(tempfile.mkdtemp(prefix="aishoppingagent-edge-cdp-construct-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_discover_edge_executable_returns_existing_explicit_path(tmp_path):
    executable = tmp_path / "msedge.exe"
    executable.write_bytes(b"")
    assert discover_edge_executable(executable) == executable.resolve()


def test_discover_edge_executable_translates_not_found_error(tmp_path):
    missing = tmp_path / "does-not-exist.exe"
    with pytest.raises(EdgeCdpSupervisorError):
        discover_edge_executable(missing)


def test_default_edge_profile_dir_is_under_tempdir_and_stable():
    result = default_edge_profile_dir()
    assert result.name == "aishoppingagent-magalu-edge-profile"
    assert result == default_edge_profile_dir()


def _build_supervisor(tmp_path: Path, **overrides: object) -> EdgeCdpSupervisor:
    fake_executable = tmp_path / "msedge.exe"
    fake_executable.write_bytes(b"")
    kwargs: dict[str, object] = {
        "executable": fake_executable,
        "profile_dir": tmp_path / "profile",
    }
    kwargs.update(overrides)
    return EdgeCdpSupervisor("http://127.0.0.1:9223", **kwargs)


def test_profile_dir_property_returns_resolved_configured_dir(tmp_path):
    supervisor = _build_supervisor(tmp_path)
    assert supervisor.profile_dir == (tmp_path / "profile").resolve()


def test_constructor_rejects_non_positive_startup_timeout(tmp_path):
    with pytest.raises(ValueError, match="supervisor timeouts must be positive"):
        _build_supervisor(tmp_path, startup_timeout_seconds=0)


def test_constructor_rejects_non_positive_probe_interval(tmp_path):
    with pytest.raises(ValueError, match="supervisor timeouts must be positive"):
        _build_supervisor(tmp_path, probe_interval_seconds=0)


def test_constructor_rejects_negative_restart_delay(tmp_path):
    with pytest.raises(ValueError, match="restart delay must not be negative"):
        _build_supervisor(tmp_path, restart_delay_seconds=-1)


def test_constructor_rejects_non_positive_idle_timeout(tmp_path):
    with pytest.raises(ValueError, match="idle_timeout_seconds must be positive"):
        _build_supervisor(tmp_path, idle_timeout_seconds=0)


def test_constructor_rejects_hostname_outside_loopback_allowlist(tmp_path, monkeypatch):
    """Defesa redundante em profundidade: mesmo que o validador
    compartilhado (`validate_loopback_cdp_endpoint`) algum dia deixasse
    passar um host fora do allowlist, o construtor ainda recusa."""
    monkeypatch.setattr(
        "app.collection.providers.edge_cdp_supervisor.validate_loopback_cdp_endpoint",
        lambda _endpoint: "http://example.com:9223",
    )
    with pytest.raises(ValueError, match="loopback"):
        _build_supervisor(tmp_path)


def test_constructor_rejects_endpoint_without_explicit_port(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.collection.providers.edge_cdp_supervisor.validate_loopback_cdp_endpoint",
        lambda _endpoint: "http://127.0.0.1",
    )
    with pytest.raises(ValueError, match="explicit port"):
        _build_supervisor(tmp_path)
