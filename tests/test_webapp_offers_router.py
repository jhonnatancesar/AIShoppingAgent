"""Contrato e ownership da página USER de oferta (TASK-095)."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.alerts.internal_history import InternalHistoricalBest
from app.collection.contracts import (
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
)
from app.collection.models import OfferInstallmentOption, PriceObservation
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.core.config import Settings, get_settings
from app.core.errors import register_api_error_handler
from app.coupons.models import Coupon
from app.database.dependency import (
    get_web_async_session,
    get_web_async_session_factory,
)
from app.historical_bootstrap.models import (
    ExternalPriceReference,
    HistoricalBootstrap,
    HistoricalBootstrapStatus,
)
from app.offers.models import Offer
from app.offers.query import (
    AllOfferSummaryDev,
    OfferPriceHistory,
    PriceHistoryMetrics,
    PriceHistoryPoint,
    PriceHistorySeries,
    UserComparisonOffer,
    UserOfferComparison,
    UserOfferDetail,
    UserOfferSummary,
    _fetch_daily_low_points,
    _resolve_current_amount,
    _resolve_reference_currency,
    comparison_offers_statement,
    get_offer_detail_for_user,
    offer_for_user_statement,
)
from app.products.models import Product
from app.stores.models import Seller, Store
from app.users.models import User, UserRole
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.offers_router import _as_comparison, router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)


def _user() -> User:
    return User(id=uuid4(), display_name="USER", role=UserRole.USER, username="user")


def _detail() -> UserOfferDetail:
    store = Store(
        id=uuid4(), code="amazon", name="Amazon", base_url="https://amazon.com.br"
    )
    seller = Seller(id=uuid4(), store_id=store.id, name="Amazon.com.br")
    product = Product(id=uuid4(), name="Título bruto", display_name="Galaxy S24 Ultra")
    offer = Offer(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        seller_id=seller.id,
        url="https://amazon.com.br/dp/example",
        image_url="https://images.example/offer.jpg",
        rating_average=Decimal("4.80"),
        review_count=2256,
        rating_observed_at=NOW,
        last_seen_at=NOW,
    )
    observation = PriceObservation(
        id=uuid4(),
        offer_id=offer.id,
        collection_run_id=uuid4(),
        amount=Decimal("4599.00"),
        currency="BRL",
        shipping_amount=Decimal("20.00"),
        total_amount=Decimal("4619.00"),
        fulfillment="Amazon.com.br",
        seller_kind=MarketplacePartyKind.PLATFORM,
        fulfillment_kind=MarketplacePartyKind.PLATFORM,
        condition=OfferCondition.NEW,
        availability=Availability.AVAILABLE,
        observed_at=NOW,
    )
    installment = OfferInstallmentOption(
        id=uuid4(),
        price_observation_id=observation.id,
        installment_count=12,
        installment_amount=Decimal("383.25"),
        interest_kind=InstallmentInterestKind.INTEREST_FREE,
        is_highlighted=True,
    )
    return UserOfferDetail(offer, product, store, seller, observation, (installment,))


def _summary() -> UserOfferSummary:
    """Mesmos dados de `_detail()`, sem `installments` -- é exatamente o
    que `UserOfferSummary` carrega (a listagem nunca teve parcelas)."""
    detail = _detail()
    return UserOfferSummary(
        detail.offer, detail.product, detail.store, detail.seller, detail.observation
    )


@pytest.fixture
def session() -> MagicMock:
    value = MagicMock()
    value.commit = AsyncMock()
    value.add = MagicMock()
    return value


@pytest.fixture
def client(session: MagicMock, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_web_async_session] = lambda: session
    owner = _user()
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: owner
    )
    return TestClient(app)


@pytest.fixture
def client_with_coupons_enabled(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """FASE G (2026-09-06): mesmo client de sempre, só com a flag
    `coupons_enabled` explicitamente ligada -- desde 2026-09-21
    `coupons_enabled` já nasce `True` por decisão explícita do usuário
    (nunca mais presumida), então esta fixture é redundante com a
    `client` normal, mas mantida para deixar a intenção explícita nos
    testes que a usam."""
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_web_async_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: Settings(coupons_enabled=True)
    owner = _user()
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: owner
    )
    return TestClient(app)


@pytest.fixture
def client_with_coupons_disabled(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Mesmo client de sempre, com `coupons_enabled` explicitamente
    desligada -- achado real + correção do usuário (2026-09-21):
    `coupons_enabled` deixou de nascer `False` por padrão (decisão
    unilateral do assistente numa sessão anterior, nunca um pedido do
    usuário) e passou a nascer `True`; os testes que provam o
    comportamento "flag desligada" (TASK-113 original, sem cupom)
    precisam desligar explicitamente agora, a `client` normal não
    representa mais esse cenário."""
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_web_async_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: Settings(coupons_enabled=False)
    owner = _user()
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: owner
    )
    return TestClient(app)


