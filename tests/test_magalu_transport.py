"""Timeouts e falha rápida do único transporte operacional da Magalu."""

import asyncio
from types import SimpleNamespace

import pytest
from app.collection.providers.magalu_transport import (
    CdpMagaluSearchTransport,
    MagaluSearchTransportError,
    UnavailableMagaluSearchTransport,
)


def test_unconfigured_magalu_transport_fails_fast() -> None:
    with pytest.raises(MagaluSearchTransportError, match="not configured"):
        asyncio.run(UnavailableMagaluSearchTransport().fetch_html("https://example"))


def test_cdp_transport_applies_separate_timeouts_and_caps_html_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, int] = {}

    class Locator:
        @property
        def first(self):
            return self

        async def wait_for(self, *, state: str, timeout: int) -> None:
            assert state == "attached"
            observed["document"] = timeout

    class Page:
        async def goto(self, url: str, *, wait_until: str, timeout: int):
            assert wait_until == "domcontentloaded"
            observed["navigation"] = timeout
            return SimpleNamespace(status=200)

        def locator(self, selector: str) -> Locator:
            assert selector == "script#__NEXT_DATA__"
            return Locator()

        async def content(self) -> str:
            await asyncio.sleep(60)
            return ""

        async def close(self) -> None:
            return None

    class Context:
        async def new_page(self) -> Page:
            return Page()

    class Chromium:
        async def connect_over_cdp(self, endpoint: str, *, timeout: int):
            assert endpoint == "http://127.0.0.1:9223"
            observed["connect"] = timeout
            return SimpleNamespace(contexts=[Context()])

    class Playwright:
        chromium = Chromium()

        async def stop(self) -> None:
            return None

    class Starter:
        async def start(self) -> Playwright:
            return Playwright()

    monkeypatch.setattr(
        "app.collection.providers.magalu_transport.async_playwright", Starter
    )
    transport = CdpMagaluSearchTransport(
        "http://127.0.0.1:9223",
        connect_timeout_ms=101,
        navigation_timeout_ms=202,
        document_timeout_ms=303,
        html_timeout_ms=10,
    )

    with pytest.raises(MagaluSearchTransportError, match="CDP transport failed"):
        asyncio.run(transport.fetch_html("https://www.magazineluiza.com.br/busca/x/"))

    assert observed == {"connect": 101, "navigation": 202, "document": 303}
