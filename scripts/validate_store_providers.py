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
    TerabyteProvider,
)

PROVIDERS = {
    "amazon": AmazonProvider,
    "kabum": KabumProvider,
    "pichau": PichauProvider,
    "terabyte": TerabyteProvider,
}


async def validate(source: str, query: str, headed: bool) -> None:
    provider = PROVIDERS[source](
        BrowserSettings(headless=not headed, navigation_timeout_ms=60_000),
        max_offers=3,
    )
    request = CollectionRequest(uuid4(), source, query, datetime.now(UTC))
    result = await provider.collect(request)
    print(f"{source}: {len(result.offers)} oferta(s)")
    for offer in result.offers:
        print(f"- {offer.external_id}: {offer.title[:80]} | {offer.raw_price}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=tuple(PROVIDERS))
    parser.add_argument("--query", default="RTX 4060")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    asyncio.run(validate(args.source, args.query, args.headed))


if __name__ == "__main__":
    main()
