import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.collection import (
    CollectionAdapter,
    CollectionContractError,
    CollectionRequest,
    CollectionResult,
    DuplicateProviderError,
    RawCollectedOffer,
    UnsupportedSourceError,
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


class FakeProvider:
    source_code = "kabum"

    def __init__(self, result: CollectionResult) -> None:
        self.result = result
        self.received: CollectionRequest | None = None

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.received = request
        return self.result


def request(source_code: str = "kabum") -> CollectionRequest:
    return CollectionRequest(uuid4(), source_code, "RTX 5070", NOW)


def result(source_code: str = "kabum") -> CollectionResult:
    offer = RawCollectedOffer(
        source_code=source_code,
        url="https://example.test/product",
        title="GPU",
        collected_at=NOW + timedelta(seconds=1),
        external_id="sku-1",
        seller_external_id="seller-1",
        seller_name="Loja Parceira",
        raw_price="R$ 4.999,90",
        raw_currency="BRL",
        raw_shipping="R$ 20,00",
        raw_availability="Em estoque",
        raw_fulfillment="Entregue pelo marketplace",
        evidence={"selector": ".price"},
    )
    return CollectionResult(
        source_code,
        NOW,
        NOW + timedelta(seconds=2),
        (offer,),
    )


def test_dispatches_to_registered_provider_and_preserves_raw_data() -> None:
    provider = FakeProvider(result())
    adapter = CollectionAdapter([provider])
    collection_request = request()

    collected = asyncio.run(adapter.collect(collection_request))

    assert provider.received == collection_request
    assert collected.offers[0].raw_price == "R$ 4.999,90"
    assert collected.offers[0].seller_name == "Loja Parceira"
    assert adapter.supported_sources == ("kabum",)


def test_rejects_duplicate_provider() -> None:
    provider = FakeProvider(result())
    with pytest.raises(DuplicateProviderError):
        CollectionAdapter([provider, provider])


def test_rejects_invalid_provider_code() -> None:
    provider = FakeProvider(result())
    provider.source_code = " Kabum"
    with pytest.raises(CollectionContractError):
        CollectionAdapter([provider])


def test_rejects_unsupported_source() -> None:
    with pytest.raises(UnsupportedSourceError):
        asyncio.run(CollectionAdapter().collect(request("amazon")))


def test_rejects_result_from_another_source() -> None:
    provider = FakeProvider(result("amazon"))
    with pytest.raises(CollectionContractError):
        asyncio.run(CollectionAdapter([provider]).collect(request()))


def test_rejects_result_started_before_request() -> None:
    early_result = CollectionResult(
        "kabum",
        NOW - timedelta(seconds=1),
        NOW,
    )
    with pytest.raises(CollectionContractError):
        asyncio.run(CollectionAdapter([FakeProvider(early_result)]).collect(request()))


def test_collects_only_selected_sources_and_rejects_duplicates() -> None:
    kabum = FakeProvider(result())
    amazon = FakeProvider(result("amazon"))
    amazon.source_code = "amazon"
    adapter = CollectionAdapter([kabum, amazon])

    results = asyncio.run(
        adapter.collect_selected((request("amazon"), request("kabum")))
    )

    assert tuple(item.source_code for item in results) == ("amazon", "kabum")
    with pytest.raises(CollectionContractError):
        asyncio.run(adapter.collect_selected((request(), request())))


def test_contract_rejects_blank_and_naive_values() -> None:
    with pytest.raises(CollectionContractError):
        request("")
    with pytest.raises(CollectionContractError):
        CollectionRequest(uuid4(), "kabum", "GPU", datetime(2026, 8, 2))


def test_result_rejects_inconsistent_timeline_and_offers() -> None:
    with pytest.raises(CollectionContractError):
        CollectionResult("kabum", NOW, NOW - timedelta(seconds=1))
    with pytest.raises(CollectionContractError):
        CollectionResult("kabum", NOW, NOW, result("amazon").offers)


# ---------------------------------------------------------------------------
# TASK-089 (DEC-069): enrich_installment_options.
# ---------------------------------------------------------------------------


def test_enrich_installment_options_passes_through_when_provider_lacks_hook() -> None:
    """Amazon/KaBuM! -- nenhuma extensão implementada, offers voltam
    exatamente como vieram, sem navegação alguma."""
    provider = FakeProvider(result())
    adapter = CollectionAdapter([provider])
    offers = result().offers

    enriched = asyncio.run(adapter.enrich_installment_options("kabum", offers))

    assert enriched == offers


def test_enrich_installment_options_delegates_to_provider_extension() -> None:
    offers = result().offers

    class EnrichingProvider(FakeProvider):
        async def enrich_installment_options(self, offers):
            self.enrich_called_with = offers
            return offers

    provider = EnrichingProvider(result())
    adapter = CollectionAdapter([provider])

    enriched = asyncio.run(adapter.enrich_installment_options("kabum", offers))

    assert provider.enrich_called_with == offers
    assert enriched == offers


def test_enrich_installment_options_rejects_count_mismatch() -> None:
    offers = result().offers

    class BrokenProvider(FakeProvider):
        async def enrich_installment_options(self, offers):
            return ()

    provider = BrokenProvider(result())
    adapter = CollectionAdapter([provider])

    with pytest.raises(CollectionContractError):
        asyncio.run(adapter.enrich_installment_options("kabum", offers))


def test_enrich_installment_options_rejects_unsupported_source() -> None:
    with pytest.raises(UnsupportedSourceError):
        asyncio.run(CollectionAdapter().enrich_installment_options("amazon", ()))
