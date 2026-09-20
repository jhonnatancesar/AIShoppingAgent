"""Histórico HTTP com autenticação/autorização e PostgreSQL reais."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from app.authentication.service import issue_web_session
from app.core.config import get_settings
from app.core.errors import register_api_error_handler
from app.database.dependency import get_session, get_web_async_session
from app.missions.models import Mission
from app.quotas.models import SearchReceipt, SearchReceiptProduct
from app.users.models import User, UserRole
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.search_router import router
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def _app(database):
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)

    def sync_session():
        with database.sessions.begin() as session:
            yield session

    async def async_session():
        async with database.async_sessions.begin() as session:
            yield session

    app.dependency_overrides[get_session] = sync_session
    app.dependency_overrides[get_web_async_session] = async_session
    app.dependency_overrides[get_settings] = lambda: database.settings
    return app


def _user(database, role):
    with database.sessions.begin() as session:
        user = User(
            username=f"history_{uuid4().hex[:12]}", display_name="Teste", role=role
        )
        session.add(user)
        session.flush()
        return user.id, issue_web_session(session, user=user)


@pytest.mark.parametrize("role", [None, UserRole.USER, UserRole.ADMIN])
@pytest.mark.parametrize("scope", ["mine", "all"])
def test_history_direct_http_requires_dev(integration_database, role, scope):
    database = integration_database
    cookies = {}
    if role is not None:
        _, token = _user(database, role)
        cookies[WEB_SESSION_COOKIE_NAME] = token

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(database)),
            base_url="http://testserver",
            cookies=cookies,
        ) as client:
            response = await client.get(f"/api/v1/product-search/history/{scope}")
            assert response.status_code == (401 if role is None else 403)
            assert "items" not in response.json()

    asyncio.run(run())


def test_dev_history_http_paginates_and_keeps_owner_scope(integration_database):
    database = integration_database
    owner, token = _user(database, UserRole.DEV)
    other, _ = _user(database, UserRole.USER)
    with database.sessions.begin() as session:
        session.add_all(
            [
                SearchReceipt(user_id=owner, query_text="primeira"),
                SearchReceipt(user_id=owner, query_text=None),
                SearchReceipt(user_id=other, query_text="privada de outro usuário"),
            ]
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(database)),
            base_url="http://testserver",
            cookies={WEB_SESSION_COOKIE_NAME: token},
        ) as client:
            pages = []
            for offset in (0, 1):
                response = await client.get(
                    "/api/v1/product-search/history/mine",
                    params={"limit": 1, "offset": offset},
                )
                assert response.status_code == 200
                page = response.json()
                assert (page["total"], page["limit"], page["offset"]) == (2, 1, offset)
                assert len(page["items"]) == 1
                assert page["items"][0]["user_id"] == str(owner)
                pages.extend(page["items"])
            assert len({item["id"] for item in pages}) == 2
            assert {item["query_text"] for item in pages} == {None, "primeira"}
            response = await client.get("/api/v1/product-search/history/all")
            assert response.status_code == 200
            assert response.json()["total"] == 3
            assert {item["user_id"] for item in response.json()["items"]} == {
                str(owner),
                str(other),
            }
            for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
                invalid = await client.get(
                    "/api/v1/product-search/history/all", params=params
                )
                assert invalid.status_code == 422

    asyncio.run(run())


def test_search_without_recognized_product_records_no_invented_link(
    integration_database,
):
    database = integration_database
    owner, token = _user(database, UserRole.USER)
    query = "produto inexistente dados privados de teste"

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(database)),
            base_url="http://testserver",
            cookies={WEB_SESSION_COOKIE_NAME: token},
        ) as client:
            response = await client.get("/api/v1/product-search", params={"q": query})
            assert response.status_code == 200
            assert response.json()["offers"] == []
            trending = await client.get("/api/v1/product-search/trending")
            assert trending.status_code == 200
            assert trending.json() == {"items": []}

    asyncio.run(run())
    with database.sessions.begin() as session:
        receipt = session.scalars(select(SearchReceipt)).one()
        assert receipt.user_id == owner and receipt.query_text == query
        assert (
            session.scalar(select(func.count()).select_from(SearchReceiptProduct)) == 0
        )
        assert session.scalar(select(func.count()).select_from(Mission)) == 0
