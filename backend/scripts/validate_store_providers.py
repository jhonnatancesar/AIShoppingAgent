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
    MagaluProvider,
    MercadoLivreProvider,
    PichauProvider,
    PriceNormalizer,
    TerabyteProvider,
)
from app.collection.providers.edge_cdp_transport import EdgeCdpTransport
from app.collection.providers.magalu_transport import build_magalu_search_transport

PROVIDERS = {
    "amazon": AmazonProvider,
    "kabum": KabumProvider,
    "magalu": MagaluProvider,
    "mercadolivre": MercadoLivreProvider,
    "pichau": PichauProvider,
    "terabyte": TerabyteProvider,
}

HEADED_SOURCES = {"pichau", "terabyte", "magalu", "mercadolivre"}


def should_use_headed(
    source: str, *, force_headed: bool = False, force_headless: bool = False
) -> bool:
    if force_headed and force_headless:
        raise ValueError("headed and headless modes are mutually exclusive")
    return force_headed or (source in HEADED_SOURCES and not force_headless)


async def validate(
    source: str,
    query: str,
    headed: bool,
    *,
    show_evidence: bool = False,
    edge_cdp_url: str | None = None,
) -> None:
    provider_kwargs = {}
    if source == "magalu":
        provider_kwargs["search_transport"] = build_magalu_search_transport(
            cdp_endpoint=edge_cdp_url,
            connect_timeout_ms=5_000,
            navigation_timeout_ms=20_000,
            document_timeout_ms=10_000,
            html_timeout_ms=3_000,
        )
    if source == "mercadolivre" and edge_cdp_url:
        provider_kwargs["edge_fallback"] = EdgeCdpTransport(
            edge_cdp_url,
            connect_timeout_ms=5_000,
            navigation_timeout_ms=20_000,
            document_timeout_ms=10_000,
        )
    if source == "terabyte" and edge_cdp_url:
        # TASK-105: Playwright gerenciado está comprovadamente bloqueado
        # (DEC-070) -- sem `edge_cdp_url`, a validação manual falha rápido,
        # igual ao worker real.
        provider_kwargs["cdp_transport"] = EdgeCdpTransport(
            edge_cdp_url,
            connect_timeout_ms=5_000,
            navigation_timeout_ms=20_000,
            document_timeout_ms=10_000,
        )
    provider = PROVIDERS[source](
        BrowserSettings(headless=not headed, navigation_timeout_ms=60_000),
        max_offers=3,
        **provider_kwargs,
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
            f"total={offer.total_amount} {offer.currency} "
            f"availability={offer.availability.value}"
        )
        if show_evidence:
            card_text = str(offer.raw_offer.evidence.get("card_text", ""))
            print("  evidence=" + " ".join(card_text.split())[:1000])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=tuple(PROVIDERS))
    parser.add_argument("--query", default="RTX 4060")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headed", action="store_true")
    mode.add_argument("--headless", action="store_true")
    parser.add_argument("--show-evidence", action="store_true")
    parser.add_argument(
        "--edge-cdp-url",
        help="Endpoint CDP HTTP loopback de um Edge normal já iniciado "
        "(Magalu, Mercado Livre e Terabyte).",
    )
    args = parser.parse_args()
    headed = should_use_headed(
        args.source, force_headed=args.headed, force_headless=args.headless
    )
    asyncio.run(
        validate(
            args.source,
            args.query,
            headed,
            show_evidence=args.show_evidence,
            edge_cdp_url=args.edge_cdp_url,
        )
    )


if __name__ == "__main__":
    main()
