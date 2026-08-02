"""Base compartilhada pelos coletores Playwright da V1."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import Page

from app.collection.browser import BrowserSession, BrowserSettings
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.collection.errors import ProviderBlockedError, ProviderNavigationError

Clock = Callable[[], datetime]


class PlaywrightStoreProvider:
    source_code: str
    result_selector: str

    def __init__(
        self,
        settings: BrowserSettings | None = None,
        *,
        max_offers: int = 20,
        clock: Clock | None = None,
    ) -> None:
        if max_offers <= 0:
            raise ValueError("max_offers must be positive")
        self.settings = settings or BrowserSettings()
        self.max_offers = max_offers
        self._clock = clock or (lambda: datetime.now(UTC))

    def build_url(self, query: str) -> str:
        raise NotImplementedError

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        raise NotImplementedError

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        if request.source_code != self.source_code:
            raise ValueError(f"request source must be {self.source_code}")
        started_at = self._clock()
        async with BrowserSession(self.settings) as session:
            page = await session.new_page()
            response = await page.goto(
                self.build_url(request.search_query), wait_until="domcontentloaded"
            )
            if response is None or response.status >= 500:
                raise ProviderNavigationError(
                    self.source_code, response.status if response else None
                )
            if response.status in {401, 403, 429}:
                raise ProviderBlockedError(self.source_code, response.status)
            try:
                await page.locator(self.result_selector).first.wait_for(
                    state="attached"
                )
            except Exception as error:
                raise ProviderBlockedError(self.source_code, response.status) from error
            offers = await self.extract(page, self._clock())
        return CollectionResult(self.source_code, started_at, self._clock(), offers)

    def offers_from_rows(
        self, rows: list[dict[str, Any]], collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        offers = []
        for row in rows[: self.max_offers]:
            title, url = (
                str(row.get("title") or "").strip(),
                str(row.get("url") or "").strip(),
            )
            if not title or not url:
                continue
            price = _optional(row.get("price"))
            offers.append(
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=url,
                    title=title,
                    collected_at=collected_at,
                    external_id=_optional(row.get("external_id")),
                    seller_name=_optional(row.get("seller")),
                    raw_price=price,
                    raw_currency="BRL" if price and "R$" in price else None,
                    raw_shipping=_optional(row.get("shipping")),
                    raw_availability=_optional(row.get("availability")),
                    raw_fulfillment=_optional(row.get("fulfillment")),
                    evidence={"card_text": str(row.get("evidence") or "")[:1000]},
                )
            )
        return tuple(offers)


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
