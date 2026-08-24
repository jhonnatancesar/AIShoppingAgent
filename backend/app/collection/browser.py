"""Infraestrutura de navegador só para teste (TASK-109, fechamento).

Nenhum Store Provider real usa mais isto -- a coleta de produção navega
exclusivamente via `EdgeCdpTransport`/`connect_over_cdp()` contra o Edge
supervisionado (`app/collection/providers/edge_cdp_supervisor.py`). O que
resta aqui existe só para dar a testes locais um `Page` real, sem rede
(`page.set_content(html)`), para exercitar parsing de HTML -- por isso
`BrowserSession` também lança o Microsoft Edge já instalado na máquina
(`discover_edge_executable`, mesma descoberta usada pelo supervisor),
nunca um Chromium baixado pelo Playwright: zero binário de Chromium em
todo o projeto, produção e teste apontam para o mesmo navegador real.
"""

from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from app.collection.edge_discovery import discover_edge_executable


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    """Opções seguras e independentes de uma fonte específica."""

    headless: bool = True
    action_timeout_ms: int = 10_000
    navigation_timeout_ms: int = 30_000
    locale: str = "pt-BR"

    def __post_init__(self) -> None:
        if self.action_timeout_ms <= 0:
            raise ValueError("action_timeout_ms must be positive")
        if self.navigation_timeout_ms <= 0:
            raise ValueError("navigation_timeout_ms must be positive")
        if not self.locale.strip():
            raise ValueError("locale must not be blank")


class BrowserSession:
    """Gerencia Playwright, o Edge instalado na máquina e um contexto
    isolado -- só para teste (ver docstring do módulo)."""

    def __init__(self, settings: BrowserSettings | None = None) -> None:
        self.settings = settings or BrowserSettings()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> BrowserSession:
        if self._playwright is not None:
            raise RuntimeError("browser session is already open")

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=self.settings.headless,
                executable_path=str(discover_edge_executable()),
            )
            self._context = await self._browser.new_context(
                accept_downloads=False,
                locale=self.settings.locale,
            )
            self._context.set_default_timeout(self.settings.action_timeout_ms)
            self._context.set_default_navigation_timeout(
                self.settings.navigation_timeout_ms
            )
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def new_page(self) -> Page:
        """Cria uma página no contexto isolado da sessão ativa."""
        if self._context is None:
            raise RuntimeError("browser session is not open")
        return await self._context.new_page()

    async def close(self) -> None:
        """Encerra todos os recursos, inclusive após inicialização parcial."""
        context, self._context = self._context, None
        browser, self._browser = self._browser, None
        playwright, self._playwright = self._playwright, None

        try:
            if context is not None:
                await context.close()
        finally:
            try:
                if browser is not None:
                    await browser.close()
            finally:
                if playwright is not None:
                    await playwright.stop()
