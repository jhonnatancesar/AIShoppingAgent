"""Consultas somente leitura de histórico de pesquisa (Frente 5, correção
de escopo 2026-09-12) -- lê `SearchReceipt`/`SearchReceiptProduct`,
nunca `app.missions`. Ver docstring de `SearchReceiptProduct` para por
que a agregação comunitária nunca expõe texto livre do usuário."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.products.models import Product
from app.quotas.models import SearchReceipt, SearchReceiptProduct

# Mesmo raciocínio de privacidade do "Veja o que estão pesquisando"
# anterior (agora aplicado a PRODUTOS reconhecidos, não a texto livre):
# um produto só entra na lista quando pelo menos este número de usuários
# DISTINTOS o pesquisou -- defesa em profundidade além do filtro
# principal (só produtos com `identity_key` resolvido nunca aparecem
# aqui como texto livre).
_MIN_TRENDING_SEARCHERS = 2


@dataclass(frozen=True, slots=True)
class TrendingSearchedProduct:
    """Produto RECONHECIDO pesquisado por vários usuários -- nunca
    carrega `user_id`/`query_text`: é o próprio formato que impede a
    view comunitária de expor quem pesquisou o quê, ou o texto livre
    que a pessoa digitou."""

    product_id: UUID
    display_name: str
    searcher_count: int


async def list_trending_searched_products(
    session: AsyncSession, *, limit: int = 20
) -> list[TrendingSearchedProduct]:
    """Produtos reconhecidos (`identity_key IS NOT NULL`) presentes em
    resultados de pesquisas reais (`SearchReceiptProduct`), agregados
    por quantidade de USUÁRIOS distintos que os pesquisaram -- nunca por
    quantidade de pesquisas (o mesmo usuário pesquisando várias vezes o
    mesmo produto nunca, sozinho, faz esse produto aparecer)."""
    searcher_count = func.count(func.distinct(SearchReceipt.user_id))
    statement = (
        select(
            Product.id.label("product_id"),
            func.coalesce(Product.display_name, Product.name).label("display_name"),
            searcher_count.label("searcher_count"),
        )
        .select_from(SearchReceiptProduct)
        .join(SearchReceipt, SearchReceipt.id == SearchReceiptProduct.search_receipt_id)
        .join(Product, Product.id == SearchReceiptProduct.product_id)
        .where(Product.identity_key.is_not(None))
        .group_by(Product.id, Product.display_name, Product.name)
        .having(searcher_count >= _MIN_TRENDING_SEARCHERS)
        .order_by(searcher_count.desc(), Product.id.asc())
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [
        TrendingSearchedProduct(
            product_id=row.product_id,
            display_name=row.display_name,
            searcher_count=row.searcher_count,
        )
        for row in rows
    ]


async def list_search_history_for_user(
    session: AsyncSession, *, user_id: UUID, limit: int, offset: int
) -> Sequence[SearchReceipt]:
    """ "Minhas pesquisas" (DEV): histórico real de pesquisas do próprio
    usuário, mais recente primeiro -- nunca missões."""
    statement = (
        select(SearchReceipt)
        .where(SearchReceipt.user_id == user_id)
        .order_by(SearchReceipt.created_at.desc(), SearchReceipt.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.scalars(statement)).all()


async def count_search_history_for_user(session: AsyncSession, *, user_id: UUID) -> int:
    statement = select(func.count(SearchReceipt.id)).where(
        SearchReceipt.user_id == user_id
    )
    return await session.scalar(statement) or 0


async def list_all_search_history(
    session: AsyncSession, *, limit: int, offset: int
) -> Sequence[SearchReceipt]:
    """ "Todas as pesquisas" (DEV): sem filtro de `user_id` -- exclusivo
    de `Permission.DEV_PANEL_ACCESS`."""
    statement = (
        select(SearchReceipt)
        .order_by(SearchReceipt.created_at.desc(), SearchReceipt.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.scalars(statement)).all()


async def count_all_search_history(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(SearchReceipt.id))) or 0
