"""Testes focados da separação pesquisa → Monitorar (TASK-099)."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.products.identity import ProductRequestKind, classify_product_request
from app.products.search import search_persisted_products
from app.webapp import missions_router, search_router


def test_search_executes_only_selects_and_cannot_increase_mission_count() -> None:
    class EmptyResult:
        def all(self):
            return []

    class ReadOnlySession:
        def __init__(self):
            self.execute_calls = 0

        async def execute(self, statement):
            assert statement.is_select
            self.execute_calls += 1
            return EmptyResult()

    session = ReadOnlySession()
    hits = asyncio.run(
        search_persisted_products(
            session,
            query="mouse gamer",
            source_codes=("amazon", "kabum"),
            request_identity=classify_product_request("mouse gamer"),
        )
    )
    assert hits == ()
    assert session.execute_calls == 2


def test_generic_search_returns_options_without_creating_mission(monkeypatch) -> None:
    """Achado (auditoria TASK-112 fase 3B): `search_products` passou a
    reservar cota (TASK-107, `check_and_reserve_search_quota_async` --
    escreve de verdade via `session`) antes deste teste ser escrito;
    `SimpleNamespace()` como `session` nunca suportaria essa escrita --
    defasagem já existente antes da fase 3B, não é regressão. Mockada no
    mesmo espírito de `search_persisted_products` abaixo (fronteira já
    testada à parte em `tests/test_quotas*.py`, fora do escopo deste
    teste, que é só o roteamento de pesquisa)."""
    calls = 0

    async def fake_search(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ()

    async def fake_reserve_quota(*args, **kwargs):
        return None

    monkeypatch.setattr(search_router, "search_persisted_products", fake_search)
    monkeypatch.setattr(
        search_router, "check_and_reserve_search_quota_async", fake_reserve_quota
    )
    response = asyncio.run(
        search_router.search_products(
            q="mouse gamer",
            stores=["amazon", "kabum"],
            user=SimpleNamespace(id=uuid4()),
            session=SimpleNamespace(),
        )
    )

    assert calls == 1
    assert response.request_kind is ProductRequestKind.GENERIC_CATEGORY
    assert response.offers == []
    assert response.variants == []


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("iPhone 17 Pro 256GB", ProductRequestKind.SPECIFIC_PRODUCT),
        ("iPhone 17", ProductRequestKind.PRODUCT_FAMILY),
        ("cadeira gamer", ProductRequestKind.GENERIC_CATEGORY),
    ],
)
def test_search_preserves_task_097_request_kinds(
    monkeypatch, query: str, kind: ProductRequestKind
) -> None:
    """Mesma correção de mock de `check_and_reserve_search_quota_async`
    (TASK-107) do teste acima -- ver docstring lá."""

    async def fake_search(*args, **kwargs):
        return ()

    async def fake_reserve_quota(*args, **kwargs):
        return None

    monkeypatch.setattr(search_router, "search_persisted_products", fake_search)
    monkeypatch.setattr(
        search_router, "check_and_reserve_search_quota_async", fake_reserve_quota
    )
    response = asyncio.run(
        search_router.search_products(
            q=query,
            stores=["amazon"],
            user=SimpleNamespace(id=uuid4()),
            session=SimpleNamespace(),
        )
    )
    assert response.request_kind is kind


def test_monitor_action_creates_mission_with_selected_family_variants(
    monkeypatch,
) -> None:
    user_id = uuid4()
    mission_id = uuid4()
    product_id = uuid4()
    now = datetime(2026, 8, 22, 18, 0, tzinfo=UTC)
    mission = SimpleNamespace(
        id=mission_id,
        title="iPhone 17",
        status="active",
        state_version=1,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    captured: dict[str, object] = {}

    async def fake_create(session, **kwargs):
        captured["create"] = kwargs
        return mission, ("amazon",)

    async def fake_select(session, **kwargs):
        captured["select"] = kwargs
        mission.state_version += 1
        return ()

    monkeypatch.setattr(missions_router, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        missions_router, "create_mission_from_criteria_async", fake_create
    )
    monkeypatch.setattr(
        missions_router, "set_mission_product_selection_async", fake_select
    )

    payload = missions_router.CreateMissionRequest(
        search_query="iPhone 17",
        source_codes=["amazon"],
        variant_product_ids=[product_id],
    )
    response = asyncio.run(
        missions_router.create_mission(
            payload,
            user=SimpleNamespace(id=user_id),
            session=SimpleNamespace(),
        )
    )

    assert captured["create"]["search_query"] == "iPhone 17"
    assert captured["select"]["product_ids"] == (product_id,)
    assert captured["select"]["allow_uncollected_family_products"] is True
    assert response.id == mission_id
    assert response.state_version == 2
