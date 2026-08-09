"""Base compartilhada pelos coletores Playwright da V1."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.collection.browser import BrowserSession, BrowserSettings
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.collection.errors import (
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.core.resilience import (
    CIRCUITS,
    CircuitOpenError,
    OperationSafety,
    RetryPolicy,
    retry_operation,
)
from app.observability.metrics import observe_resilience_event

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
        retry_policy: RetryPolicy | None = None,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
    ) -> None:
        if max_offers <= 0:
            raise ValueError("max_offers must be positive")
        self.settings = settings or BrowserSettings()
        self.max_offers = max_offers
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_policy = retry_policy or RetryPolicy()
        self._circuit = CIRCUITS.get(
            f"store:{self.source_code}:search",
            failure_threshold=circuit_failure_threshold,
            open_seconds=circuit_open_seconds,
        )

    def build_url(self, query: str) -> str:
        raise NotImplementedError

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        raise NotImplementedError

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        if request.source_code != self.source_code:
            raise ValueError(f"request source must be {self.source_code}")

        async def collect_once() -> CollectionResult:
            try:
                self._circuit.before_call()
            except CircuitOpenError:
                observe_resilience_event("store", "circuit_open")
                raise ProviderCircuitOpenError(self.source_code) from None
            try:
                result = await self._collect_once(request)
            except ProviderNavigationError:
                self._circuit.record_failure(transient=True)
                raise
            except ProviderBlockedError as error:
                self._circuit.record_failure(transient=error.status == 429)
                raise
            except Exception:
                self._circuit.record_failure(transient=False)
                raise
            self._circuit.record_success()
            return result

        return await retry_operation(
            collect_once,
            safety=OperationSafety.SAFE,
            policy=self._retry_policy,
            is_transient=lambda error: isinstance(error, ProviderNavigationError),
            on_retry=lambda: observe_resilience_event("store", "retry"),
        )

    async def _collect_once(self, request: CollectionRequest) -> CollectionResult:
        started_at = self._clock()
        async with BrowserSession(self.settings) as session:
            page = await session.new_page()
            try:
                response = await page.goto(
                    self.build_url(request.search_query), wait_until="domcontentloaded"
                )
            except PlaywrightTimeoutError, PlaywrightError:
                raise ProviderNavigationError(self.source_code, None) from None
            if response is None or response.status == 408 or response.status >= 500:
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
            if not offers:
                raise ProviderBlockedError(self.source_code, response.status)
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
