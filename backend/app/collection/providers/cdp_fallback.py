"""Fallback CDP genérico e restrito a loopback para providers Playwright."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.collection.providers.magalu_transport import validate_loopback_cdp_endpoint

ResultT = TypeVar("ResultT")


class CdpFallbackError(RuntimeError):
    """Falha única no transporte CDP, sem retry próprio."""


class CdpPageFallback:
    """Executa a extração existente numa página Edge/CDP já supervisionada.

    Genérico o bastante para dois papéis diferentes: último recurso do
    Mercado Livre (só depois do Playwright falhar) e transporte primário
    -- e único -- da Terabyte (TASK-105, Playwright comprovadamente
    bloqueado pelo Cloudflare, sem fallback de volta a ele)."""

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

    async def run(
        self,
        url: str,
        *,
        readiness_selector: str,
        extract: Callable[[Page], Awaitable[ResultT]],
    ) -> ResultT:
        playwright = None
        page = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(
                self.endpoint, timeout=self._connect_timeout_ms
            )
            if not browser.contexts:
                raise CdpFallbackError("CDP browser has no context")
            page = await browser.contexts[0].new_page()
            page.set_default_timeout(self._document_timeout_ms)
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._navigation_timeout_ms,
            )
            if response is None or response.status in {401, 403, 408, 429}:
                raise CdpFallbackError("CDP fallback navigation failed")
            if response.status >= 500:
                raise CdpFallbackError("CDP fallback navigation failed")
            await page.locator(readiness_selector).first.wait_for(
                state="attached", timeout=self._document_timeout_ms
            )
            return await extract(page)
        except asyncio.CancelledError:
            raise
        except CdpFallbackError:
            raise
        except (PlaywrightError, PlaywrightTimeoutError) as error:
            raise CdpFallbackError("CDP fallback failed") from error
        finally:
            if page is not None:
                try:
                    await page.close()
                except PlaywrightError:
                    pass
            if playwright is not None:
                await playwright.stop()
