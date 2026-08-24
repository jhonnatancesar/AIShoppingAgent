"""Transporte CDP genérico e restrito a loopback, reutilizável por qualquer provider."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.collection.providers.edge_cdp_endpoint import validate_loopback_cdp_endpoint

ResultT = TypeVar("ResultT")


class EdgeCdpTransportError(RuntimeError):
    """Falha única no transporte CDP, sem retry próprio."""


class EdgeCdpTransport:
    """Conecta e navega numa página Edge/CDP já supervisionada.

    Genérico o bastante para qualquer papel de qualquer provider -- rede
    de segurança do Mercado Livre/Amazon/Kabum (só depois do Playwright
    falhar ou quando não configurado) e transporte primário -- e único --
    da Terabyte (TASK-105, Playwright comprovadamente bloqueado pelo
    Cloudflare, sem fallback de volta a ele). Não conhece nenhuma loja:
    não decide o que é "resultado", "busca vazia" ou "bloqueio" -- quem
    decide isso é sempre o provider chamador, reaproveitando a mesma
    lógica que já usa com o Playwright gerenciado."""

    def __init__(
        self,
        endpoint: str,
        *,
        connect_timeout_ms: int = 5_000,
        navigation_timeout_ms: int = 20_000,
        document_timeout_ms: int = 10_000,
    ) -> None:
        self.endpoint = validate_loopback_cdp_endpoint(endpoint)
        if min(
            connect_timeout_ms, navigation_timeout_ms, document_timeout_ms
        ) <= 0:
            raise ValueError("CDP fallback timeouts must be positive")
        self._connect_timeout_ms = connect_timeout_ms
        self._navigation_timeout_ms = navigation_timeout_ms
        self._document_timeout_ms = document_timeout_ms

    @asynccontextmanager
    async def open_blank_page(self) -> AsyncIterator[Page]:
        """Conecta via CDP e devolve uma página nova, sem navegar --
        para quem precisa fazer suas próprias navegações sequenciais
        (ex.: enriquecimento de detalhe, uma por oferta, TASK-109).
        Nenhuma decisão de negócio acontece aqui, só conexão/limpeza."""
        playwright = None
        page = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(
                self.endpoint, timeout=self._connect_timeout_ms
            )
            if not browser.contexts:
                raise EdgeCdpTransportError("CDP browser has no context")
            page = await browser.contexts[0].new_page()
            page.set_default_timeout(self._document_timeout_ms)
            yield page
        except asyncio.CancelledError:
            raise
        except EdgeCdpTransportError:
            raise
        except (PlaywrightError, PlaywrightTimeoutError) as error:
            raise EdgeCdpTransportError("CDP fallback failed") from error
        finally:
            if page is not None:
                try:
                    await page.close()
                except PlaywrightError:
                    pass
            if playwright is not None:
                await playwright.stop()

    @asynccontextmanager
    async def open_page(self, url: str) -> AsyncIterator[Page]:
        """Conecta via CDP e navega até `url`; devolve a `Page` já
        carregada para o provider aplicar sua própria estratégia de
        espera/extração (TASK-109) -- equivalente a abrir uma página com
        o Playwright gerenciado, só a origem da página muda. Nenhuma
        decisão de "resultado"/"vazio"/"bloqueio" acontece aqui; só o
        status HTTP da navegação em si é validado (o mesmo checado por
        `run()`), porque isso é uma falha de transporte, não de negócio.
        """
        async with self.open_blank_page() as page:
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._navigation_timeout_ms,
                )
            except asyncio.CancelledError:
                raise
            except (PlaywrightError, PlaywrightTimeoutError) as error:
                raise EdgeCdpTransportError("CDP fallback failed") from error
            if response is None or response.status in {401, 403, 408, 429}:
                raise EdgeCdpTransportError("CDP fallback navigation failed")
            if response.status >= 500:
                raise EdgeCdpTransportError("CDP fallback navigation failed")
            yield page

    async def run(
        self,
        url: str,
        *,
        readiness_selector: str,
        extract: Callable[[Page], Awaitable[ResultT]],
    ) -> ResultT:
        """Atalho para o caso comum: espera um único seletor de resultado
        aparecer e extrai. Providers com distinção real entre "vazio" e
        "bloqueado" (ex.: Pichau) usam `open_page` diretamente."""
        async with self.open_page(url) as page:
            try:
                await page.locator(readiness_selector).first.wait_for(
                    state="attached", timeout=self._document_timeout_ms
                )
            except asyncio.CancelledError:
                raise
            except EdgeCdpTransportError:
                raise
            except (PlaywrightError, PlaywrightTimeoutError) as error:
                raise EdgeCdpTransportError("CDP fallback failed") from error
            return await extract(page)