@pytest.fixture
def client_as_dev(session: MagicMock, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TASK-126: mesmo client de sempre, usuário logado é DEV --
    `authorize(session, user, Permission.DEV_PANEL_ACCESS)` é chamado
    inline (mesmo padrão de `Permission.MISSION_READ` já usado neste
    router), direto com a sessão async injetada -- nunca precisa de um
    `get_session` síncrono separado (diferente de `require_dev_web_
    session`, a dependência do FastAPI, que este endpoint não usa)."""
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_web_async_session] = lambda: session
    dev_user = User(id=uuid4(), display_name="DEV", role=UserRole.DEV, username="dev")
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: dev_user
    )
    return TestClient(app)


def _cookies() -> dict[str, str]:
    return {WEB_SESSION_COOKIE_NAME: "task095-web-session"}


def test_user_accesses_offer_linked_to_own_mission(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _detail()
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )

    response = client.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Galaxy S24 Ultra"
    assert body["rating"] == {
        "average": "4.80",
        "review_count": 2256,
        "observed_at": NOW.isoformat(),
    }
    assert body["latest_observation"]["amount"] == "4599.00"
    assert body["latest_observation"]["installments"][0]["installment_count"] == 12


def test_offer_without_own_image_falls_back_to_product_canonical_image(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subtask 4 (auditoria GG Oferta): Offer sem imagem própria + Product
    com canônica -- a API expõe a canônica, nunca `null` à toa, sem
    alternativa (nada mais para tentar)."""
    detail = _detail()
    detail.offer.image_url = None
    detail.product.canonical_image_url = "https://media.pichau.com.br/canonica.jpg"
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )

    response = client.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["image_url"] == "https://media.pichau.com.br/canonica.jpg"
    assert body["image_fallback_url"] is None


def test_offer_with_canonical_and_different_own_image_exposes_both(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subtask 4 (revisão, item 2 da checagem final): quando a Offer TEM
    imagem própria mas o Product também tem canônica DIFERENTE, a API
    expõe a canônica como principal (`image_url`) e a própria Offer como
    alternativa (`image_fallback_url`) -- o front-end decide, em runtime,
    se precisa tentar a alternativa (canônica quebrou ao carregar)."""
    detail = _detail()
    assert detail.offer.image_url == "https://images.example/offer.jpg"  # sanity
    detail.product.canonical_image_url = "https://media.pichau.com.br/canonica.jpg"
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )

    response = client.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["image_url"] == "https://media.pichau.com.br/canonica.jpg"
    assert body["image_fallback_url"] == "https://images.example/offer.jpg"


def test_offer_with_applicable_coupon_shows_final_price(
    client_with_coupons_enabled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_with_coupons_enabled
    """Consumo de cupons (2026-09-06): a API re-consulta cupons NA HORA
    (nunca reaproveita um vínculo antigo persistido) e devolve o preço
    final calculado -- o preço original (`latest_observation.amount`)
    continua vindo intocado da mesma `PriceObservation` de sempre."""
    detail = _detail()
    coupon = Coupon(
        id=uuid4(),
        store_id=detail.offer.store_id,
        code="SITE15",
        discount_kind="fixed_amount",
        discount_value=Decimal("100.00"),
        scope_kind="store_wide",
        evidence="ev",
        status="active",
        last_seen_at=NOW,
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_candidate_coupons_for_offer",
        AsyncMock(return_value=(coupon,)),
    )

    response = client.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["latest_observation"]["amount"] == "4599.00"  # original intocado
    assert body["applied_coupon"] == {
        "code": "SITE15",
        "discount_kind": "fixed_amount",
        "original_amount": "4599.00",
        "discount_amount": "100.00",
        "final_amount": "4499.00",
        "currency": "BRL",
    }


def test_offer_coupon_lookup_failure_never_breaks_offer_display(
    client_with_coupons_enabled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_with_coupons_enabled
    detail = _detail()
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_candidate_coupons_for_offer",
        AsyncMock(side_effect=RuntimeError("conexão perdida")),
    )

    response = client.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["applied_coupon"] is None
    assert body["latest_observation"]["amount"] == "4599.00"


def test_offer_never_queries_coupons_when_flag_is_off(
    client_with_coupons_disabled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FASE G: `coupons_enabled=False` (explícito -- deixou de ser o
    default em 2026-09-21) -- comportamento idêntico ao existente antes
    de cupons: nem tenta consultar, `applied_coupon` sempre `None`."""
    detail = _detail()
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )
    query = AsyncMock(side_effect=AssertionError("não deveria consultar cupons"))
    monkeypatch.setattr(
        "app.webapp.offers_router.get_candidate_coupons_for_offer", query
    )

    response = client_with_coupons_disabled.get(
        f"/api/v1/offers/{detail.offer.id}", cookies=_cookies()
    )

    assert response.status_code == 200
    assert response.json()["applied_coupon"] is None
    query.assert_not_called()


