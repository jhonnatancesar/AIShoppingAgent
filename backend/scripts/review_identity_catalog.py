"""Revisão do catálogo de nomenclaturas (TASK-132, Parte B).

O catálogo é consultado ANTES do reuso por palavras e da IA, então uma entrada
errada vale como verdade até alguém recusar. Este script lista e recusa:

- `--list`: entradas ativas (`--all` inclui as recusadas), com origem e códigos;
- `--reject ID`: a entrada deixa de valer, e os códigos dela ficam guardados
  para o mesmo código nunca ser reaprendido;
- `--activate ID`: desfaz uma recusa.

ID aceita o começo do UUID (8 primeiros caracteres da listagem), desde que só
uma linha comece assim. Nenhuma chamada de IA.

Uso:
    python -m scripts.review_identity_catalog --list
    python -m scripts.review_identity_catalog --list --all
    python -m scripts.review_identity_catalog --reject 1a2b3c4d
    python -m scripts.review_identity_catalog --activate 1a2b3c4d
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.database.session import (
    create_async_database_engine,
    create_async_session_factory,
)
from app.products.identity_catalog_models import (
    ProductIdentityCatalogCode,
    ProductIdentityCatalogEntry,
)
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession


async def _list(session: AsyncSession, *, include_all: bool) -> None:
    query = select(ProductIdentityCatalogEntry).order_by(
        ProductIdentityCatalogEntry.category,
        ProductIdentityCatalogEntry.brand,
        ProductIdentityCatalogEntry.family,
        ProductIdentityCatalogEntry.model,
    )
    if not include_all:
        query = query.where(ProductIdentityCatalogEntry.status == "active")
    entries = (await session.scalars(query)).all()
    if not entries:
        print("Catálogo vazio.")
        return
    for entry in entries:
        codes = (
            await session.scalars(
                select(ProductIdentityCatalogCode.value_normalized)
                .where(ProductIdentityCatalogCode.entry_id == entry.id)
                .order_by(ProductIdentityCatalogCode.kind)
            )
        ).all()
        print(
            f"{str(entry.id)[:8]}  [{entry.status}/{entry.source}]  {entry.category} "
            f"{entry.brand}/{entry.family}/{entry.model}/{entry.variant}  "
            f"códigos: {', '.join(codes) or '-'}"
        )


async def _decide(session: AsyncSession, prefix: str, *, status: str) -> bool:
    cleaned = prefix.strip().lower()
    if not cleaned:
        print("ID vazio.")
        return False
    rows = (
        await session.scalars(
            select(ProductIdentityCatalogEntry).where(
                cast(ProductIdentityCatalogEntry.id, String).like(f"{cleaned}%")
            )
        )
    ).all()
    if len(rows) != 1:
        print(
            "Nenhuma entrada com esse ID."
            if not rows
            else f"{len(rows)} entradas começam com esse ID -- use mais caracteres."
        )
        return False
    entry = rows[0]
    if entry.status == status:
        print(f"Já está {status}: {entry.brand}/{entry.family}/{entry.model}")
        return False
    entry.status = status
    await session.commit()
    print(f"{status}: {entry.brand}/{entry.family}/{entry.model}/{entry.variant}")
    return True


async def run(
    *,
    list_all: bool = False,
    reject: str | None = None,
    activate: str | None = None,
) -> bool:
    settings = get_settings()
    async_driver = "asyncpg" if sys.platform == "win32" else "psycopg"
    engine = create_async_database_engine(settings, async_driver=async_driver)
    session_factory = create_async_session_factory(engine)
    try:
        async with session_factory() as session:
            if reject is not None:
                return await _decide(session, reject, status="rejected")
            if activate is not None:
                return await _decide(session, activate, status="active")
            await _list(session, include_all=list_all)
            return True
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Entradas do catálogo.")
    group.add_argument("--reject", metavar="ID", help="Recusa uma entrada.")
    group.add_argument("--activate", metavar="ID", help="Desfaz uma recusa.")
    parser.add_argument(
        "--all", action="store_true", help="Com --list: inclui as recusadas."
    )
    args = parser.parse_args()
    if args.all and not args.list:
        parser.error("--all só vale com --list")
    ok = asyncio.run(run(list_all=args.all, reject=args.reject, activate=args.activate))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
