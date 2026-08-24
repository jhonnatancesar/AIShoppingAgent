"""Lifecycle sob demanda do EdgeCdpSupervisor (TASK-109): lease/idle-timeout.

Testes locais, sem Edge/CDP/psutil reais -- `_ensure_running`,
`_cdp_ready`, `_close_dedicated_browser` e `_terminate_launcher` são
substituídos por dublês para exercitar só a máquina de estados
(`_acquire`/`_release`/monitor/timer de ociosidade). Convenção do
projeto: `asyncio.run(...)` dentro de teste síncrono, sem
pytest-asyncio (não é dependência do projeto)."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from app.collection.providers.edge_cdp_supervisor import EdgeCdpSupervisor


@pytest.fixture
def tmp_path():
    """`tempfile.mkdtemp()` direto -- o `tmp_path` padrão do pytest usa
    `%TEMP%/pytest-of-User`, que neste ambiente pode ficar com ACL travada
    (Kaspersky), sem relação com o código sob teste."""
    path = Path(tempfile.mkdtemp(prefix="aishoppingagent-edge-cdp-test-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _build_supervisor(
    tmp_path: Path, *, idle_timeout_seconds: float = 0.05
) -> EdgeCdpSupervisor:
    fake_executable = tmp_path / "msedge.exe"
    fake_executable.write_bytes(b"")
    return EdgeCdpSupervisor(
        "http://127.0.0.1:9223",
        executable=fake_executable,
        profile_dir=tmp_path / "profile",
        idle_timeout_seconds=idle_timeout_seconds,
        probe_interval_seconds=0.02,
    )


def _patch_lifecycle(monkeypatch, supervisor: EdgeCdpSupervisor):
    """Dublês: `_cdp_ready` reflete um estado local simples (nunca "adota"
    um Edge inexistente); `_ensure_running` só marca `_started_process` e
    conta chamadas -- sem processo/subprocesso real."""
    calls = {"ensure_running": 0, "close": 0, "terminate": 0}
    state = {"ready": False}

    async def fake_ensure_running() -> None:
        # Replica o guard real de `_ensure_running`: `_acquire()` sempre
        # chama, mas o Edge só é (re)lançado se ainda não estiver pronto.
        if state["ready"]:
            return
        calls["ensure_running"] += 1
        supervisor._started_process = True
        state["ready"] = True

    async def fake_cdp_ready() -> bool:
        return state["ready"]

    async def fake_close() -> None:
        calls["close"] += 1
        state["ready"] = False

    async def fake_terminate() -> None:
        calls["terminate"] += 1

    monkeypatch.setattr(supervisor, "_ensure_running", fake_ensure_running)
    monkeypatch.setattr(supervisor, "_cdp_ready", fake_cdp_ready)
    monkeypatch.setattr(supervisor, "_close_dedicated_browser", fake_close)
    monkeypatch.setattr(supervisor, "_terminate_launcher", fake_terminate)
    return calls, state


def test_lease_launches_edge_on_demand(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        assert supervisor.active_leases == 0
        assert calls["ensure_running"] == 0

        async with supervisor.lease():
            assert supervisor.active_leases == 1
            assert calls["ensure_running"] == 1

        await supervisor._stop_monitor()

    asyncio.run(scenario())


def test_lease_reused_across_batch_without_relaunch(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            async with supervisor.lease():
                assert supervisor.active_leases == 2
                assert calls["ensure_running"] == 1
            assert supervisor.active_leases == 1
            assert calls["ensure_running"] == 1

        for _ in range(3):
            async with supervisor.lease():
                pass
        assert calls["ensure_running"] == 1

        await supervisor._stop_monitor()

    asyncio.run(scenario())


def test_idle_timeout_closes_edge_after_configured_period(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path, idle_timeout_seconds=0.05)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            pass

        assert calls["close"] == 0
        await asyncio.sleep(0.2)

        assert calls["close"] == 1
        assert calls["terminate"] == 1
        assert supervisor._started_process is False
        assert supervisor.active_leases == 0

    asyncio.run(scenario())


def test_closed_during_idle_does_not_relaunch_until_new_lease(
    tmp_path, monkeypatch
) -> None:
    supervisor = _build_supervisor(tmp_path, idle_timeout_seconds=0.05)
    calls, _state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            pass
        await asyncio.sleep(0.2)
        assert calls["ensure_running"] == 1

        await asyncio.sleep(0.2)
        assert calls["ensure_running"] == 1

        async with supervisor.lease():
            assert calls["ensure_running"] == 2

        await supervisor._stop_monitor()

    asyncio.run(scenario())


def test_manual_close_while_idle_before_timeout_does_not_relaunch(
    tmp_path, monkeypatch
) -> None:
    supervisor = _build_supervisor(tmp_path, idle_timeout_seconds=1.0)
    calls, state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            pass
        assert calls["ensure_running"] == 1

        state["ready"] = False
        await asyncio.sleep(0.15)

        assert calls["ensure_running"] == 1

    asyncio.run(scenario())


def test_edge_killed_during_active_lease_recovers(tmp_path, monkeypatch) -> None:
    supervisor = _build_supervisor(tmp_path)
    calls, state = _patch_lifecycle(monkeypatch, supervisor)

    async def scenario() -> None:
        async with supervisor.lease():
            assert calls["ensure_running"] == 1

            state["ready"] = False
            await asyncio.sleep(0.15)

            assert calls["ensure_running"] >= 2

        await supervisor._stop_monitor()

    asyncio.run(scenario())
