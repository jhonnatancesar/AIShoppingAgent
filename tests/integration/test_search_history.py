"""Histórico real de pesquisas (Frente 5, correção de escopo 2026-09-12)
contra PostgreSQL real -- `SearchReceipt`/`SearchReceiptProduct`
(`app.quotas.models`/`app.quotas.query`), nunca `app.missions`. Ver
docstring de `SearchReceiptProduct` para por que a agregação comunitária
nunca expõe texto livre do usuário."""

import asyncio

import pytest
from app.products.models import Product
from app.quotas.models import SearchReceipt, SearchReceiptProduct
from app.quotas.query import (
    count_all_search_history,
    count_search_history_for_user,
    list_all_search_history,
    list_search_history_for_user,
    list_trending_searched_products,
)
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _seed_user(sessions, *, username: str) -> User:
    with sessions.begin() as session:
        user = User(display_name=username, role=UserRole.USER, username=username)
        session.add(user)
        session.flush()
        session.expunge(user)
    return user


def _seed_recognized_product(sessions, *, name: str, identity_key: str) -> Product:
    """Produto RECONHECIDO -- identidade completa, obrigatória pela
    constraint `ck_products_identity_complete` sempre que `identity_key`
    não é nulo."""
    with sessions.begin() as session:
        product = Product(
            name=name,
            display_name=name,
            category="monitor",
            brand="LG",
            family="UltraGear",
            model=name,
            variant="default",
            family_key=f"family:{identity_key}",
            identity_key=identity_key,
            identity_version=1,
        )
        session.add(product)
        session.flush()
        session.expunge(product)
    return product


def _seed_unrecognized_product(sessions, *, name: str) -> Product:
    with sessions.begin() as session:
        product = Product(name=name)
        session.add(product)
        session.flush()
        session.expunge(product)
    return product


def _seed_search(
    sessions, *, user_id, query_text: str, product_ids=()
) -> SearchReceipt:
    with sessions.begin() as session:
        receipt = SearchReceipt(user_id=user_id, query_text=query_text)
        session.add(receipt)
        session.flush()
        for product_id in product_ids:
            session.add(
                SearchReceiptProduct(
                    search_receipt_id=receipt.id, product_id=product_id
                )
            )
        session.flush()
        session.expunge(receipt)
    return receipt


def test_trending_never_surfaces_a_product_searched_by_a_single_user(
    integration_database,
) -> None:
    """Prova real do requisito de privacidade: um produto pesquisado por
    só UM usuário nunca aparece em "o que estão pesquisando" -- só
    quando um SEGUNDO usuário distinto pesquisa o MESMO produto
    reconhecido é que ele passa a ser elegível."""
    user1 = _seed_user(integration_database.sessions, username="trending1")
    user2 = _seed_user(integration_database.sessions, username="trending2")
    product = _seed_recognized_product(
        integration_database.sessions,
        name="Monitor UltraGear 27GP850",
        identity_key="k1",
    )
    _seed_search(
        integration_database.sessions,
        user_id=user1.id,
        query_text="ultragear 27gp850",
        product_ids=[product.id],
    )

    async def fetch():
        async with integration_database.async_sessions.begin() as session:
            return await list_trending_searched_products(session)

    solo = asyncio.run(fetch())
    assert solo == []

    _seed_search(
        integration_database.sessions,
        user_id=user2.id,
        query_text="monitor lg 27 polegadas",  # busca com texto BEM diferente
        product_ids=[product.id],
    )

    with_second_user = asyncio.run(fetch())
    assert len(with_second_user) == 1
    assert with_second_user[0].product_id == product.id
    assert with_second_user[0].display_name == "Monitor UltraGear 27GP850"
    assert with_second_user[0].searcher_count == 2


