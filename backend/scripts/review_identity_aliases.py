"""Revisão das grafias sugeridas para a identidade de produto (TASK-129).

A extração por IA usa as grafias ATIVAS de `product_identity_aliases`
(linha de produto -> fabricante, ex.: "fury" -> "kingston"). Grafia com
prova (mesmo part number do fabricante em dois anúncios) já nasce ativa;
a que é só suspeita (marca nova parecida com a família de outra marca)
nasce `candidate` e espera esta revisão:

- `--list`: sugestões pendentes (`--all` mostra também ativas e recusadas);
- `--approve ID`: a sugestão passa a valer nas próximas resoluções;
- `--reject ID`: recusada -- fica guardada para nunca ser sugerida de novo.

ID aceita o começo do UUID (os 8 primeiros caracteres da listagem),
desde que só uma linha comece assim. Nenhuma chamada de IA.

Uso:
    python -m scripts.review_identity_aliases --list
    python -m scripts.review_identity_aliases --list --all
    python -m scripts.review_identity_aliases --approve 1a2b3c4d
    python -m scripts.review_identity_aliases --reject 1a2b3c4d
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
from app.products.models import ProductIdentityAlias
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession


def _line(alias: ProductIdentityAlias) -> str:
    return (
        f"{str(alias.id)[:8]}  [{alias.status}]  {alias.category} "
        f"{alias.attribute_name}: {alias.raw_value_normalized} -> "
        f"{alias.canonical_value}"
    )


async def _list(session: AsyncSession, *, include_all: bool) -> None:
    query = select(ProductIdentityAlias).order_by(
        ProductIdentityAlias.status,
        ProductIdentityAlias.category,
        ProductIdentityAlias.raw_value_normalized,
    )
    if not include_all:
        query = query.where(ProductIdentityAlias.status == "candidate")
    rows = (await session.scalars(query)).all()
    if not rows:
        print("Nenhuma sugestão pendente." if not include_all else "Tabela vazia.")
        return
    for alias in rows:
        print(_line(alias))


async def _find(session: AsyncSession, prefix: str) -> ProductIdentityAlias | None:
    cleaned = prefix.strip().lower()
    if not cleaned:
        print("ID vazio.")
        return None
    rows = (
        await session.scalars(
            select(ProductIdentityAlias).where(
                cast(ProductIdentityAlias.id, String).like(f"{cleaned}%")
            )
        )
    ).all()
    if len(rows) != 1:
        print(
            "Nenhuma grafia com esse ID."
            if not rows
            else f"{len(rows)} grafias começam com esse ID -- use mais caracteres."
        )
        return None
    return rows[0]


async def _decide(session: AsyncSession, prefix: str, *, approve: bool) -> bool:
    alias = await _find(session, prefix)
    if alias is None:
        return False
    if alias.status != "candidate":
        print(f"Só sugestão pendente pode ser revisada: {_line(alias)}")
        return False
    alias.status = "active" if approve else "rejected"
    await session.commit()
    print(
        ("Aprovada (vale nas próximas resoluções): " if approve else "Recusada: ")
        + _line(alias)
    )
    return True


async def run(
    *,
    list_all: bool = False,
    approve: str | None = None,
    reject: str | None = None,
) -> bool:
    settings = get_settings()
    async_driver = "asyncpg" if sys.platform == "win32" else "psycopg"
    engine = create_async_database_engine(settings, async_driver=async_driver)
    session_factory = create_async_session_factory(engine)
    try:
        async with session_factory() as session:
            if approve is not None:
                return await _decide(session, approve, approve=True)
            if reject is not None:
                return await _decide(session, reject, approve=False)
            await _list(session, include_all=list_all)
            return True
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Sugestões pendentes.")
    group.add_argument("--approve", metavar="ID", help="Aprova uma sugestão.")
    group.add_argument("--reject", metavar="ID", help="Recusa uma sugestão.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Com --list: mostra também as ativas e as recusadas.",
    )
    args = parser.parse_args()
    if args.all and not args.list:
        parser.error("--all só vale com --list")
    ok = asyncio.run(run(list_all=args.all, approve=args.approve, reject=args.reject))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
