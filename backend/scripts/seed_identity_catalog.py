"""Popula o catálogo de nomenclaturas (TASK-132, Parte B).

Duas fontes, sempre idempotente (rodar de novo não duplica nada):

1. **Pré-lista** (`app/products/identity_catalog_seed.py`): produtos que mais
   saem, de várias categorias, com nome exato e, no caso do celular, capacidade
   obrigatória.
2. **Identidades já aprovadas**: cada candidato `approved` de
   `product_identity_candidates` que traz part number vira (ou reforça) uma
   entrada `learned`. O mesmo part number apontando para DUAS identidades
   diferentes não é catalogado: sai na lista de conflitos para revisão.

`--dry-run` mostra os números sem gravar; `--apply` grava. Nenhuma chamada de
IA.

Uso:
    python -m scripts.seed_identity_catalog --dry-run
    python -m scripts.seed_identity_catalog --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

from app.core.config import get_settings
from app.database.session import (
    create_async_database_engine,
    create_async_session_factory,
)
from app.products.identity import ResolvedProductVariant
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_catalog import (
    compact_code,
    is_usable_part_number,
    learn_catalog_entry,
    upsert_seed_entry,
)
from app.products.identity_catalog_seed import SEED_ENTRIES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _resolved(candidate: ProductIdentityCandidate) -> ResolvedProductVariant:
    return ResolvedProductVariant(
        category=candidate.category,
        brand=candidate.brand,
        family=candidate.family,
        model=candidate.model,
        variant=candidate.variant,
        attributes=tuple(sorted(candidate.attributes.items())),
        family_key=candidate.family_key,
        identity_key=candidate.identity_key,
        label="",
    )


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    created = 0
    codes = 0
    for seed in SEED_ENTRIES:
        new_entry, new_codes = await upsert_seed_entry(session, seed)
        created += 1 if new_entry else 0
        codes += new_codes
    return len(SEED_ENTRIES), created, codes


async def _learn_from_candidates(
    session: AsyncSession,
) -> tuple[int, int, list[tuple[str, list[str]]]]:
    rows = (
        await session.scalars(
            select(ProductIdentityCandidate).where(
                ProductIdentityCandidate.status == "approved",
                ProductIdentityCandidate.manufacturer_part_number.is_not(None),
                ProductIdentityCandidate.identity_key.is_not(None),
            )
        )
    ).all()
    by_code: dict[str, dict[str, ProductIdentityCandidate]] = defaultdict(dict)
    for row in rows:
        compact = compact_code(row.manufacturer_part_number or "")
        if is_usable_part_number(compact):
            by_code[compact].setdefault(row.identity_key, row)
    learned = 0
    conflicts: list[tuple[str, list[str]]] = []
    for compact, identities in sorted(by_code.items()):
        if len(identities) > 1:
            conflicts.append(
                (
                    compact,
                    sorted(
                        f"{c.brand}/{c.family}/{c.model}/{c.variant}"
                        for c in identities.values()
                    ),
                )
            )
            continue
        candidate = next(iter(identities.values()))
        if await learn_catalog_entry(
            session, _resolved(candidate), candidate.manufacturer_part_number
        ):
            learned += 1
    return len(by_code), learned, conflicts


async def run(*, apply: bool) -> None:
    settings = get_settings()
    async_driver = "asyncpg" if sys.platform == "win32" else "psycopg"
    engine = create_async_database_engine(settings, async_driver=async_driver)
    session_factory = create_async_session_factory(engine)
    try:
        async with session_factory() as session:
            total, created, codes = await _seed(session)
            print(
                f"Pré-lista: {total} entradas ({created} novas, {codes} nomes novos)."
            )
            pn_total, learned, conflicts = await _learn_from_candidates(session)
            print(
                f"Identidades aprovadas com part number: {pn_total} códigos "
                f"({learned} entraram no catálogo, {len(conflicts)} em conflito)."
            )
            for compact, identities in conflicts:
                print(f"  CONFLITO {compact}: " + " | ".join(identities))
            if apply:
                await session.commit()
                print("Aplicado.")
            else:
                await session.rollback()
                print("Dry-run -- nada foi persistido.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