def test_trending_counts_distinct_searchers_not_searches(integration_database) -> None:
    """O mesmo usuário pesquisando o mesmo produto várias vezes nunca,
    sozinho, faz esse produto aparecer -- conta usuários distintos, não
    linhas de pesquisa."""
    user = _seed_user(integration_database.sessions, username="trending3")
    product = _seed_recognized_product(
        integration_database.sessions, name="SSD NVMe 2TB", identity_key="k2"
    )
    _seed_search(
        integration_database.sessions,
        user_id=user.id,
        query_text="ssd 2tb",
        product_ids=[product.id],
    )
    _seed_search(
        integration_database.sessions,
        user_id=user.id,
        query_text="ssd nvme 2 tb",
        product_ids=[product.id],
    )

    async def fetch():
        async with integration_database.async_sessions.begin() as session:
            return await list_trending_searched_products(session)

    assert asyncio.run(fetch()) == []


def test_trending_never_surfaces_an_unrecognized_product(integration_database) -> None:
    """Produto sem `identity_key` (ainda não reconhecido) nunca aparece
    em "o que estão pesquisando", mesmo com 2+ usuários distintos --
    filtro é sobre a natureza do produto (canônico/vetado), não só sobre
    pluralidade."""
    user1 = _seed_user(integration_database.sessions, username="trending4")
    user2 = _seed_user(integration_database.sessions, username="trending5")
    product = _seed_unrecognized_product(
        integration_database.sessions, name="Produto genérico ainda não identificado"
    )
    for user in (user1, user2):
        _seed_search(
            integration_database.sessions,
            user_id=user.id,
            query_text="algo genérico",
            product_ids=[product.id],
        )

    async def fetch():
        async with integration_database.async_sessions.begin() as session:
            return await list_trending_searched_products(session)

    assert asyncio.run(fetch()) == []


def test_search_history_scoped_to_owner_vs_spanning_every_user(
    integration_database,
) -> None:
    """ "Minhas pesquisas" (escopo do dono) vs. "Todas as pesquisas" (DEV,
    cruza usuários) -- prova real da diferença de escopo, com o texto
    real da pesquisa (`query_text`) presente nos dois casos."""
    user1 = _seed_user(integration_database.sessions, username="history1")
    user2 = _seed_user(integration_database.sessions, username="history2")
    _seed_search(integration_database.sessions, user_id=user1.id, query_text="rtx 4060")
    _seed_search(integration_database.sessions, user_id=user1.id, query_text="rtx 4070")
    _seed_search(integration_database.sessions, user_id=user2.id, query_text="ssd 1tb")

    async def fetch():
        async with integration_database.async_sessions.begin() as session:
            mine = await list_search_history_for_user(
                session, user_id=user1.id, limit=20, offset=0
            )
            mine_total = await count_search_history_for_user(session, user_id=user1.id)
            everyone = await list_all_search_history(session, limit=20, offset=0)
            everyone_total = await count_all_search_history(session)
            return mine, mine_total, everyone, everyone_total

    mine, mine_total, everyone, everyone_total = asyncio.run(fetch())

    assert mine_total == 2
    assert {receipt.query_text for receipt in mine} == {"rtx 4060", "rtx 4070"}
    assert all(receipt.user_id == user1.id for receipt in mine)

    assert everyone_total == 3
    assert {receipt.query_text for receipt in everyone} == {
        "rtx 4060",
        "rtx 4070",
        "ssd 1tb",
    }


def test_search_receipt_product_link_survives_product_deletion_cascade(
    integration_database,
) -> None:
    """`SearchReceiptProduct.product_id` é `ondelete=CASCADE` -- remover
    um Product nunca deixa uma FK órfã apontando para nada."""
    user = _seed_user(integration_database.sessions, username="history3")
    product = _seed_recognized_product(
        integration_database.sessions,
        name="Placa de vídeo temporária",
        identity_key="k3",
    )
    receipt = _seed_search(
        integration_database.sessions,
        user_id=user.id,
        query_text="placa de video",
        product_ids=[product.id],
    )

    with integration_database.sessions.begin() as session:
        session.execute(Product.__table__.delete().where(Product.id == product.id))

    with integration_database.sessions.begin() as session:
        remaining = session.scalar(
            select(SearchReceiptProduct).where(
                SearchReceiptProduct.search_receipt_id == receipt.id
            )
        )
        assert remaining is None
