"""Falhas de navegação e cancelamento devem sempre liberar página/lease.

O runtime CDP é controlado; complementa a prova real Terabyte e evita
sessões órfãs e extração de páginas bloqueadas em qualquer loja.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.collection.providers import edge_cdp_transport as module
from app.collection.providers.edge_cdp_supervisor import EdgeCdpSupervisorError
from playwright.async_api import Error as PlaywrightError


@pytest.fixture
def runtime(monkeypatch):
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        close=AsyncMock(),
        set_default_timeout=Mock(),
        locator=Mock(),
    )
    wait = AsyncMock()
    page.locator.return_value.first.wait_for = wait
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(contexts=[context])
    driver = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser)),
        stop=AsyncMock(),
    )
    monkeypatch.setattr(
        module,
        "async_playwright",
        lambda: SimpleNamespace(start=AsyncMock(return_value=driver)),
    )
    return page, wait, driver, browser


@pytest.mark.parametrize("status", [None, 401, 403, 408, 429, 500, 503])
def test_blocked_or_failed_navigation_never_extracts_and_releases_page(runtime, status):
    page, _, driver, _ = runtime
    page.goto.return_value = None if status is None else SimpleNamespace(status=status)
    extract = AsyncMock()

    async def run():
        transport = module.EdgeCdpTransport("http://127.0.0.1:9333")
        with pytest.raises(module.EdgeCdpTransportError, match="navigation failed"):
            await transport.run(
                "https://www.terabyteshop.com.br/produto/123",
                readiness_selector=".product",
                extract=extract,
            )

    asyncio.run(run())
    extract.assert_not_awaited()
    page.close.assert_awaited_once()
    driver.stop.assert_awaited_once()


@pytest.mark.parametrize("phase", ["connect", "navigation", "readiness"])
@pytest.mark.parametrize("cancel", [False, True])
def test_transport_failure_and_cancellation_release_resources(runtime, phase, cancel):
    page, wait, driver, _ = runtime
    error = asyncio.CancelledError() if cancel else PlaywrightError("failure")
    target = {
        "connect": driver.chromium.connect_over_cdp,
        "navigation": page.goto,
        "readiness": wait,
    }[phase]
    target.side_effect = error

    async def run():
        transport = module.EdgeCdpTransport("http://127.0.0.1:9333")
        expected = asyncio.CancelledError if cancel else module.EdgeCdpTransportError
        with pytest.raises(expected):
            await transport.run(
                "https://www.terabyteshop.com.br/",
                readiness_selector=".product",
                extract=AsyncMock(),
            )

    asyncio.run(run())
    assert page.close.await_count == (0 if phase == "connect" else 1)
    driver.stop.assert_awaited_once()


def test_missing_browser_context_and_failed_page_close_are_safe(runtime):
    page, _, driver, browser = runtime

    async def run():
        transport = module.EdgeCdpTransport("http://localhost:9333")
        browser.contexts = []
        with pytest.raises(module.EdgeCdpTransportError, match="no context"):
            async with transport.open_blank_page():
                pytest.fail("Não deve devolver página sem contexto")
        browser.contexts = [SimpleNamespace(new_page=AsyncMock(return_value=page))]
        page.close.side_effect = PlaywrightError("already closed")
        async with transport.open_blank_page() as opened:
            assert opened is page

    asyncio.run(run())
    assert driver.stop.await_count == 2


def test_supervisor_lease_is_released_after_success_and_failure(runtime):
    page, _, driver, _ = runtime
    events = []

    class Supervisor:
        @asynccontextmanager
        async def lease(self):
            events.append("acquired")
            try:
                yield
            finally:
                events.append("released")

    async def run():
        transport = module.EdgeCdpTransport(
            "http://127.0.0.1:9333", supervisor=Supervisor()
        )
        extract = AsyncMock(return_value=("oferta",))
        assert await transport.run(
            "https://www.terabyteshop.com.br/",
            readiness_selector=".product",
            extract=extract,
        ) == ("oferta",)
        extract.assert_awaited_once_with(page)
        page.goto.side_effect = PlaywrightError("timeout")
        with pytest.raises(module.EdgeCdpTransportError):
            async with transport.open_page("https://www.terabyteshop.com.br/"):
                pytest.fail("Falha não pode produzir página")

    asyncio.run(run())
    assert events == ["acquired", "released", "acquired", "released"]
    assert driver.stop.await_count == 2


def test_supervisor_failure_never_starts_browser(runtime):
    _, _, driver, _ = runtime

    class Supervisor:
        @asynccontextmanager
        async def lease(self):
            raise EdgeCdpSupervisorError("unavailable")
            yield

    async def run():
        transport = module.EdgeCdpTransport(
            "http://127.0.0.1:9333", supervisor=Supervisor()
        )
        with pytest.raises(module.EdgeCdpTransportError):
            async with transport.open_blank_page():
                pytest.fail("Supervisor indisponível")

    asyncio.run(run())
    driver.chromium.connect_over_cdp.assert_not_awaited()
