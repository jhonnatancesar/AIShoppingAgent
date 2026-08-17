"""Base compartilhada pelos coletores Playwright da V1."""

import asyncio
import contextlib
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.collection.browser import BrowserSession, BrowserSettings
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.collection.errors import (
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.normalization import PriceNormalizer
from app.core.resilience import (
    CIRCUITS,
    CircuitOpenError,
    OperationSafety,
    RetryPolicy,
    retry_operation,
)
from app.core.urls import normalize_http_url
from app.observability.metrics import observe_resilience_event

Clock = Callable[[], datetime]
_HAS_DIGIT = re.compile(r"\d")
_BLOCKED_STATUSES = frozenset({401, 403, 429})


class PlaywrightStoreProvider:
    source_code: str
    result_selector: str

    # Estratégia de espera do `page.goto`. A maioria das fontes usa
    # "domcontentloaded" (padrão). Providers cujo readiness real independe
    # desse evento (ex.: Pichau, TASK-075 correção) podem sobrescrever para
    # "commit" e implementar `empty_result_locator` para diferenciar
    # "sem resultados" de bloqueio/erro após a navegação.
    navigation_wait_until: str = "domcontentloaded"

    def __init__(
        self,
        settings: BrowserSettings | None = None,
        *,
        max_offers: int = 20,
        clock: Clock | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
        circuit_namespace: str = "search",
        availability_fallback_max_candidates: int = 3,
    ) -> None:
        if max_offers <= 0:
            raise ValueError("max_offers must be positive")
        if availability_fallback_max_candidates < 0:
            raise ValueError(
                "availability_fallback_max_candidates must not be negative"
            )
        if not circuit_namespace.strip():
            raise ValueError("circuit_namespace must not be blank")
        self.settings = settings or BrowserSettings()
        self.max_offers = max_offers
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_policy = retry_policy or RetryPolicy()
        self._availability_fallback_max_candidates = (
            availability_fallback_max_candidates
        )
        # TASK-083: `circuit_namespace` default ("search") preserva
        # exatamente a chave já usada pela coleta normal -- só um consumidor
        # que precisa de isolamento (ex.: resolução de identidade, que usa
        # instâncias dedicadas com timeout/volume bem menores) passa um
        # namespace diferente, para que falhas de um propósito nunca abram
        # o circuito do outro propósito na mesma fonte.
        self._circuit = CIRCUITS.get(
            f"store:{self.source_code}:{circuit_namespace}",
            failure_threshold=circuit_failure_threshold,
            open_seconds=circuit_open_seconds,
        )

    def build_url(self, query: str) -> str:
        raise NotImplementedError

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        raise NotImplementedError

    async def resolve_product_availability(self, page: Page) -> str | None:
        """Evidência de disponibilidade na página individual (fallback).

        `page` já está navegada na URL do produto. Retorna o texto canônico
        de disponibilidade (ex.: "Disponível"/"Esgotado") ou `None` quando a
        evidência continua ambígua. Providers sem fallback de página
        individual (ex.: Amazon, ainda não revisitada) mantêm o padrão.
        """
        return None

    def empty_result_locator(self, page: Page) -> Locator | None:
        """Locator do estado legítimo de "busca sem resultados", se houver.

        Retorna `None` por padrão: o provider continua dependendo apenas de
        `result_selector` após a navegação, como hoje. Só sobrescrever
        quando o provider precisa distinguir "zero resultados" (coleta
        válida, vazia) de bloqueio/erro (ex.: seletor nunca aparece).
        """
        return None

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
                    self.build_url(request.search_query),
                    wait_until=self.navigation_wait_until,
                )
            except PlaywrightTimeoutError, PlaywrightError:
                raise ProviderNavigationError(self.source_code, None) from None
            if response is None or response.status == 408 or response.status >= 500:
                raise ProviderNavigationError(
                    self.source_code, response.status if response else None
                )
            if response.status in _BLOCKED_STATUSES:
                raise ProviderBlockedError(self.source_code, response.status)

            empty_locator = self.empty_result_locator(page)
            if empty_locator is None:
                try:
                    await page.locator(self.result_selector).first.wait_for(
                        state="attached"
                    )
                except Exception as error:
                    raise ProviderBlockedError(
                        self.source_code, response.status
                    ) from error
            else:
                try:
                    readiness = await self._wait_for_results_or_empty(
                        page, empty_locator
                    )
                except Exception as error:
                    raise ProviderBlockedError(
                        self.source_code, response.status
                    ) from error
                if readiness == "empty":
                    return CollectionResult(
                        self.source_code, started_at, self._clock(), ()
                    )

            offers = await self.extract(page, self._clock())
            if not offers:
                raise ProviderBlockedError(self.source_code, response.status)
            offers = await self._resolve_unknown_availability(page, offers)
        return CollectionResult(self.source_code, started_at, self._clock(), offers)

    async def _wait_for_results_or_empty(
        self, page: Page, empty_locator: Locator
    ) -> str:
        """Aguarda o primeiro entre resultados reais e o estado vazio legítimo.

        Timeout compartilhado com a navegação (`navigation_timeout_ms`): esta
        espera substitui, para providers com `empty_result_locator`, o que
        antes era coberto implicitamente por `domcontentloaded` demorado.
        Retorna "results" ou "empty"; propaga a exceção original se nenhum
        dos dois estados aparecer dentro do timeout.
        """
        timeout_ms = self.settings.navigation_timeout_ms
        results_wait = asyncio.ensure_future(
            page.locator(self.result_selector).first.wait_for(
                state="attached", timeout=timeout_ms
            )
        )
        empty_wait = asyncio.ensure_future(
            empty_locator.first.wait_for(state="visible", timeout=timeout_ms)
        )
        try:
            done, pending = await asyncio.wait(
                {results_wait, empty_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await task
            if results_wait in done and results_wait.exception() is None:
                return "results"
            if empty_wait in done and empty_wait.exception() is None:
                return "empty"
            raise (
                results_wait.exception()
                if results_wait in done
                else empty_wait.exception()
            )
        finally:
            for task in (results_wait, empty_wait):
                if not task.done():
                    task.cancel()

    async def _resolve_unknown_availability(
        self, page: Page, offers: tuple[RawCollectedOffer, ...]
    ) -> tuple[RawCollectedOffer, ...]:
        """Fallback seletivo: só abre página individual dos UNKNOWN mais baratos.

        AVAILABLE/UNAVAILABLE já resolvidos no card nunca chegam aqui
        (`raw_availability` já preenchido). Candidatos sem preço válido não
        entram no ranking; falha isolada (timeout, navegação) em um
        candidato nunca derruba os seguintes nem o resultado já obtido no
        card. Um 401/403/429 é tratado como sinal de bloqueio/challenge da
        própria fonte: encerra o fallback do ciclo inteiro sem tentar mais
        candidatos, para não insistir contra uma proteção anti-bot ativa.
        """
        if self._availability_fallback_max_candidates == 0:
            return offers
        if (
            type(self).resolve_product_availability
            is PlaywrightStoreProvider.resolve_product_availability
        ):
            # Provider sem fallback de página individual implementado
            # (ex.: Amazon, ainda não revisitada): nenhuma navegação extra.
            return offers
        candidates = self._rank_unknown_candidates(offers)
        candidates = candidates[: self._availability_fallback_max_candidates]
        if not candidates:
            return offers
        resolved: dict[str, str] = {}
        for offer in candidates:
            try:
                response = await page.goto(offer.url, wait_until="domcontentloaded")
            except Exception:
                continue
            if response is not None and response.status in _BLOCKED_STATUSES:
                break
            try:
                evidence = await self.resolve_product_availability(page)
            except Exception:
                evidence = None
            if evidence:
                resolved[offer.url] = evidence
        if not resolved:
            return offers
        return tuple(
            replace(offer, raw_availability=resolved[offer.url])
            if offer.url in resolved
            else offer
            for offer in offers
        )

    def _rank_unknown_candidates(
        self, offers: tuple[RawCollectedOffer, ...]
    ) -> list[RawCollectedOffer]:
        normalizer = PriceNormalizer()
        scored: list[tuple[Decimal, RawCollectedOffer]] = []
        for offer in offers:
            if offer.raw_availability is not None:
                continue
            try:
                amount = normalizer.normalize_offer(offer).amount
            except CollectionNormalizationError:
                continue
            scored.append((amount, offer))
        scored.sort(key=lambda pair: pair[0])
        return [offer for _, offer in scored]

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
            if price is None or not _HAS_DIGIT.search(price):
                # Sem preço numérico no card (ausente, ou texto como
                # "Indisponível" no lugar do valor) o produto não vira
                # RawCollectedOffer: normalizar preço inválido derrubaria o
                # lote inteiro da fonte, não só esta oferta, e a V1 nunca
                # fabrica preço. Sem preço determinável, não é candidato
                # desta execução (mesmo princípio da Kabum: ausência de
                # oferta normal na busca não é candidato).
                continue
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
                    image_url=normalize_http_url(row.get("image")),
                    evidence={"card_text": str(row.get("evidence") or "")[:1000]},
                )
            )
        return tuple(offers)


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
