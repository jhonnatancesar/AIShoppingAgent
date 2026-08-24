"""Base compartilhada pelos coletores Playwright da V1."""

import asyncio
import contextlib
import json
import random
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from playwright.async_api import Locator, Page

from app.collection.browser import BrowserSettings
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    InstallmentInterestKind,
    MarketplacePartyKind,
    RawCollectedOffer,
    RawInstallmentOption,
)
from app.collection.errors import (
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.normalization import PriceNormalizer
from app.collection.providers.edge_cdp_transport import (
    EdgeCdpTransport,
    EdgeCdpTransportError,
)
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
# TASK-109: default de fábrica do pacing entre navegações sequenciais de
# detalhe -- só usado quando o chamador não passa
# `detail_request_min/max_delay_seconds` (ex.: `Settings`, via
# `build_collection_adapter`). Ver `_pace_before_next_detail_request`.
_DEFAULT_DETAIL_REQUEST_MIN_DELAY_SECONDS = 0.6
_DEFAULT_DETAIL_REQUEST_MAX_DELAY_SECONDS = 1.6


class PlaywrightStoreProvider:
    source_code: str
    result_selector: str
    rating_detail_enabled: bool = False

    # Estratégia de espera do `page.goto`. A maioria das fontes usa
    # "domcontentloaded" (padrão). Providers cujo readiness real independe
    # desse evento (ex.: Pichau, TASK-075 correção) podem sobrescrever para
    # "commit" e implementar `empty_result_locator` para diferenciar
    # "sem resultados" de bloqueio/erro após a navegação.
    navigation_wait_until: str = "domcontentloaded"
    result_wait_uses_navigation_timeout: bool = False

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
        marketplace_party_max_candidates: int = 3,
        installment_option_max_candidates: int = 3,
        cdp_transport: EdgeCdpTransport | None = None,
        detail_request_min_delay_seconds: float = (
            _DEFAULT_DETAIL_REQUEST_MIN_DELAY_SECONDS
        ),
        detail_request_max_delay_seconds: float = (
            _DEFAULT_DETAIL_REQUEST_MAX_DELAY_SECONDS
        ),
    ) -> None:
        if max_offers <= 0:
            raise ValueError("max_offers must be positive")
        if availability_fallback_max_candidates < 0:
            raise ValueError(
                "availability_fallback_max_candidates must not be negative"
            )
        if marketplace_party_max_candidates < 0:
            raise ValueError("marketplace_party_max_candidates must not be negative")
        if installment_option_max_candidates < 0:
            raise ValueError("installment_option_max_candidates must not be negative")
        if not circuit_namespace.strip():
            raise ValueError("circuit_namespace must not be blank")
        if detail_request_min_delay_seconds < 0:
            raise ValueError("detail_request_min_delay_seconds must not be negative")
        if detail_request_max_delay_seconds < detail_request_min_delay_seconds:
            raise ValueError(
                "detail_request_max_delay_seconds must not be smaller than min"
            )
        self.settings = settings or BrowserSettings()
        self.max_offers = max_offers
        self._detail_request_min_delay_seconds = detail_request_min_delay_seconds
        self._detail_request_max_delay_seconds = detail_request_max_delay_seconds
        # TASK-109: mesmo transporte Edge/CDP usado (ou não) pela busca --
        # centralizado aqui pra que o enriquecimento de detalhe
        # (`enrich_marketplace_parties`/`enrich_installment_options`/
        # `enrich_offer_details`) reaproveite a mesma página em vez de
        # abrir Chromium gerenciado separado. Cada subclasse que aceita
        # `cdp_transport` no próprio construtor repassa pra cá.
        self._cdp_transport = cdp_transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_policy = retry_policy or RetryPolicy()
        self._availability_fallback_max_candidates = (
            availability_fallback_max_candidates
        )
        self._marketplace_party_max_candidates = marketplace_party_max_candidates
        self._installment_option_max_candidates = installment_option_max_candidates
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

    async def _collect_once(self, request: CollectionRequest) -> CollectionResult:
        """Cada loja implementa a própria busca via Edge/CDP
        (`self._cdp_transport`, TASK-109). Não há mais implementação
        genérica aqui -- o antigo caminho comum abria Chromium gerenciado
        (`BrowserSession`) como fallback; removido no fechamento da
        migração de browser (TASK-109) para que nenhum provider possa
        lançar Chromium."""
        raise NotImplementedError

    async def resolve_product_availability(self, page: Page) -> str | None:
        """Evidência de disponibilidade na página individual (fallback).

        `page` já está navegada na URL do produto. Retorna o texto canônico
        de disponibilidade (ex.: "Disponível"/"Esgotado") ou `None` quando a
        evidência continua ambígua. Providers sem fallback de página
        individual (ex.: Amazon, ainda não revisitada) mantêm o padrão.
        """
        return None

    async def resolve_marketplace_parties(
        self, page: Page
    ) -> tuple[MarketplacePartyKind, MarketplacePartyKind]:
        """Classifica vendedor e entrega numa página individual válida."""
        return (MarketplacePartyKind.UNKNOWN, MarketplacePartyKind.UNKNOWN)

    async def resolve_offer_condition(self, page: Page) -> str | None:
        """Lê condição explícita na mesma página já aberta para marketplace."""
        return None

    async def resolve_offer_availability(self, page: Page) -> str | None:
        """Lê disponibilidade durante o enriquecimento único de detalhe."""
        return None

    async def resolve_seller_name(self, page: Page) -> str | None:
        """Lê o nome exibido do Seller durante o mesmo enriquecimento."""
        return None

    async def resolve_offer_rating(self, page: Page) -> tuple[str, str] | None:
        """Lê nota+contagem estruturadas na página já aberta por outro motivo."""

        def aggregate_ratings(value: object):
            if isinstance(value, dict):
                aggregate = value.get("aggregateRating")
                if isinstance(aggregate, dict):
                    yield aggregate
                for child in value.values():
                    yield from aggregate_ratings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from aggregate_ratings(child)

        scripts = await page.locator(
            'script[type="application/ld+json"]'
        ).all_text_contents()
        for raw_json in scripts:
            try:
                document = json.loads(raw_json)
            except TypeError, ValueError:
                continue
            for aggregate in aggregate_ratings(document):
                average = aggregate.get("ratingValue")
                count = aggregate.get("reviewCount", aggregate.get("ratingCount"))
                best = aggregate.get("bestRating")
                if best is not None:
                    try:
                        if Decimal(str(best).replace(",", ".")) != Decimal(5):
                            continue
                    except Exception:
                        continue
                if average is not None and count is not None:
                    return (str(average).strip(), str(count).strip())

        average_element = page.locator('[itemprop="ratingValue"]').first
        count_element = page.locator(
            '[itemprop="reviewCount"], [itemprop="ratingCount"]'
        ).first
        if await average_element.count() and await count_element.count():
            best_element = page.locator('[itemprop="bestRating"]').first
            if await best_element.count():
                best = (
                    await best_element.get_attribute("content")
                    or await best_element.text_content()
                )
                try:
                    if best is None or Decimal(best.replace(",", ".")) != Decimal(5):
                        return None
                except Exception:
                    return None
            average = (
                await average_element.get_attribute("content")
                or await average_element.text_content()
            )
            count = (
                await count_element.get_attribute("content")
                or await count_element.text_content()
            )
            if average and count:
                return (average.strip(), count.strip())
        return None

    @asynccontextmanager
    async def _open_detail_page(self) -> AsyncIterator[Page]:
        """Página dedicada ao enriquecimento de detalhe (uma navegação por
        oferta, TASK-109): sempre via Edge/CDP. Sem `cdp_transport`
        configurado, falha explícita -- nunca abre Chromium gerenciado
        como fallback (fechamento da migração de browser, TASK-109). Os
        hooks `resolve_*` recebem a `Page` já pronta."""
        if self._cdp_transport is None:
            raise EdgeCdpTransportError(
                f"{self.source_code} CDP transport is not configured"
            )
        async with self._cdp_transport.open_blank_page() as page:
            yield page

    async def _pace_before_next_detail_request(self, position: int) -> None:
        """Sem atraso na primeira navegação; um intervalo curto e variável
        entre as seguintes (TASK-109), configurável por
        `detail_request_min/max_delay_seconds`."""
        if position > 0:
            await asyncio.sleep(
                random.uniform(
                    self._detail_request_min_delay_seconds,
                    self._detail_request_max_delay_seconds,
                )
            )

    async def enrich_marketplace_parties(
        self, offers: tuple[RawCollectedOffer, ...]
    ) -> tuple[RawCollectedOffer, ...]:
        """Visita poucos candidatos finais, em sequência e sem retry.

        NULL significa que não houve avaliação. UNKNOWN significa que a página
        respondeu, mas não ofereceu evidência inequívoca. Um bloqueio encerra o
        lote para não insistir contra proteção anti-bot.
        """
        if self._marketplace_party_max_candidates == 0:
            return offers
        if (
            type(self).resolve_marketplace_parties
            is PlaywrightStoreProvider.resolve_marketplace_parties
        ):
            return offers
        candidates = self._rank_offers(offers)[: self._marketplace_party_max_candidates]
        if not candidates:
            return offers
        resolved: dict[
            str, tuple[MarketplacePartyKind, MarketplacePartyKind, str | None]
        ] = {}
        async with self._open_detail_page() as page:
            for position, offer in enumerate(candidates):
                await self._pace_before_next_detail_request(position)
                try:
                    response = await page.goto(offer.url, wait_until="domcontentloaded")
                except Exception:
                    continue
                if response is None:
                    continue
                if response.status in _BLOCKED_STATUSES:
                    break
                if response.status == 408 or response.status >= 500:
                    continue
                try:
                    (
                        seller_kind,
                        fulfillment_kind,
                    ) = await self.resolve_marketplace_parties(page)
                except Exception:
                    seller_kind, fulfillment_kind = (
                        MarketplacePartyKind.UNKNOWN,
                        MarketplacePartyKind.UNKNOWN,
                    )
                try:
                    raw_condition = await self.resolve_offer_condition(page)
                except Exception:
                    raw_condition = None
                resolved[offer.url] = (
                    seller_kind,
                    fulfillment_kind,
                    raw_condition,
                )
        return tuple(
            replace(
                offer,
                seller_kind=(
                    offer.seller_kind
                    if resolved[offer.url][0] is MarketplacePartyKind.UNKNOWN
                    and offer.seller_kind is not None
                    else resolved[offer.url][0]
                ),
                fulfillment_kind=(
                    offer.fulfillment_kind
                    if resolved[offer.url][1] is MarketplacePartyKind.UNKNOWN
                    and offer.fulfillment_kind is not None
                    else resolved[offer.url][1]
                ),
                raw_condition=resolved[offer.url][2] or offer.raw_condition,
            )
            if offer.url in resolved
            else offer
            for offer in offers
        )

    async def resolve_installment_options(
        self, page: Page
    ) -> tuple[RawInstallmentOption, ...]:
        """TASK-089: opções de parcelamento adicionais só visíveis na
        página individual (ex.: tabela "PARCELAMENTO" da Pichau, painel
        "VER PARCELAMENTO" da Terabyte). `page` já está navegada na URL do
        produto. Providers cuja investigação real não encontrou nenhuma
        tabela equivalente (Amazon, KaBuM!) mantêm o padrão -- `()` nunca
        aciona navegação extra (ver `enrich_installment_options`)."""
        return ()

    async def enrich_installment_options(
        self, offers: tuple[RawCollectedOffer, ...]
    ) -> tuple[RawCollectedOffer, ...]:
        """Visita poucos candidatos finais para complementar as opções de
        parcelamento já capturadas no card, mesma disciplina de
        `enrich_marketplace_parties`: sequencial, sem retry, sem
        navegação alguma quando o provider não sobrescreve o hook. Uma
        opção nova com a mesma `installment_count` de uma já existente
        (capturada no card) substitui a do card -- a página individual é
        a fonte mais detalhada quando as duas existem."""
        if self._installment_option_max_candidates == 0:
            return offers
        if (
            type(self).resolve_installment_options
            is PlaywrightStoreProvider.resolve_installment_options
        ):
            return offers
        candidates = self._rank_offers(offers)[
            : self._installment_option_max_candidates
        ]
        if not candidates:
            return offers
        resolved: dict[str, tuple[RawInstallmentOption, ...]] = {}
        async with self._open_detail_page() as page:
            for position, offer in enumerate(candidates):
                await self._pace_before_next_detail_request(position)
                try:
                    response = await page.goto(offer.url, wait_until="domcontentloaded")
                except Exception:
                    continue
                if response is None:
                    continue
                if response.status in _BLOCKED_STATUSES:
                    break
                if response.status == 408 or response.status >= 500:
                    continue
                try:
                    resolved[offer.url] = await self.resolve_installment_options(page)
                except Exception:
                    continue
        return tuple(
            replace(
                offer,
                installment_options=_merge_installment_options(
                    offer.installment_options, resolved[offer.url]
                ),
            )
            if offer.url in resolved
            else offer
            for offer in offers
        )

    async def enrich_offer_details(
        self, offers: tuple[RawCollectedOffer, ...]
    ) -> tuple[RawCollectedOffer, ...]:
        """Enriquecimento extensível com no máximo uma abertura por oferta.

        Um provider novo só implementa os hooks de detalhe que suporta. O
        núcleo combina vendedor, condição, parcelamento e avaliação durante
        a mesma navegação, sem uma rodada exclusiva para cada capability.
        """
        parties_enabled = (
            type(self).resolve_marketplace_parties
            is not PlaywrightStoreProvider.resolve_marketplace_parties
            and self._marketplace_party_max_candidates > 0
        )
        installments_enabled = (
            type(self).resolve_installment_options
            is not PlaywrightStoreProvider.resolve_installment_options
            and self._installment_option_max_candidates > 0
        )
        condition_enabled = (
            type(self).resolve_offer_condition
            is not PlaywrightStoreProvider.resolve_offer_condition
            and self._marketplace_party_max_candidates > 0
        )
        availability_enabled = (
            type(self).resolve_offer_availability
            is not PlaywrightStoreProvider.resolve_offer_availability
            and self._marketplace_party_max_candidates > 0
        )
        seller_name_enabled = (
            type(self).resolve_seller_name
            is not PlaywrightStoreProvider.resolve_seller_name
            and self._marketplace_party_max_candidates > 0
        )
        if not (
            parties_enabled
            or condition_enabled
            or availability_enabled
            or seller_name_enabled
            or installments_enabled
            or self.rating_detail_enabled
        ):
            return offers
        limits = []
        if parties_enabled:
            limits.append(self._marketplace_party_max_candidates)
        if condition_enabled:
            limits.append(self._marketplace_party_max_candidates)
        if availability_enabled:
            limits.append(self._marketplace_party_max_candidates)
        if seller_name_enabled:
            limits.append(self._marketplace_party_max_candidates)
        if installments_enabled:
            limits.append(self._installment_option_max_candidates)
        if self.rating_detail_enabled:
            limits.append(max(self._marketplace_party_max_candidates, 1))
        candidates = self._rank_offers(offers)[: max(limits)]
        resolved: dict[
            str,
            tuple[
                MarketplacePartyKind | None,
                MarketplacePartyKind | None,
                str | None,
                tuple[RawInstallmentOption, ...] | None,
                tuple[str, str] | None,
                str | None,
                str | None,
            ],
        ] = {}
        async with self._open_detail_page() as page:
            for position, offer in enumerate(candidates):
                await self._pace_before_next_detail_request(position)
                try:
                    response = await page.goto(offer.url, wait_until="domcontentloaded")
                except Exception:
                    continue
                if response is None:
                    continue
                if response.status in _BLOCKED_STATUSES:
                    break
                if response.status == 408 or response.status >= 500:
                    continue
                seller_kind: MarketplacePartyKind | None = None
                fulfillment_kind: MarketplacePartyKind | None = None
                raw_condition: str | None = None
                raw_availability: str | None = None
                seller_name: str | None = None
                options: tuple[RawInstallmentOption, ...] | None = None
                if (
                    parties_enabled
                    and position < self._marketplace_party_max_candidates
                ):
                    try:
                        (
                            seller_kind,
                            fulfillment_kind,
                        ) = await self.resolve_marketplace_parties(page)
                    except Exception:
                        seller_kind = fulfillment_kind = MarketplacePartyKind.UNKNOWN
                if (
                    condition_enabled
                    and position < self._marketplace_party_max_candidates
                ):
                    try:
                        raw_condition = await self.resolve_offer_condition(page)
                    except Exception:
                        raw_condition = None
                if (
                    availability_enabled
                    and position < self._marketplace_party_max_candidates
                ):
                    try:
                        raw_availability = await self.resolve_offer_availability(page)
                    except Exception:
                        raw_availability = None
                if (
                    seller_name_enabled
                    and position < self._marketplace_party_max_candidates
                ):
                    try:
                        seller_name = await self.resolve_seller_name(page)
                    except Exception:
                        seller_name = None
                if (
                    installments_enabled
                    and position < self._installment_option_max_candidates
                ):
                    try:
                        options = await self.resolve_installment_options(page)
                    except Exception:
                        options = ()
                try:
                    rating = await self.resolve_offer_rating(page)
                except Exception:
                    rating = None
                resolved[offer.url] = (
                    seller_kind,
                    fulfillment_kind,
                    raw_condition,
                    options,
                    rating,
                    raw_availability,
                    seller_name,
                )

        def enriched(offer: RawCollectedOffer) -> RawCollectedOffer:
            if offer.url not in resolved:
                return offer
            (
                seller,
                fulfillment,
                condition,
                options,
                rating,
                availability,
                seller_name,
            ) = resolved[offer.url]
            return replace(
                offer,
                seller_kind=(
                    offer.seller_kind
                    if seller in {None, MarketplacePartyKind.UNKNOWN}
                    and offer.seller_kind is not None
                    else seller or offer.seller_kind
                ),
                fulfillment_kind=(
                    offer.fulfillment_kind
                    if fulfillment in {None, MarketplacePartyKind.UNKNOWN}
                    and offer.fulfillment_kind is not None
                    else fulfillment or offer.fulfillment_kind
                ),
                raw_condition=condition or offer.raw_condition,
                raw_availability=availability or offer.raw_availability,
                seller_name=seller_name or offer.seller_name,
                installment_options=(
                    _merge_installment_options(offer.installment_options, options)
                    if options is not None
                    else offer.installment_options
                ),
                raw_rating_average=(
                    rating[0] if rating is not None else offer.raw_rating_average
                ),
                raw_review_count=(
                    rating[1] if rating is not None else offer.raw_review_count
                ),
            )

        return tuple(enriched(offer) for offer in offers)

    def _rank_offers(
        self, offers: tuple[RawCollectedOffer, ...]
    ) -> list[RawCollectedOffer]:
        normalizer = PriceNormalizer()
        scored: list[tuple[Decimal, str, RawCollectedOffer]] = []
        for offer in offers:
            try:
                amount = normalizer.normalize_offer(offer).amount
            except CollectionNormalizationError:
                continue
            scored.append((amount, offer.external_id or offer.url, offer))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [offer for _, _, offer in scored]

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
                    seller_external_id=_optional(row.get("seller_external_id")),
                    seller_name=_optional(row.get("seller")),
                    raw_price=price,
                    raw_currency="BRL" if price and "R$" in price else None,
                    raw_shipping=_optional(row.get("shipping")),
                    raw_availability=_optional(row.get("availability")),
                    raw_fulfillment=_optional(row.get("fulfillment")),
                    raw_condition=_optional(row.get("condition")),
                    raw_rating_average=_optional(row.get("rating_average")),
                    raw_review_count=_optional(row.get("review_count")),
                    seller_kind=_party_kind(row.get("seller_kind")),
                    image_url=normalize_http_url(row.get("image")),
                    evidence={"card_text": str(row.get("evidence") or "")[:1000]},
                    installment_options=_installment_options_from_row(row),
                )
            )
        return tuple(offers)


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _party_kind(value: object) -> MarketplacePartyKind | None:
    try:
        return MarketplacePartyKind(str(value)) if value is not None else None
    except ValueError:
        return None


