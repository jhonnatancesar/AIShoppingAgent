"""Supervisão local do Edge dedicado, compartilhado por todos os providers.

TASK-109: generalizado a partir do antigo `MagaluEdgeSupervisor` -- o Edge
supervisionado aqui é infraestrutura do `collection_worker`, não de uma
loja específica (Magalu, Terabyte, Mercado Livre, Amazon, Kabum, Pichau e
o `StoreProductIdentityResolver` só reaproveitam o mesmo processo/perfil/
CDP já supervisionado). Esta classe não conhece nenhuma loja: inicia o
Edge sob demanda, gerencia perfil dedicado, expõe CDP, monitora liveness
enquanto há uso ativo, recupera automaticamente durante uso, encerra por
idle quando não há mais uso, e evita processos duplicados/órfãos -- nada
além disso.

Lifecycle sob demanda (TASK-109): o Edge só é iniciado no primeiro
`lease()`; enquanto houver pelo menos uma lease ativa, um monitor recupera
o Edge automaticamente se ele morrer; quando a última lease é liberada, um
timer de ociosidade começa (`idle_timeout_seconds`) -- se nenhuma lease
nova chegar antes do timeout, o Edge é encerrado normalmente e o
supervisor volta a ficar dormente (sem monitor, sem processo) até a
próxima necessidade real. Fechar o Edge manualmente enquanto ocioso (sem
lease ativa) nunca causa relançamento imediato -- só a próxima `lease()`
relança, sob demanda, exatamente como o próprio timeout já faria.
"""

import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.collection.edge_discovery import (
    EdgeExecutableNotFoundError,
    discover_edge_executable as _discover_edge_executable,
)
from app.collection.providers.edge_cdp_endpoint import validate_loopback_cdp_endpoint

logger = logging.getLogger("app.collection.edge_cdp_supervisor")

# TASK-109: default de fábrica do tempo de ociosidade -- só usado quando o
# chamador não passa `idle_timeout_seconds` (ex.: `Settings`, via
# `build_edge_supervisor`). Nunca lido diretamente fora do `__init__`.
DEFAULT_EDGE_IDLE_TIMEOUT_SECONDS = 180.0


class EdgeCdpSupervisorError(RuntimeError):
    """Falha ao iniciar ou recuperar o Edge dedicado."""


def discover_edge_executable(explicit_path: Path | None = None) -> Path:
    """Localiza somente instalações normais do Microsoft Edge.

    TASK-109 (fechamento): a busca em si mora em `edge_cdp_endpoint.py`
    (compartilhada com `BrowserSession`, que não é supervisionada) --
    aqui só traduz a falha para `EdgeCdpSupervisorError`, contrato já
    usado pelos chamadores existentes (`build_edge_supervisor` etc.)."""
    try:
        return _discover_edge_executable(explicit_path)
    except EdgeExecutableNotFoundError as error:
        raise EdgeCdpSupervisorError(str(error)) from error


def default_edge_profile_dir() -> Path:
    """Perfil persistente e exclusivo, separado do perfil pessoal do usuário.

    Nome da pasta em disco mantido igual ao original (`...-magalu-edge-...`)
    de propósito, mesmo com a classe generalizada (TASK-109): um Edge já
    em execução de antes desta mudança precisa continuar reconhecível por
    `_find_dedicated_edge_process()` -- renomear a pasta faria o supervisor
    rejeitar um processo legítimo já supervisionado como "de outro perfil"
    até o próximo restart completo do ambiente."""
    return Path(tempfile.gettempdir()) / "aishoppingagent-magalu-edge-profile"


