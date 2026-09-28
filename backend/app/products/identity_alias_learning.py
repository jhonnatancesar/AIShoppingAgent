"""TASK-129 -- a tabela de grafias (`product_identity_aliases`) aprende
sozinha, só com prova; nada aqui chama IA.

- PROVA (vira `active` na hora): dois anúncios com o MESMO part number do
  fabricante e grafias diferentes de marca/família ("fury"/"beast" x
  "kingston"/"fury-beast") -- é o mesmo produto, então a grafia nova passa
  a apontar para a já aprovada, e o anúncio novo herda a identidade dela.
- SUSPEITA (vira `candidate`, para revisão em
  `scripts/review_identity_aliases.py`): a marca nova é o começo de uma
  família de OUTRA marca da mesma categoria ("fury" x kingston/"fury-beast")
  -- parece a linha no lugar do fabricante, mas só uma pessoa confirma.

Sugestão recusada fica `rejected` e nunca é sugerida de novo (o INSERT
ignora grafia já registrada, em qualquer status)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.products.identity import ResolvedProductVariant
from app.products.identity_ai import values_match_ignoring_punctuation
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_vocabulary import category_slug
from app.products.models import ProductIdentityAlias

logger = logging.getLogger(__name__)


async def find_same_part_number_candidate(
    session: AsyncSession,
    resolved: ResolvedProductVariant,
    manufacturer_part_number: str | None,
) -> ProductIdentityCandidate | None:
    """Candidato já APROVADO da mesma categoria, com o mesmo part number
    (ignorando pontuação) e outra identidade -- `None` quando não há."""
    if not manufacturer_part_number or not manufacturer_part_number.strip():
        return None
    rows = (
        await session.scalars(
            select(ProductIdentityCandidate)
            .where(
                ProductIdentityCandidate.status == "approved",
                ProductIdentityCandidate.category == resolved.category,
                ProductIdentityCandidate.manufacturer_part_number.is_not(None),
                ProductIdentityCandidate.identity_key != resolved.identity_key,
            )
            .order_by(ProductIdentityCandidate.created_at)
        )
    ).all()
    return next(
        (
            row
            for row in rows
            if values_match_ignoring_punctuation(
                row.manufacturer_part_number, manufacturer_part_number
            )
        ),
        None,
    )


async def record_part_number_aliases(
    session: AsyncSession,
    *,
    resolved: ResolvedProductVariant,
    approved_brand: str,
    approved_family: str,
) -> None:
    """Grafias de marca/família do anúncio novo passam a apontar para as do
    candidato aprovado de mesmo part number. Sugestão pendente (`candidate`)
    dessa grafia é promovida; recusa humana (`rejected`) nunca é desfeita."""
    for attribute, raw, canonical in (
        ("brand", resolved.brand, approved_brand),
        ("family", resolved.family, approved_family),
    ):
        raw_slug = category_slug(raw or "")
        canonical_slug = category_slug(canonical or "")
        if not raw_slug or not canonical_slug or raw_slug == canonical_slug:
            continue
        statement = insert(ProductIdentityAlias).values(
            category=resolved.category,
            attribute_name=attribute,
            raw_value_normalized=raw_slug,
            canonical_value=canonical_slug,
            status="active",
        )
        statement = statement.on_conflict_do_update(
            index_elements=["category", "attribute_name", "raw_value_normalized"],
            set_={"status": "active", "canonical_value": canonical_slug},
            where=ProductIdentityAlias.status == "candidate",
        )
        await _best_effort(session, statement, attribute=attribute, raw=raw_slug)
        logger.info(
            "product_identity_alias_learned_from_part_number",
            extra={
                "category": resolved.category,
                "attribute_name": attribute,
                "raw_value": raw_slug,
                "canonical_value": canonical_slug,
            },
        )


def _is_line_of(brand: str, family: str) -> bool:
    return bool(brand) and (family == brand or family.startswith(f"{brand}-"))


async def suggest_brand_aliases(
    session: AsyncSession, resolved: ResolvedProductVariant
) -> None:
    """Marca que é o começo da família de outra marca da mesma categoria
    vira sugestão (`candidate`), nos dois sentidos: a marca nova é a linha
    ("fury" x kingston/"fury-beast"), ou a marca antiga era a linha e agora
    veio o fabricante ("fury"/"beast" já aprovado x kingston/"fury-beast")."""
    brand, family = resolved.brand, resolved.family
    rows = (
        await session.execute(
            select(ProductIdentityCandidate.brand, ProductIdentityCandidate.family)
            .where(
                ProductIdentityCandidate.status == "approved",
                ProductIdentityCandidate.category == resolved.category,
                ProductIdentityCandidate.brand.is_not(None),
                ProductIdentityCandidate.brand != brand,
            )
            .distinct()
        )
    ).all()
    suggestions: set[tuple[str, str]] = set()
    for other_brand, other_family in rows:
        if _is_line_of(brand, other_family or ""):
            suggestions.add((brand, other_brand))
        if _is_line_of(other_brand, family):
            suggestions.add((other_brand, brand))
    for raw, canonical in sorted(suggestions):
        statement = (
            insert(ProductIdentityAlias)
            .values(
                category=resolved.category,
                attribute_name="brand",
                raw_value_normalized=raw,
                canonical_value=canonical,
                status="candidate",
            )
            .on_conflict_do_nothing(
                index_elements=["category", "attribute_name", "raw_value_normalized"]
            )
        )
        await _best_effort(session, statement, attribute="brand", raw=raw)


async def _best_effort(
    session: AsyncSession, statement, *, attribute: str, raw: str
) -> None:
    """Aprender grafia nunca derruba a resolução do produto: a falha é
    registrada em log e a resolução segue."""
    try:
        async with session.begin_nested():
            await session.execute(statement)
    except SQLAlchemyError:
        logger.warning(
            "product_identity_alias_learning_failed",
            extra={"attribute_name": attribute, "raw_value": raw},
            exc_info=True,
        )