def _installment_options_from_row(
    row: dict[str, Any],
) -> tuple[RawInstallmentOption, ...]:
    """TASK-089: condição de parcelamento já visível no card da busca --
    schema comum aos 4 providers (`installment_count`/`installment_amount`/
    `installment_total`/`installment_interest_free`), preenchido só quando
    a própria loja mostra o dado no card. Ausência de qualquer parte
    obrigatória (contagem ou valor) nunca derruba a oferta -- só não gera
    opção nenhuma para o card. `is_highlighted=True` sempre -- esta função
    é a ÚNICA origem possível dessa flag (o card nunca expõe mais de uma
    linha de parcelamento); opções vindas da página individual
    (`resolve_installment_options`) nunca a definem."""
    count_text = _optional(row.get("installment_count"))
    amount = _optional(row.get("installment_amount"))
    if count_text is None or amount is None or not _HAS_DIGIT.search(amount):
        return ()
    try:
        count = int(count_text)
    except ValueError:
        return ()
    if count <= 0:
        return ()
    total = _optional(row.get("installment_total"))
    interest_kind = (
        InstallmentInterestKind.INTEREST_FREE
        if row.get("installment_interest_free")
        else InstallmentInterestKind.UNKNOWN
    )
    return (
        RawInstallmentOption(
            installment_count=count,
            raw_amount=amount,
            raw_total_amount=total if total and _HAS_DIGIT.search(total) else None,
            interest_kind=interest_kind,
            is_highlighted=True,
        ),
    )