class EdgeCdpSupervisor:
    """Mantém um Edge dedicado disponível em CDP loopback sob demanda."""

    def __init__(
        self,
        endpoint: str,
        *,
        executable: Path | None = None,
        profile_dir: Path | None = None,
        startup_timeout_seconds: float = 30.0,
        probe_interval_seconds: float = 1.0,
        restart_delay_seconds: float = 1.0,
        idle_timeout_seconds: float = DEFAULT_EDGE_IDLE_TIMEOUT_SECONDS,
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
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        self._executable = discover_edge_executable(executable)
        self._profile_dir = (profile_dir or default_edge_profile_dir()).resolve()
        self._startup_timeout_seconds = startup_timeout_seconds
        self._probe_interval_seconds = probe_interval_seconds
        self._restart_delay_seconds = restart_delay_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._extra_args = tuple(extra_args)
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._idle_timer_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._started_process = False
        # TASK-109: contador de uso ativo -- nunca negativo. >0 mantém o
        # monitor de recuperação ligado; a transição pra 0 dispara o timer
        # de ociosidade. Não é um lock de exclusão mútua (múltiplos
        # consumidores usam o mesmo Edge ao mesmo tempo, cada um com sua
        # própria página/conexão CDP), só uma contagem de "alguém precisa
        # que o Edge continue vivo agora".
        self._active_leases = 0

    @property
    def profile_dir(self) -> Path:
        return self._profile_dir

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def active_leases(self) -> int:
        return self._active_leases

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        """Empresta o Edge dedicado pela duração do bloco `async with`.

        Providers nunca chamam isto diretamente (TASK-109): quem usa é
        `EdgeCdpTransport`/`CdpMagaluSearchTransport`, transparente para o
        provider. Primeira lease ativa inicia o Edge (se ausente/adota se
        já existir) e liga o monitor de recuperação; última lease liberada
        começa a contagem de ociosidade. Nunca decide nada de negócio, só
        lifecycle do processo."""
        await self._acquire()
        try:
            yield
        finally:
            await self._release()

    async def ensure_started(self) -> None:
        """Garante o Edge rodando, sem lease/monitor/timer -- para quem
        gerencia o próprio ciclo de vida por fora, num limite de
        `asyncio.run()` só seu (ex.: fixture de sessão da suíte de
        testes, `tests/conftest.py`, que também chama `stop()` num
        `asyncio.run()` separado no fim). Nunca usado pelo
        `collection_worker` real (esse sempre passa por `lease()`) --
        por isso nunca liga monitor/timer, que criariam `asyncio.Task`
        presos ao loop deste `asyncio.run()` e quebrariam ao serem
        reaproveitados por um `stop()` chamado de outro loop depois."""
        async with self._lifecycle_lock:
            await self._ensure_running()

    async def _acquire(self) -> None:
        async with self._lifecycle_lock:
            self._active_leases += 1
            if self._active_leases > 1:
                return
            await self._cancel_idle_timer()
            if await self._cdp_ready() and not self._started_process:
                if await self._find_dedicated_edge_process() is None:
                    self._active_leases -= 1
                    raise EdgeCdpSupervisorError(
                        "configured CDP port is owned by another Edge profile"
                    )
                self._started_process = True
            await self._ensure_running()
            self._start_monitor()

    async def _release(self) -> None:
        async with self._lifecycle_lock:
            self._active_leases = max(0, self._active_leases - 1)
            if self._active_leases > 0:
                return
            await self._stop_monitor()
            self._start_idle_timer()

    async def stop(self) -> None:
        """Encerramento definitivo do worker -- nunca chamado entre leases."""
        async with self._lifecycle_lock:
            self._active_leases = 0
            await self._stop_monitor()
            await self._cancel_idle_timer()
        await self._close_dedicated_browser()
        await self._terminate_launcher()

    async def close_via_cdp(self) -> None:
        """Encerra o Edge só pelo protocolo CDP (`Browser.close`), sem
        tocar no `asyncio.subprocess.Process` que o lançou -- para quem
        chama `ensure_started()`/`close_via_cdp()` de dois `asyncio.run()`
        separados (ex.: fixture de sessão de teste, `tests/conftest.py`):
        o handle do subprocesso fica preso ao loop que o criou
        (`_terminate_launcher`/`process.wait()` quebra com
        "attached to a different loop" se chamado de outro loop depois).
        O processo real ainda termina -- só não é formalmente esperado
        (`wait()`) pelo Python; aceitável para teste, nunca usado pelo
        `collection_worker` real (que sempre roda `lease()`/`stop()` no
        mesmo loop, via `stop()` acima)."""
        await self._close_dedicated_browser()

    async def wait_until_ready(self, *, timeout_seconds: float | None = None) -> None:
        """Espera a recuperação sem expor processo ou comandos ao provider."""
        timeout = timeout_seconds or self._startup_timeout_seconds
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self._cdp_ready():
                return
            await asyncio.sleep(min(self._probe_interval_seconds, 0.25))
        raise EdgeCdpSupervisorError("Edge CDP did not become ready")

    def _start_monitor(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._monitor(), name="edge-cdp-supervisor"
            )

    async def _stop_monitor(self) -> None:
        """Cancela e espera terminar antes de devolver -- garante que
        `_start_monitor` nunca veja a tarefa antiga ainda "não terminada"
        e deixe de criar uma nova por engano."""
        task = self._monitor_task
        self._monitor_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _start_idle_timer(self) -> None:
        if self._idle_timer_task is None or self._idle_timer_task.done():
            self._idle_timer_task = asyncio.create_task(
                self._idle_timeout_watch(), name="edge-cdp-idle-timer"
            )

    async def _cancel_idle_timer(self) -> None:
        task = self._idle_timer_task
        self._idle_timer_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _idle_timeout_watch(self) -> None:
        """Só encerra o Edge se ninguém pegou uma lease nova nesse meio
        tempo -- reconfere `_active_leases` sob o lock antes de agir, nunca
        confia só em ter sobrevivido ao `sleep` sem ser cancelado. O
        encerramento em si também roda com o lock preso: uma `lease()`
        nova que chegue exatamente nesse instante espera o encerramento
        terminar antes de relançar, em vez de arriscar adotar um processo
        que já está sendo fechado."""
        try:
            await asyncio.sleep(self._idle_timeout_seconds)
        except asyncio.CancelledError:
            return
        async with self._lifecycle_lock:
            if self._active_leases > 0:
                return
            self._idle_timer_task = None
            self._started_process = False
            logger.info("edge_cdp_idle_timeout")
            await self._close_dedicated_browser()
            await self._terminate_launcher()

    async def _monitor(self) -> None:
        while True:
            try:
                if not await self._cdp_ready():
                    async with self._lifecycle_lock:
                        if self._active_leases > 0 and not await self._cdp_ready():
                            await self._ensure_running()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "edge_cdp_recovery_failed",
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
            raise EdgeCdpSupervisorError("could not start dedicated Edge") from error
        self._started_process = True
        try:
            await self.wait_until_ready()
        except Exception:
            await self._terminate_launcher()
            raise
        logger.info("edge_cdp_ready")

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
            logger.warning("edge_cdp_shutdown_failed")

    async def _find_dedicated_edge_process(self) -> int | None:
        """Identifica com segurança, por inspeção nativa de processos do
        Windows (`psutil`, sem shell), o processo do Edge dedicado do
        AIShoppingAgent já em execução -- usado só para decidir se um
        Edge encontrado no CDP configurado pode ser adotado após um
        restart do worker.

        TASK-109: substitui `Browser.getBrowserCommandLine` (CDP), que
        neste ambiente passou a falhar com "Command line not returned
        because --enable-automation not set" -- fato comprovado por
        teste direto, sem depender de suposição sobre a causa (não
        adicionamos `--enable-automation` só para contornar isso).

        Adoção exige TODAS as condições, nunca por heurística de nome
        isolada: processo `msedge.exe`, sem `--type=` (exclui processos
        filhos -- renderer/GPU/utility herdam os mesmos flags do
        processo principal e dariam falso positivo), com
        `--remote-debugging-port=<porta configurada>` e
        `--user-data-dir=<perfil dedicado configurado>` batendo
        exatamente (comparação por argumento inteiro, não substring)."""
        expected_port_arg = f"--remote-debugging-port={self._port}".casefold()
        expected_profile_arg = f"--user-data-dir={self._profile_dir}".casefold()

        def _scan() -> int | None:
            for process in psutil.process_iter(["name", "cmdline"]):
                try:
                    name = (process.info["name"] or "").casefold()
                    if name != "msedge.exe":
                        continue
                    cmdline = process.info["cmdline"] or []
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                normalized = [str(argument).casefold() for argument in cmdline]
                if any(argument.startswith("--type=") for argument in normalized):
                    continue
                if expected_port_arg not in normalized:
                    continue
                if expected_profile_arg not in normalized:
                    continue
                return process.pid
            return None

        return await asyncio.to_thread(_scan)

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
