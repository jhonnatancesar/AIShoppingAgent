"""Testes da infraestrutura Playwright, incluindo o Edge real da máquina
(TASK-109, fechamento -- nunca Chromium)."""

import asyncio

import pytest
from app.collection import BrowserSession, BrowserSettings


def test_browser_settings_reject_invalid_values() -> None:
    with pytest.raises(ValueError):
        BrowserSettings(action_timeout_ms=0)
    with pytest.raises(ValueError):
        BrowserSettings(navigation_timeout_ms=0)
    with pytest.raises(ValueError):
        BrowserSettings(locale=" ")


def test_session_requires_an_active_context() -> None:
    session = BrowserSession()

    with pytest.raises(RuntimeError):
        asyncio.run(session.new_page())


def test_edge_opens_local_page_and_closes_session() -> None:
    async def exercise_browser() -> BrowserSession:
        session = BrowserSession(
            BrowserSettings(
                action_timeout_ms=5_000,
                navigation_timeout_ms=5_000,
            )
        )
        async with session:
            page = await session.new_page()
            await page.set_content("<title>AIShoppingAgent</title><h1>coleta</h1>")
            assert await page.title() == "AIShoppingAgent"
            assert await page.locator("h1").text_content() == "coleta"
            with pytest.raises(RuntimeError):
                await session.__aenter__()
        return session

    closed_session = asyncio.run(exercise_browser())

    with pytest.raises(RuntimeError):
        asyncio.run(closed_session.new_page())


def test_session_propagates_and_cleans_up_when_cdp_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-109 (fechamento, parte 3): `BrowserSession` só conecta via
    CDP -- uma falha de `connect_over_cdp` (não mais de `launch`) precisa
    propagar e deixar a sessão limpa, igual antes."""
    import app.collection.providers.edge_cdp_transport as transport_module

    class FailingChromium:
        async def connect_over_cdp(self, endpoint: str, *, timeout: int) -> None:
            assert endpoint
            raise OSError("cdp connect failed")

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FailingChromium()
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    class FakeManager:
        def __init__(self, playwright: FakePlaywright) -> None:
            self.playwright = playwright

        async def start(self) -> FakePlaywright:
            return self.playwright

    fake_playwright = FakePlaywright()
    monkeypatch.setattr(
        transport_module,
        "async_playwright",
        lambda: FakeManager(fake_playwright),
    )
    session = BrowserSession()

    with pytest.raises(OSError, match="cdp connect failed"):
        asyncio.run(session.__aenter__())

    assert fake_playwright.stopped is True
    with pytest.raises(RuntimeError):
        asyncio.run(session.new_page())