def _merge_installment_options(
    card_options: tuple[RawInstallmentOption, ...],
    page_options: tuple[RawInstallmentOption, ...],
) -> tuple[RawInstallmentOption, ...]:
    """TASK-089: combina o que já veio do card com o que a página
    individual acrescentou -- nunca duplica a mesma `installment_count`.

    Mescla campo a campo, nunca substitui a opção inteira: o card às
    vezes é a ÚNICA fonte de `raw_total_amount` (ex.: Pichau) para a
    mesma quantidade que a página individual detalha com mais precisão
    (desconto/juros explícitos, ex.: a tabela "PARCELAMENTO"). Perder o
    total do card ao "vencer" com a versão da página seria destruir
    informação real sem necessidade -- cada campo usa a fonte que o tem,
    preferindo a página quando as duas o informam (mais recente/detalhada).

    `is_highlighted` nunca é reescrito aqui: quando a página confirma a
    mesma `installment_count` do card, o `replace(...)` abaixo não lista o
    campo, então o `True` original do card sobrevive intacto -- é
    exatamente a condição que a própria loja destacou. Contagens que só
    existem na página entram com o próprio valor do `page_option`, que
    nunca é `True` (só `_installment_options_from_row` produz `True`)."""
    by_count = {option.installment_count: option for option in card_options}
    for page_option in page_options:
        existing = by_count.get(page_option.installment_count)
        if existing is None:
            by_count[page_option.installment_count] = page_option
            continue
        by_count[page_option.installment_count] = replace(
            existing,
            raw_amount=page_option.raw_amount,
            raw_total_amount=(
                page_option.raw_total_amount
                if page_option.raw_total_amount is not None
                else existing.raw_total_amount
            ),
            discount_percent=(
                page_option.discount_percent
                if page_option.discount_percent is not None
                else existing.discount_percent
            ),
            interest_kind=(
                page_option.interest_kind
                if page_option.interest_kind != InstallmentInterestKind.UNKNOWN
                else existing.interest_kind
            ),
        )
    return tuple(by_count[count] for count in sorted(by_count))
