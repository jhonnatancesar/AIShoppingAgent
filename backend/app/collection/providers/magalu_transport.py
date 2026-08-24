"""Transportes substituíveis para obter o HTML público da busca Magalu."""

import asyncio
from contextlib import AsyncExitStack
from typing import Protocol

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.collection.providers.edge_cdp_endpoint import validate_loopback_cdp_endpoint
from app.collection.providers.edge_cdp_supervisor import (
    EdgeCdpSupervisor,
    EdgeCdpSupervisorError,
)


class MagaluSearchTransportError(RuntimeError):
    """Falha isolada ao adquirir o documento da busca Magalu."""


class MagaluSearchTransport(Protocol):
    """Porta de aquisição; parser, domínio e ranking não conhecem o runtime."""

    async def fetch_html(self, url: str) -> str: ...


class UnavailableMagaluSearchTransport:
    """Falha rápida quando o único transporte operacional não foi configurado."""

    async def fetch_html(self, url: str) -> str:
        del url
        raise MagaluSearchTransportError("magalu CDP transport is not configured")


class CdpMagaluSearchTransport:
    """Lê o documento final de um Edge normal já exposto em loopback via CDP."""

    def __init__(
        self,
        endpoint: str,
        *,
        connect_timeout_ms: int,
        navigation_timeout_ms: int,
        document_timeout_ms: int,
        html_timeout_ms: int,
        supervisor: EdgeCdpSupervisor | None = None,
    ) -> None:
        self.endpoint = validate_loopback_cdp_endpoint(endpoint)
        if (
            min(
                connect_timeout_ms,
                navigation_timeout_ms,
                document_timeout_ms,
                html_timeout_ms,
            )
            <= 0
        ):
            raise ValueError("CDP timeouts must be positive")
        self._connect_timeout_ms = connect_timeout_ms
        self._navigation_timeout_ms = navigation_timeout_ms
        self._document_timeout_ms = document_timeout_ms
        self._html_timeout_seconds = html_timeout_ms / 1000
        # TASK-109: mesma lease automática do EdgeCdpTransport -- a Magalu
        # também é só mais um consumidor do Edge compartilhado.
        self._supervisor = supervisor

    async def fetch_html(self, url: str) -> str:
        async with AsyncExitStack() as stack:
            if self._supervisor is not None:
                try:
                    await stack.enter_async_context(self._supervisor.lease())
                except EdgeCdpSupervisorError as error:
                    raise MagaluSearchTransportError(
                        "magalu CDP transport failed"
                    ) from error
            playwright = None
            page = None
            try:
                playwright = await async_playwright().start()
                browser = await playwright.chromium.connect_over_cdp(
                    self.endpoint, timeout=self._connect_timeout_ms
                )
                if not browser.contexts:
                    raise MagaluSearchTransportError("CDP browser has no context")
                page = await browser.contexts[0].new_page()
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._navigation_timeout_ms,
                )
                if (
                    response is None
                    or response.status == 408
                    or response.status >= 500
                ):
                    raise MagaluSearchTransportError("CDP navigation failed")
                if response.status in {401, 403, 429}:
                    raise MagaluSearchTransportError(
                        f"CDP navigation returned {response.status}"
                    )
                await page.locator("script#__NEXT_DATA__").first.wait_for(
                    state="attached", timeout=self._document_timeout_ms
                )
                html = await asyncio.wait_for(
                    page.content(), timeout=self._html_timeout_seconds
                )
                if "__NEXT_DATA__" not in html:
                    raise MagaluSearchTransportError("Magalu SSR document is missing")
                return html
            except asyncio.CancelledError:
                raise
            except MagaluSearchTransportError:
                raise
            except (TimeoutError, PlaywrightError, PlaywrightTimeoutError) as error:
                raise MagaluSearchTransportError(
                    "magalu CDP transport failed"
                ) from error
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except PlaywrightError:
                        pass
                if playwright is not None:
                    await playwright.stop()
        raise AssertionError("unreachable")  # pragma: no cover


def build_magalu_search_transport(
    *,
    cdp_endpoint: str | None,
    connect_timeout_ms: int,
    navigation_timeout_ms: int,
    document_timeout_ms: int,
    html_timeout_ms: int,
    supervisor: EdgeCdpSupervisor | None = None,
) -> MagaluSearchTransport:
    """Seleciona CDP ou falha rápida; não há transporte alternativo nesta versão."""
    if cdp_endpoint is None:
        return UnavailableMagaluSearchTransport()
    return CdpMagaluSearchTransport(
        cdp_endpoint,
        connect_timeout_ms=connect_timeout_ms,
        navigation_timeout_ms=navigation_timeout_ms,
        document_timeout_ms=document_timeout_ms,
        html_timeout_ms=html_timeout_ms,
        supervisor=supervisor,
    )
