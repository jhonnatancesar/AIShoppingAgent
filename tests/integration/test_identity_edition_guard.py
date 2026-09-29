"""TASK-131 -- guarda de palavras de edição contra Postgres real: um título com
"MAX" nunca é fundido no cadastro aprovado da mesma placa SEM "MAX" (achado do
backfill de PROD, 2026-09-28), mas o título sem "MAX" continua reaproveitando
o cadastro sem nenhuma chamada de IA."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.products.identity import build_resolved_variant_from_fields
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import resolve_or_learn_product_variant
from app.users.models import UserRole

pytestmark = pytest.mark.integration

_MAX_TITLE = (
    "MSI Placa-mãe MAG X870E Tomahawk MAX WiFi, ATX - Suporta processadores "
    "AMD Ryzen 9000/8000/7000, AM5, DDR5, Wi-Fi 7"
)
_PLAIN_TITLE = "Placa Mãe MSI MAG X870E TOMAHAWK WIFI, AMD, Socket AM5, ATX, DDR5"


class _AI:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


def _approve_plain_tomahawk(integration_database) -> str:
    resolved = build_resolved_variant_from_fields(
        category="motherboard",
        brand="msi",
        family="mag-tomahawk",
        model="x870e",
        variant=None,
        attributes={},
    )
    with integration_database.sessions.begin() as session:
        session.add(
            ProductIdentityCandidate(
                id=uuid4(),
                raw_title="Placa Mae Msi X870E Tomahawk (cadastro aprovado)",
                normalized_title_hash=uuid4().hex,
                category=resolved.category,
                brand=resolved.brand,
                family=resolved.family,
                model=resolved.model,
                variant=resolved.variant,
                attributes={},
                family_key=resolved.family_key,
                identity_key=resolved.identity_key,
                status="approved",
                grounded=True,
            )
        )
    return resolved.identity_key


def _resolve(integration_database, title: str, ai: _AI):
    async def _run():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=ai, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    return asyncio.run(_run())


def test_max_title_goes_to_the_ai_instead_of_the_plain_candidate(
    integration_database,
) -> None:
    plain_key = _approve_plain_tomahawk(integration_database)
    ai = _AI(
        json.dumps(
            {
                "category": "motherboard",
                "brand": "MSI",
                "family": "MAG Tomahawk MAX",
                "model": "X870E",
                "variant": None,
                "store_sku": None,
                "manufacturer_part_number": None,
                "attributes": {},
            }
        )
    )

    resolved = _resolve(integration_database, _MAX_TITLE, ai)

    assert ai.calls == 1, "título com MAX não pode reaproveitar o cadastro sem MAX"
    assert resolved.family == "mag-tomahawk-max"
    assert resolved.identity_key != plain_key


def test_title_without_max_still_reuses_the_plain_candidate_for_free(
    integration_database,
) -> None:
    plain_key = _approve_plain_tomahawk(integration_database)
    ai = _AI("{}")

    resolved = _resolve(integration_database, _PLAIN_TITLE, ai)

    assert ai.calls == 0, "sem palavra de edição desconhecida, reuso sem IA"
    assert resolved.identity_key == plain_key
