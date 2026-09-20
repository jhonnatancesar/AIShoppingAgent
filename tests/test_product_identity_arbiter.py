"""Árbitro de IA para a zona cinzenta de identidade de produto --
parsing estrito de veredito e disciplina fail-closed (checkpoint 3,
2026-09-13), sem banco."""

import asyncio
from datetime import UTC, datetime

from app.ai_provider import AIResponse
from app.products.identity_arbiter import (
    ArbiterVerdict,
    ListingEvidence,
    arbitrate_same_product,
)
from app.users.models import UserRole

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


class _StaticArbiterAIManager:
    """Fronteira de IA controlada -- devolve sempre o MESMO conteúdo,
    registra o `profile` de cada chamada recebida (para provar
    `cost_policy=free_only` via `UserRole.USER`, ver
    `test_arbitrate_same_product_always_forces_user_profile`)."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0
        self.received_profiles: list[UserRole] = []

    async def generate(self, request):
        self.calls += 1
        self.received_profiles.append(request.profile)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-arbiter-model",
            content=self._content,
            finished_at=NOW,
        )


class _RaisingAIManager:
    async def generate(self, request):
        raise RuntimeError("provider indisponível")


def _listing(**overrides) -> ListingEvidence:
    defaults = dict(
        manufacturer="ASUS",
        family="TUF Gaming",
        model_name="B650M-E",
        variant=None,
        store_sku=None,
        manufacturer_part_number=None,
        attributes={},
    )
    defaults.update(overrides)
    return ListingEvidence(**defaults)


def test_arbitrate_same_product_parses_same_product_verdict() -> None:
    manager = _StaticArbiterAIManager('{"verdict": "SAME_PRODUCT"}')
    verdict = asyncio.run(
        arbitrate_same_product(
            manager,
            listing_a=_listing(store_sku="TUF-GAMING-B650M-E-WIFI"),
            listing_b=_listing(manufacturer_part_number="90MB1FV0-M0EAY0"),
            requested_at=NOW,
        )
    )
    assert verdict is ArbiterVerdict.SAME_PRODUCT
    assert manager.calls == 1


def test_arbitrate_same_product_parses_different_product_verdict() -> None:
    manager = _StaticArbiterAIManager('{"verdict": "DIFFERENT_PRODUCT"}')
    verdict = asyncio.run(
        arbitrate_same_product(
            manager, listing_a=_listing(), listing_b=_listing(model_name="B650E-E")
        )
    )
    assert verdict is ArbiterVerdict.DIFFERENT_PRODUCT


def test_arbitrate_same_product_parses_inconclusive_verdict() -> None:
    manager = _StaticArbiterAIManager('{"verdict": "INCONCLUSIVE"}')
    verdict = asyncio.run(
        arbitrate_same_product(manager, listing_a=_listing(), listing_b=_listing())
    )
    assert verdict is ArbiterVerdict.INCONCLUSIVE


def test_arbitrate_same_product_tolerates_markdown_code_fence() -> None:
    manager = _StaticArbiterAIManager('```json\n{"verdict": "SAME_PRODUCT"}\n```')
    verdict = asyncio.run(
        arbitrate_same_product(manager, listing_a=_listing(), listing_b=_listing())
    )
    assert verdict is ArbiterVerdict.SAME_PRODUCT


def test_arbitrate_same_product_is_inconclusive_on_invalid_json() -> None:
    manager = _StaticArbiterAIManager("isto não é JSON")
    verdict = asyncio.run(
        arbitrate_same_product(manager, listing_a=_listing(), listing_b=_listing())
    )
    assert verdict is ArbiterVerdict.INCONCLUSIVE


def test_arbitrate_same_product_is_inconclusive_on_unexpected_shape() -> None:
    manager = _StaticArbiterAIManager('{"verdict": "SAME_PRODUCT", "reason": "x"}')
    verdict = asyncio.run(
        arbitrate_same_product(manager, listing_a=_listing(), listing_b=_listing())
    )
    assert verdict is ArbiterVerdict.INCONCLUSIVE


def test_arbitrate_same_product_is_inconclusive_on_unknown_verdict_value() -> None:
    manager = _StaticArbiterAIManager('{"verdict": "MAYBE"}')
    verdict = asyncio.run(
        arbitrate_same_product(manager, listing_a=_listing(), listing_b=_listing())
    )
    assert verdict is ArbiterVerdict.INCONCLUSIVE


def test_arbitrate_same_product_is_inconclusive_on_provider_failure() -> None:
    verdict = asyncio.run(
        arbitrate_same_product(
            _RaisingAIManager(), listing_a=_listing(), listing_b=_listing()
        )
    )
    assert verdict is ArbiterVerdict.INCONCLUSIVE


def test_arbitrate_same_product_always_forces_user_profile() -> None:
    """cost_policy=free_only (checkpoint 3, seção 11) -- o árbitro NUNCA
    deve permitir fallback pago, independente de quem chamou a função
    ter um profile ADMIN/DEV em outro contexto; a chamada à IA aqui
    sempre usa `UserRole.USER`."""
    manager = _StaticArbiterAIManager('{"verdict": "SAME_PRODUCT"}')
    asyncio.run(
        arbitrate_same_product(manager, listing_a=_listing(), listing_b=_listing())
    )
    assert manager.received_profiles == [UserRole.USER]
