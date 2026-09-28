"""TASK-129 -- vocabulário da extração por IA contra Postgres real: o que
está no banco (grafias aprovadas, categorias genéricas em uso, aliases
ATIVOS) chega à IA e trava a grafia gravada."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import resolve_or_learn_product_variant
from app.products.identity_vocabulary import load_identity_vocabulary
from app.products.models import ProductIdentityAlias
from app.users.models import UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _extraction(**fields) -> str:
    base = {
        "category": "",
        "brand": "",
        "family": "",
        "model": "",
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": None,
        "attributes": {},
    }
    base.update(fields)
    return json.dumps(base)


class _AI:
    def __init__(self, content: str) -> None:
        self.content = content
        self.system_prompts: list[str] = []

    async def generate(self, request):
        self.system_prompts.append(request.messages[0].content)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


def _seed(integration_database) -> None:
    with integration_database.sessions.begin() as session:
        session.add_all(
            [
                ProductIdentityCandidate(
                    id=uuid4(),
                    raw_title="Placa-Mãe ASUS TUF Gaming B550M-Plus",
                    normalized_title_hash=uuid4().hex,
                    category="motherboard",
                    brand="asus",
                    family="tuf-gaming",
                    model="b550m-plus",
                    variant="base",
                    family_key="asus-tuf-gaming-b550m-plus",  # gitleaks:allow -- slug de teste
                    identity_key="asus-tuf-gaming-b550m-plus-base",  # gitleaks:allow -- slug de teste
                    status="approved",
                    grounded=True,
                ),
                ProductIdentityCandidate(
                    id=uuid4(),
                    raw_title="Cadeira Gamer Preta",
                    normalized_title_hash=uuid4().hex,
                    category="cadeira-gamer",
                    status="partial",
                    grounded=False,
                ),
                ProductIdentityAlias(
                    category="ram",
                    attribute_name="brand",
                    raw_value_normalized="beast",
                    canonical_value="nunca-aplicar",
                    status="candidate",
                ),
            ]
        )


def test_vocabulary_is_read_from_the_database(integration_database) -> None:
    _seed(integration_database)

    async def _load():
        async with integration_database.async_sessions() as session:
            return await load_identity_vocabulary(session)

    vocab = asyncio.run(_load())

    assert vocab.generic_categories == ("cadeira-gamer",)
    assert vocab.known_families == (("motherboard", "asus", "tuf-gaming"),)
    # fury -> kingston vem da lista inicial da migration 20260927_0001
    assert ("ram", "brand", "fury", "kingston") in vocab.aliases
    assert ("*", "brand", "rog", "asus") in vocab.aliases
    assert not any(raw == "beast" for _, _, raw, _ in vocab.aliases), (
        "alias só sugerido (candidate) nunca vale"
    )


def test_resolution_sends_the_vocabulary_and_stores_the_official_spelling(
    integration_database,
) -> None:
    """A IA respondeu com sinônimo ("Memória RAM") e com a linha no lugar
    do fabricante ("Fury") -- o banco guarda "ram" e "kingston"."""
    _seed(integration_database)
    title = "Memória Fury Beast 8GB DDR4 3200MHz KF432C16BB/8"
    ai = _AI(
        _extraction(
            category="Memória RAM",
            brand="Fury",
            family="Fury Beast",
            model="KF432C16BB/8",
        )
    )

    async def _resolve():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=ai, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    resolved = asyncio.run(_resolve())

    assert "VOCABULÁRIO OBRIGATÓRIO" in ai.system_prompts[0]
    assert "- motherboard | asus | tuf-gaming" in ai.system_prompts[0]
    assert "- ram brand: fury -> kingston" in ai.system_prompts[0]
    assert (resolved.category, resolved.brand) == ("ram", "kingston")
    with integration_database.sessions() as session:
        stored = session.scalar(
            select(ProductIdentityCandidate).where(
                ProductIdentityCandidate.raw_title == title
            )
        )
    assert (stored.status, stored.category, stored.brand) == (
        "approved",
        "ram",
        "kingston",
    )
