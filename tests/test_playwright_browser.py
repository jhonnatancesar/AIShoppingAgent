"""Testes da infraestrutura Playwright, incluindo Chromium real."""

import asyncio

import app.collection.browser as browser_module
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


def test_chromium_opens_local_page_and_closes_session() -> None:
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


def test_session_stops_playwright_when_browser_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingChromium:
        async def launch(self, *, headless: bool) -> None:
            assert headless is True
            raise OSError("browser launch failed")

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
        browser_module,
        "async_playwright",
        lambda: FakeManager(fake_playwright),
    )
    session = BrowserSession()

    with pytest.raises(OSError, match="browser launch failed"):
        asyncio.run(session.__aenter__())

    assert fake_playwright.stopped is True
    with pytest.raises(RuntimeError):
        asyncio.run(session.new_page())
