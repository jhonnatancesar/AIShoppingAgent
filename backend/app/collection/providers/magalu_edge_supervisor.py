"""Supervisão local do Edge dedicado ao transporte CDP da Magalu."""

import asyncio
import logging
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.collection.providers.magalu_transport import validate_loopback_cdp_endpoint

logger = logging.getLogger("app.collection.magalu_edge_supervisor")


class MagaluEdgeSupervisorError(RuntimeError):
    """Falha ao iniciar ou recuperar o Edge dedicado."""


def discover_edge_executable(explicit_path: Path | None = None) -> Path:
    """Localiza somente instalações normais do Microsoft Edge."""
    if explicit_path is not None:
        candidate = explicit_path.expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise MagaluEdgeSupervisorError("configured Edge executable was not found")

    candidates: list[Path] = []
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(
                Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise MagaluEdgeSupervisorError("Microsoft Edge executable was not found")


def default_magalu_edge_profile_dir() -> Path:
    """Perfil persistente e exclusivo, separado do perfil pessoal do usuário."""
    return Path(tempfile.gettempdir()) / "aishoppingagent-magalu-edge-profile"


class MagaluEdgeSupervisor:
    """Mantém um Edge dedicado disponível em CDP loopback durante o worker."""

    def __init__(
        self,
        endpoint: str,
        *,
        executable: Path | None = None,
        profile_dir: Path | None = None,
        startup_timeout_seconds: float = 30.0,
        probe_interval_seconds: float = 1.0,
        restart_delay_seconds: float = 1.0,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.endpoint = validate_loopback_cdp_endpoint(endpoint)
        parsed = urlsplit(self.endpoint)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Edge CDP endpoint must be loopback")
        self._port = parsed.port
        if self._port is None:
            raise ValueError("Edge CDP endpoint must have an explicit port")
        if startup_timeout_seconds <= 0 or probe_interval_seconds <= 0:
            raise ValueError("supervisor timeouts must be positive")
        if restart_delay_seconds < 0:
            raise ValueError("restart delay must not be negative")
        self._executable = discover_edge_executable(executable)
        self._profile_dir = (profile_dir or default_magalu_edge_profile_dir()).resolve()
        self._startup_timeout_seconds = startup_timeout_seconds
        self._probe_interval_seconds = probe_interval_seconds
        self._restart_delay_seconds = restart_delay_seconds
        self._extra_args = tuple(extra_args)
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._stopping = False
        self._started_process = False

    @property
    def profile_dir(self) -> Path:
        return self._profile_dir

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    async def start(self) -> None:
        """Inicia o Edge quando ausente e ativa recuperação contínua."""
        async with self._lifecycle_lock:
            if self._monitor_task is not None and not self._monitor_task.done():
                return
            self._stopping = False
            if await self._cdp_ready() and not self._started_process:
                if not await self._is_dedicated_browser():
                    raise MagaluEdgeSupervisorError(
                        "configured CDP port is owned by another Edge profile"
                    )
                self._started_process = True
            await self._ensure_running()
            self._monitor_task = asyncio.create_task(
                self._monitor(), name="magalu-edge-supervisor"
            )

    async def stop(self) -> None:
        """Encerra a supervisão; nunca é chamado entre coletas."""
        self._stopping = True
        task = self._monitor_task
        self._monitor_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_dedicated_browser()
        await self._terminate_launcher()

    async def wait_until_ready(self, *, timeout_seconds: float | None = None) -> None:
        """Espera a recuperação sem expor processo ou comandos ao provider."""
        timeout = timeout_seconds or self._startup_timeout_seconds
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self._cdp_ready():
                return
            await asyncio.sleep(min(self._probe_interval_seconds, 0.25))
        raise MagaluEdgeSupervisorError("Edge CDP did not become ready")

    async def _monitor(self) -> None:
        while not self._stopping:
            try:
                if not await self._cdp_ready():
                    async with self._lifecycle_lock:
                        if not self._stopping and not await self._cdp_ready():
                            await self._ensure_running()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "magalu_edge_recovery_failed",
                    extra={"supervisor_failure": type(error).__name__},
                )
                await asyncio.sleep(self._restart_delay_seconds)
            await asyncio.sleep(self._probe_interval_seconds)

    async def _ensure_running(self) -> None:
        if await self._cdp_ready():
            return
        await self._terminate_launcher()
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        arguments = [
            str(self._executable),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            "--start-minimized",
            *self._extra_args,
            "about:blank",
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(asyncio.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creationflags == 0:
                import subprocess

                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            self._process = await asyncio.create_subprocess_exec(
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as error:
            raise MagaluEdgeSupervisorError("could not start dedicated Edge") from error
        self._started_process = True
        try:
            await self.wait_until_ready()
        except Exception:
            await self._terminate_launcher()
            raise
        logger.info("magalu_edge_ready")

    async def _terminate_launcher(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def _close_dedicated_browser(self) -> None:
        if not await self._cdp_ready():
            return
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(
                    self.endpoint, timeout=5_000
                )
                session = await browser.new_browser_cdp_session()
                await session.send("Browser.close")
        except PlaywrightError:
            logger.warning("magalu_edge_shutdown_failed")

    async def _is_dedicated_browser(self) -> bool:
        """Adota após restart do worker somente o Edge com o perfil esperado."""
        expected = f"--user-data-dir={self._profile_dir}"
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(
                    self.endpoint, timeout=5_000
                )
                session = await browser.new_browser_cdp_session()
                result = await session.send("Browser.getBrowserCommandLine")
        except PlaywrightError:
            return False
        arguments = result.get("arguments", [])
        return any(
            str(argument).casefold() == expected.casefold() for argument in arguments
        )

    async def _cdp_ready(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(f"{self.endpoint}/json/version")
            if response.status_code != 200:
                return False
            payload = response.json()
        except httpx.HTTPError, TypeError, ValueError:
            return False
        browser = str(payload.get("Browser") or "")
        websocket_url = str(payload.get("webSocketDebuggerUrl") or "")
        return browser.startswith("Edg/") and websocket_url.startswith(
            ("ws://127.0.0.1:", "ws://localhost:", "ws://[::1]:")
        )