def test_list_offers_shows_applied_coupon_when_eligible(
    client_with_coupons_enabled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Achado real (2026-09-08): `list_offers` nunca calculava cupom
    nenhum -- só o detalhe de UMA Offer (`get_user_offer`) fazia isso.
    214 cupons reais persistidos em PROD nunca apareciam na listagem, a
    primeira superfície que o usuário vê. Mesmo cenário de
    `test_offer_with_applicable_coupon_shows_final_price`, agora pela
    listagem."""
    client = client_with_coupons_enabled
    summary = _summary()
    coupon = Coupon(
        id=uuid4(),
        store_id=summary.offer.store_id,
        code="SITE15",
        discount_kind="fixed_amount",
        discount_value=Decimal("100.00"),
        scope_kind="store_wide",
        evidence="ev",
        status="active",
        last_seen_at=NOW,
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.list_user_offers",
        AsyncMock(return_value=((summary,), 1)),
    )
    batch = AsyncMock(return_value={summary.offer.store_id: (coupon,)})
    monkeypatch.setattr("app.webapp.offers_router.get_active_coupons_by_store", batch)

    response = client.get("/api/v1/offers", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert (
        body["items"][0]["latest_observation"]["amount"] == "4599.00"
    )  # original intocado
    assert body["items"][0]["applied_coupon"] == {
        "code": "SITE15",
        "discount_kind": "fixed_amount",
        "original_amount": "4599.00",
        "discount_amount": "100.00",
        "final_amount": "4499.00",
        "currency": "BRL",
    }
    batch.assert_awaited_once()


def test_list_offers_never_queries_coupons_when_flag_is_off(
    client_with_coupons_disabled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FASE G: `coupons_enabled=False` (explícito -- deixou de ser o
    default em 2026-09-21) -- a listagem nem tenta consultar cupons,
    `applied_coupon` sempre `None`, sem regressão no comportamento
    anterior a esta correção."""
    summary = _summary()
    monkeypatch.setattr(
        "app.webapp.offers_router.list_user_offers",
        AsyncMock(return_value=((summary,), 1)),
    )
    batch = AsyncMock(side_effect=AssertionError("não deveria consultar cupons"))
    monkeypatch.setattr("app.webapp.offers_router.get_active_coupons_by_store", batch)

    response = client_with_coupons_disabled.get("/api/v1/offers", cookies=_cookies())

    assert response.status_code == 200
    assert response.json()["items"][0]["applied_coupon"] is None
    batch.assert_not_called()


def test_list_offers_coupon_lookup_failure_never_breaks_listing(
    client_with_coupons_enabled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesma disciplina do detalhe (`test_offer_coupon_lookup_failure_
    never_breaks_offer_display`): consumo de cupons é derivado, nunca
    crítico -- falha na consulta em lote não pode derrubar a listagem
    inteira."""
    client = client_with_coupons_enabled
    summary = _summary()
    monkeypatch.setattr(
        "app.webapp.offers_router.list_user_offers",
        AsyncMock(return_value=((summary,), 1)),
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_active_coupons_by_store",
        AsyncMock(side_effect=RuntimeError("conexão perdida")),
    )

    response = client.get("/api/v1/offers", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["applied_coupon"] is None
    assert body["items"][0]["latest_observation"]["amount"] == "4599.00"


def test_list_offers_omits_coupon_for_offer_without_observation(
    client_with_coupons_enabled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem `PriceObservation` não há `reference_amount` pra calcular
    desconto -- nunca inventa um preço final sem observação real."""
    client = client_with_coupons_enabled
    summary = _summary()
    summary_no_price = UserOfferSummary(
        summary.offer, summary.product, summary.store, summary.seller, None
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.list_user_offers",
        AsyncMock(return_value=((summary_no_price,), 1)),
    )
    batch = AsyncMock(return_value={})
    monkeypatch.setattr("app.webapp.offers_router.get_active_coupons_by_store", batch)

    response = client.get("/api/v1/offers", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["applied_coupon"] is None
    assert body["items"][0]["latest_observation"] is None


# --- TASK-126: `all_users` (DEV-only, inclui NO_MATCH) -----------------


def test_list_offers_all_users_requires_dev_role(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`client` (fixture normal) é USER -- barrado antes de chamar
    `list_all_offers_dev`, mesmo achado real desta TASK: a listagem
    normal (`list_user_offers`) nunca deveria ser trocada sem checar
    DEV primeiro."""
    dev_query = AsyncMock(side_effect=AssertionError("não deveria chamar a query DEV"))
    monkeypatch.setattr("app.webapp.offers_router.list_all_offers_dev", dev_query)

    response = client.get("/api/v1/offers?all_users=true", cookies=_cookies())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "dev_access_denied"
    dev_query.assert_not_called()


def test_list_offers_all_users_as_dev_exposes_no_match_classification(
    client_as_dev: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prova o achado da TASK-126: com `all_users=true` e usuário DEV, a
    listagem usa `list_all_offers_dev` (todos os usuários, `NO_MATCH`
    incluso) em vez de `list_user_offers`, e o item devolvido expõe
    `classification` -- `None` nunca aparece quando o dado existe."""
    summary = _summary()
    all_users_item = AllOfferSummaryDev(
        offer=summary.offer,
        product=summary.product,
        store=summary.store,
        seller=summary.seller,
        observation=summary.observation,
        classification=OfferRelevance.NO_MATCH,
    )
    all_users_query = AsyncMock(return_value=((all_users_item,), 1))
    monkeypatch.setattr("app.webapp.offers_router.list_all_offers_dev", all_users_query)
    user_query = AsyncMock(
        side_effect=AssertionError("não deveria chamar a query USER")
    )
    monkeypatch.setattr("app.webapp.offers_router.list_user_offers", user_query)

    response = client_as_dev.get("/api/v1/offers?all_users=true", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["classification"] == "no_match"
    all_users_query.assert_awaited_once()
    user_query.assert_not_called()


def test_list_offers_default_never_exposes_classification(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem `all_users` (comportamento de sempre) -- `classification`
    nunca aparece, mesmo para USER normal, nunca um vazamento de dado
    DEV-only pelo caminho comum."""
    summary = _summary()
    monkeypatch.setattr(
        "app.webapp.offers_router.list_user_offers",
        AsyncMock(return_value=((summary,), 1)),
    )

    response = client.get("/api/v1/offers", cookies=_cookies())

    assert response.status_code == 200
    assert response.json()["items"][0]["classification"] is None


def test_other_user_cannot_access_offer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=None),
    )

    response = client.get(f"/api/v1/offers/{uuid4()}", cookies=_cookies())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "offer_access_denied"


def test_no_match_does_not_grant_access(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    statement = offer_for_user_statement(offer_id=uuid4(), user_id=uuid4())
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "'match', 'possible_match'" in sql
    assert "'no_match'" not in sql
    assert "missions.user_id" in sql
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=None),
    )

    response = client.get(f"/api/v1/offers/{uuid4()}", cookies=_cookies())

    assert response.status_code == 403


def test_offer_query_uses_latest_price_observation() -> None:
    expected = _detail()
    execute_result = MagicMock()
    execute_result.first.return_value = (
        expected.offer,
        expected.product,
        expected.store,
        expected.seller,
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.scalar = AsyncMock(return_value=expected.observation)
    session.scalars = AsyncMock(return_value=list(expected.installments))

    result = asyncio.run(
        get_offer_detail_for_user(session, offer_id=expected.offer.id, user_id=uuid4())
    )

    assert result is not None
    assert result.observation is expected.observation
    latest_statement = session.scalar.await_args.args[0]
    sql = str(latest_statement.compile(dialect=postgresql.dialect()))
    assert "price_observations.observed_at DESC" in sql
    assert "price_observations.id DESC" in sql


def test_comparison_query_requires_same_product_and_user_owned_relevance() -> None:
    product_id, user_id = uuid4(), uuid4()
    sql = str(
        comparison_offers_statement(product_id=product_id, user_id=user_id).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert f"offers.product_id = '{product_id}'" in sql
    assert f"missions.user_id = '{user_id}'" in sql
    assert "classification in ('match', 'possible_match')" in sql
    assert "no_match" not in sql


def test_price_history_returns_series_and_metrics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _detail()
    store_id = detail.store.id
    history = OfferPriceHistory(
        product_id=detail.product.id,
        comparable=True,
        reason=None,
        period="1m",
        currency="BRL",
        period_from=datetime(2026, 7, 23, 3, 0, tzinfo=UTC),
        period_to=NOW,
        series=(
            PriceHistorySeries(
                store_id=store_id,
                store_code="amazon",
                store_name="Amazon",
                points=(
                    PriceHistoryPoint(
                        day=NOW.date(),
                        amount=Decimal("4599.00"),
                        observation_id=uuid4(),
                        offer_id=detail.offer.id,
                    ),
                ),
            ),
        ),
        metrics=PriceHistoryMetrics(
            current_amount=Decimal("4599.00"),
            min_amount=Decimal("4199.99"),
            max_amount=Decimal("4599.00"),
            average_amount=Decimal("4399.5000"),
            variation_percent=Decimal("2.50"),
        ),
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_price_history_for_user",
        AsyncMock(return_value=history),
    )

    response = client.get(
        f"/api/v1/offers/{detail.offer.id}/price-history?period=1m",
        cookies=_cookies(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["comparable"] is True
    assert body["currency"] == "BRL"
    assert body["series"][0]["store_code"] == "amazon"
    assert body["series"][0]["points"][0]["date"] == NOW.date().isoformat()
    assert body["series"][0]["points"][0]["amount"] == "4599.00"
    assert body["metrics"]["current_amount"] == "4599.00"
    assert body["metrics"]["variation_percent"] == "2.50"


def test_price_history_not_comparable_returns_200_with_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    product_id = uuid4()
    history = OfferPriceHistory(
        product_id=product_id,
        comparable=False,
        reason="unresolved_product_identity",
        period="all",
        currency=None,
        period_from=None,
        period_to=NOW,
        series=(),
        metrics=None,
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_price_history_for_user",
        AsyncMock(return_value=history),
    )

    response = client.get(
        f"/api/v1/offers/{uuid4()}/price-history?period=all", cookies=_cookies()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["comparable"] is False
    assert body["reason"] == "unresolved_product_identity"
    assert body["series"] == []
    assert body["metrics"] is None
    assert body["period_from"] is None


def test_price_history_denies_access_for_unauthorized_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_price_history_for_user",
        AsyncMock(return_value=None),
    )

    response = client.get(f"/api/v1/offers/{uuid4()}/price-history", cookies=_cookies())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "offer_access_denied"


def test_price_history_invalid_period_returns_native_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_price_history_for_user",
        AsyncMock(side_effect=AssertionError("não deveria ser chamado")),
    )

    response = client.get(
        f"/api/v1/offers/{uuid4()}/price-history?period=2y", cookies=_cookies()
    )

    assert response.status_code == 422
    body = response.json()
    assert "detail" in body
    assert "error" not in body


def test_daily_low_points_query_filters_new_available_matching_currency_and_ownership() -> (
    None
):
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.all.return_value = []
    session.execute = AsyncMock(return_value=execute_result)
    product_id, user_id = uuid4(), uuid4()
    start = datetime(2026, 7, 23, 3, 0, tzinfo=UTC)
    end = NOW

    asyncio.run(
        _fetch_daily_low_points(
            session,
            product_id=product_id,
            user_id=user_id,
            start_utc=start,
            end_utc=end,
            reference_currency="BRL",
        )
    )

    statement = session.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert f"offers.product_id = '{product_id}'" in sql
    assert f"missions.user_id = '{user_id}'" in sql
    assert "classification in ('match', 'possible_match')" in sql
    assert "condition = 'new'" in sql
    assert "availability = 'available'" in sql
    assert "currency = 'brl'" in sql
    assert "observed_at >=" in sql
    assert "observed_at <=" in sql


def test_daily_low_points_query_omits_lower_bound_for_period_all() -> None:
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.all.return_value = []
    session.execute = AsyncMock(return_value=execute_result)

    asyncio.run(
        _fetch_daily_low_points(
            session,
            product_id=uuid4(),
            user_id=uuid4(),
            start_utc=None,
            end_utc=NOW,
            reference_currency="BRL",
        )
    )

    statement = session.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert "observed_at >=" not in sql
    assert "observed_at <=" in sql


def test_current_amount_query_resolves_latest_observation_per_offer_before_validity() -> (
    None
):
    """Rodada de frescor: `_resolve_current_amount` agora busca candidatos
    via `session.execute` (não mais um `MIN()` escalar direto) para poder
    filtrar cada um por `resolve_offer_freshness` em seguida -- este
    teste continua verificando só a ESTRUTURA da query de candidatos
    (mesma garantia de sempre: latest-per-offer antes de checar
    validade), sem exercitar o loop de frescor (mock devolve zero
    candidatos)."""
    session = MagicMock()

    async def fake_execute(statement, *args, **kwargs):
        return MagicMock(all=lambda: [])

    session.execute = AsyncMock(side_effect=fake_execute)
    product_id, user_id = uuid4(), uuid4()

    asyncio.run(
        _resolve_current_amount(
            session,
            product_id=product_id,
            user_id=user_id,
            reference_currency="BRL",
            now=NOW,
        )
    )

    statement = session.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert "row_number() over" in sql
    assert "partition by price_observations.offer_id" in sql
    assert f"offers.product_id = '{product_id}'" in sql
    assert f"missions.user_id = '{user_id}'" in sql
    assert "rn = 1" in sql
    assert "condition = 'new'" in sql
    assert "availability = 'available'" in sql


def test_reference_currency_prefers_anchors_own_observation() -> None:
    """Correção pós-plano item 4(A): a âncora tem prioridade -- nunca cai
    para outra Offer só porque esta é mais recente."""
    session = MagicMock()
    session.scalar = AsyncMock(return_value="BRL")
    product_id, user_id, anchor_offer_id = uuid4(), uuid4(), uuid4()

    result = asyncio.run(
        _resolve_reference_currency(
            session,
            product_id=product_id,
            user_id=user_id,
            anchor_offer_id=anchor_offer_id,
        )
    )

    assert result == "BRL"
    assert session.scalar.await_count == 1
    statement = session.scalar.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert f"price_observations.offer_id = '{anchor_offer_id}'" in sql
    assert "order by price_observations.observed_at desc" in sql


def test_reference_currency_falls_back_to_other_accessible_offers() -> None:
    """Correção pós-plano item 4(B): só cai para outra Offer quando a
    âncora não tem NENHUMA observação própria."""
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[None, "BRL"])
    product_id, user_id, anchor_offer_id = uuid4(), uuid4(), uuid4()

    result = asyncio.run(
        _resolve_reference_currency(
            session,
            product_id=product_id,
            user_id=user_id,
            anchor_offer_id=anchor_offer_id,
        )
    )

    assert result == "BRL"
    assert session.scalar.await_count == 2
    fallback_statement = session.scalar.await_args_list[1].args[0]
    sql = str(
        fallback_statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert f"offers.product_id = '{product_id}'" in sql
    assert f"missions.user_id = '{user_id}'" in sql
    assert "order by price_observations.observed_at desc" in sql


def test_comparison_response_preserves_source_bound_data() -> None:
    detail = _detail()
    detail.product.identity_key = "resolved-key"
    detail.product.variant = "512 GB"
    response = _as_comparison(
        UserOfferComparison(
            product=detail.product,
            offers=(
                UserComparisonOffer(
                    detail.offer,
                    detail.store,
                    detail.seller,
                    detail.observation,
                    detail.installments,
                ),
            ),
        )
    )
    assert response.comparable is True
    assert response.variant == "512 GB"
    assert response.offers[0].store.code == "amazon"
    assert response.offers[0].rating is not None
    assert response.offers[0].latest_observation is not None
    assert response.offers[0].latest_observation.total_amount == Decimal("4619.00")


# --- TASK-127: preço histórico + botão (e furo da TASK-126) ------------

_CSRF = "task127-csrf"


def _historical_settings(**overrides) -> Settings:
    values = {"cesar_core_api_key_file": Path("fake-core-key")}
    values.update(overrides)
    return Settings(**values)


def _historical_client(
    session: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    role: UserRole,
    settings: Settings | None = None,
) -> TestClient:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_web_async_session] = lambda: session
    app.dependency_overrides[get_web_async_session_factory] = lambda: "factory"
    effective = settings if settings is not None else _historical_settings()
    app.dependency_overrides[get_settings] = lambda: effective
    viewer = User(id=uuid4(), display_name=role.value, role=role, username="viewer")
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: viewer
    )
    return TestClient(app)


def _post_cookies() -> dict[str, str]:
    return {WEB_SESSION_COOKIE_NAME: "task127-session", CSRF_COOKIE_NAME: _CSRF}


def _identified_detail() -> UserOfferDetail:
    detail = _detail()
    detail.product.identity_key = "smartphone:samsung:galaxy-s24-ultra"
    return detail


def _mock_historical_reads(
    monkeypatch: pytest.MonkeyPatch,
    detail: UserOfferDetail,
    *,
    bootstrap=None,
    internal=None,
    reference=None,
) -> None:
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_internal_historical_best",
        AsyncMock(return_value=internal),
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_external_price_reference_evidence",
        AsyncMock(return_value=reference),
    )
    state = (
        bootstrap
        if isinstance(bootstrap, AsyncMock)
        else AsyncMock(return_value=bootstrap)
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.get_historical_bootstrap_state", state
    )


def _mock_search_machinery(monkeypatch: pytest.MonkeyPatch, *, claimed="claimed"):
    claim = AsyncMock(return_value=claimed)
    runner = AsyncMock()
    user_ai = MagicMock(return_value="user-ai")
    admin_ai = MagicMock(return_value="admin-dev-ai")
    monkeypatch.setattr(
        "app.webapp.offers_router.claim_manual_historical_bootstrap", claim
    )
    monkeypatch.setattr(
        "app.webapp.offers_router._run_manual_historical_search", runner
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.build_user_ai_provider_manager", user_ai
    )
    monkeypatch.setattr(
        "app.webapp.offers_router.build_admin_dev_ai_provider_manager", admin_ai
    )
    monkeypatch.setattr("app.webapp.offers_router.CesarCoreFetchProvider", MagicMock())
    return claim, runner, user_ai, admin_ai


def _recent_bootstrap(days_ago: int = 10) -> HistoricalBootstrap:
    return HistoricalBootstrap(
        product_id=uuid4(),
        condition="new",
        currency="BRL",
        status=HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES,
        completed_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


def _search(client: TestClient, offer_id, *, force: bool = False):
    return client.post(
        f"/api/v1/offers/{offer_id}/historical-price/search",
        json={"force": force},
        cookies=_post_cookies(),
        headers={"X-CSRF-Token": _CSRF},
    )


def test_historical_price_shows_already_collected_values_without_any_click(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pedido explícito do usuário: o que já foi coletado aparece na hora,
    nunca um campo vazio esperando alguém clicar no botão."""
    detail = _identified_detail()
    reference = ExternalPriceReference(
        amount=Decimal("2199.00"),
        currency="BRL",
        source="hardware_barato",
        safe_url="https://www.hardwarebarato.com/produtos/s24-ultra",
        store_name=None,
        historical_date=date(2026, 3, 12),
        collected_at=NOW,
    )
    _mock_historical_reads(
        monkeypatch,
        detail,
        internal=InternalHistoricalBest(amount=Decimal("2249.00"), currency="BRL"),
        reference=reference,
    )
    client = _historical_client(session, monkeypatch, role=UserRole.USER)

    response = client.get(
        f"/api/v1/offers/{detail.offer.id}/historical-price", cookies=_cookies()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["internal"] == {"amount": "2249.00", "currency": "BRL"}
    assert body["reference"]["amount"] == "2199.00"
    assert body["reference"]["source_url"] == reference.safe_url
    assert body["reference"]["historical_date"] == "2026-03-12"
    assert body["search"]["availability"] == "available"


def test_historical_price_search_is_disabled_without_core_credential(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _identified_detail()
    _mock_historical_reads(monkeypatch, detail)
    client = _historical_client(
        session,
        monkeypatch,
        role=UserRole.USER,
        settings=_historical_settings(cesar_core_api_key_file=None),
    )

    response = client.get(
        f"/api/v1/offers/{detail.offer.id}/historical-price", cookies=_cookies()
    )

    assert response.json()["search"]["availability"] == "disabled"


def test_user_search_without_price_claims_and_runs_in_background(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _identified_detail()
    in_progress = HistoricalBootstrap(
        product_id=detail.product.id,
        condition="new",
        currency="BRL",
        status=HistoricalBootstrapStatus.PROCESSING,
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    _mock_historical_reads(
        monkeypatch, detail, bootstrap=AsyncMock(side_effect=[None, in_progress])
    )
    claim, runner, _user_ai, admin_ai = _mock_search_machinery(monkeypatch)
    client = _historical_client(session, monkeypatch, role=UserRole.USER)

    response = _search(client, detail.offer.id)

    assert response.status_code == 202
    assert response.json()["search"]["availability"] == "in_progress"
    assert claim.await_args.kwargs["force"] is False
    runner.assert_awaited_once()
    assert runner.await_args.kwargs["profile"] is UserRole.USER
    assert runner.await_args.kwargs["ai"] == "user-ai"
    admin_ai.assert_not_called()


def test_user_cannot_search_again_within_window_even_asking_to_force(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _identified_detail()
    _mock_historical_reads(monkeypatch, detail, bootstrap=_recent_bootstrap())
    claim, runner, _user_ai, _admin_ai = _mock_search_machinery(monkeypatch)
    client = _historical_client(session, monkeypatch, role=UserRole.USER)

    for force in (False, True):
        response = _search(client, detail.offer.id, force=force)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "historical_price_recently_searched"
    claim.assert_not_called()
    runner.assert_not_called()


def test_dev_within_window_needs_confirmation_then_forces(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _identified_detail()
    _mock_historical_reads(monkeypatch, detail, bootstrap=_recent_bootstrap())
    claim, runner, user_ai, _admin_ai = _mock_search_machinery(monkeypatch)
    client = _historical_client(session, monkeypatch, role=UserRole.DEV)

    refused = _search(client, detail.offer.id)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "historical_price_force_required"
    assert refused.json()["error"]["details"]["search"]["next_allowed_at"]
    claim.assert_not_called()

    forced = _search(client, detail.offer.id, force=True)
    assert forced.status_code == 202
    assert claim.await_args.kwargs["force"] is True
    assert runner.await_args.kwargs["ai"] == "admin-dev-ai"
    user_ai.assert_not_called()


def test_search_refused_for_product_without_identity(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _detail()  # identity_key None
    _mock_historical_reads(monkeypatch, detail)
    claim, _runner, _user_ai, _admin_ai = _mock_search_machinery(monkeypatch)
    client = _historical_client(session, monkeypatch, role=UserRole.DEV)

    response = _search(client, detail.offer.id, force=True)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "historical_price_no_identity"
    claim.assert_not_called()


def test_search_race_never_starts_a_second_search(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _identified_detail()
    _mock_historical_reads(monkeypatch, detail)
    _claim, runner, _user_ai, _admin_ai = _mock_search_machinery(
        monkeypatch, claimed=None
    )
    client = _historical_client(session, monkeypatch, role=UserRole.USER)

    response = _search(client, detail.offer.id)

    assert response.status_code == 409
    runner.assert_not_called()


def test_dev_opens_offer_outside_own_missions_user_still_denied(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Furo da TASK-126: DEV vê a oferta na listagem "todos os usuários" e
    precisa conseguir abrir o detalhe; USER continua exatamente igual."""
    detail = _detail()
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=None),
    )
    dev_lookup = AsyncMock(return_value=detail)
    monkeypatch.setattr("app.webapp.offers_router.get_offer_detail_for_dev", dev_lookup)

    as_user = _historical_client(session, monkeypatch, role=UserRole.USER)
    denied = as_user.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())
    assert denied.status_code == 403
    dev_lookup.assert_not_called()

    as_dev = _historical_client(session, monkeypatch, role=UserRole.DEV)
    response = as_dev.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())
    assert response.status_code == 200
    assert response.json()["title"] == "Galaxy S24 Ultra"


# --- TASK-126 (furo): comparação e gráfico também abrem para DEV --------


def _comparison_of(detail: UserOfferDetail) -> UserOfferComparison:
    return UserOfferComparison(
        product=detail.product,
        offers=(
            UserComparisonOffer(
                detail.offer, detail.store, detail.seller, detail.observation, ()
            ),
        ),
    )


def _empty_history(product_id) -> OfferPriceHistory:
    return OfferPriceHistory(
        product_id=product_id,
        comparable=False,
        reason="unresolved_product_identity",
        period="1m",
        currency=None,
        period_from=None,
        period_to=NOW,
        series=(),
        metrics=None,
    )


@pytest.mark.parametrize(
    ("role", "expected_status"), [(UserRole.USER, 403), (UserRole.DEV, 200)]
)
def test_comparison_falls_back_to_dev_view_only_for_dev(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch, role, expected_status
) -> None:
    detail = _detail()
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_comparison_for_user",
        AsyncMock(return_value=None),
    )
    dev_lookup = AsyncMock(return_value=_comparison_of(detail))
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_comparison_for_dev", dev_lookup
    )
    client = _historical_client(session, monkeypatch, role=role)

    response = client.get(
        f"/api/v1/offers/{detail.offer.id}/comparison", cookies=_cookies()
    )

    assert response.status_code == expected_status
    if role is UserRole.DEV:
        assert response.json()["offers"][0]["id"] == str(detail.offer.id)
    else:
        dev_lookup.assert_not_called()


@pytest.mark.parametrize(
    ("role", "expected_status"), [(UserRole.USER, 403), (UserRole.DEV, 200)]
)
def test_price_history_falls_back_to_dev_view_only_for_dev(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch, role, expected_status
) -> None:
    detail = _detail()
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_price_history_for_user",
        AsyncMock(return_value=None),
    )
    dev_lookup = AsyncMock(return_value=_empty_history(detail.product.id))
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_price_history_for_dev", dev_lookup
    )
    client = _historical_client(session, monkeypatch, role=role)

    response = client.get(
        f"/api/v1/offers/{detail.offer.id}/price-history?period=1m",
        cookies=_cookies(),
    )

    assert response.status_code == expected_status
    if role is UserRole.DEV:
        assert response.json()["reason"] == "unresolved_product_identity"
        assert dev_lookup.await_args.kwargs["period"] == "1m"
    else:
        dev_lookup.assert_not_called()


def test_historical_price_denied_for_offer_the_user_cannot_see(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=None),
    )
    client = _historical_client(session, monkeypatch, role=UserRole.USER)

    response = client.get(
        f"/api/v1/offers/{uuid4()}/historical-price", cookies=_cookies()
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "offer_access_denied"
