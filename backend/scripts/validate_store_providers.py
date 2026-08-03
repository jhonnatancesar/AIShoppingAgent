"""Validação manual dos providers contra as origens públicas reais."""

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from app.collection import (
    AmazonProvider,
    BrowserSettings,
    CollectionRequest,
    KabumProvider,
    PichauProvider,
    PriceNormalizer,
    TerabyteProvider,
)

PROVIDERS = {
    "amazon": AmazonProvider,
    "kabum": KabumProvider,
    "pichau": PichauProvider,
    "terabyte": TerabyteProvider,
}

HEADED_SOURCES = {"pichau", "terabyte"}


def should_use_headed(
    source: str, *, force_headed: bool = False, force_headless: bool = False
) -> bool:
    if force_headed and force_headless:
        raise ValueError("headed and headless modes are mutually exclusive")
    return force_headed or (source in HEADED_SOURCES and not force_headless)


async def validate(source: str, query: str, headed: bool) -> None:
    provider = PROVIDERS[source](
        BrowserSettings(headless=not headed, navigation_timeout_ms=60_000),
        max_offers=3,
    )
    request = CollectionRequest(uuid4(), source, query, datetime.now(UTC))
    result = await provider.collect(request)
    normalized = PriceNormalizer().normalize_result(result)
    if not normalized.offers:
        raise RuntimeError(f"{source} returned no offers")
    print(f"{source}: {len(normalized.offers)} oferta(s)")
    for offer in normalized.offers:
        print(
            f"- {offer.raw_offer.external_id}: {offer.raw_offer.title[:80]} | "
            f"item={offer.amount} shipping={offer.shipping_amount} "
            f"total={offer.total_amount} {offer.currency}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=tuple(PROVIDERS))
    parser.add_argument("--query", default="RTX 4060")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headed", action="store_true")
    mode.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    headed = should_use_headed(
        args.source, force_headed=args.headed, force_headless=args.headless
    )
    asyncio.run(validate(args.source, args.query, headed))


if __name__ == "__main__":
    main()
