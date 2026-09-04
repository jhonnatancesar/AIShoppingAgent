"""Contrato HTTP dos endpoints de missão da aplicação web (TASK-092, item
2 da V1.2).

Monta uma app FastAPI mínima (só o router + o handler de erro) e
sobrepõe `get_web_async_session` com um `AsyncMock`; `require_web_session`
é exercitado de verdade (não sobreposto) via mock de
`app.webapp.dependency.get_web_session_user`, para provar que sessão +
CSRF continuam protegendo estes endpoints exatamente como os de
`app.webapp.router` (mesma dependência, TASK-091/DEC-074)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.collection.contracts import OfferCondition
from app.core.errors import register_api_error_handler
from app.database.dependency import get_web_async_session
from app.missions.models import Mission, MissionStatus
from app.missions.query import MissionDetail, MissionListExtras
from app.missions.service import (
    MissionCreationError,
    MissionEditConditionError,
    MissionTransitionConditionError,
    MissionVersionConflictError,
)
from app.offers.models import Offer
from app.offers.query import MissionOfferLink
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.missions_router import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CSRF_TOKEN = "csrf-canary-token"


def _user() -> User:
    return User(
        id=uuid4(), display_name="Cliente Teste", role=UserRole.USER, username="cliente"
    )


def _mission(*, user_id, status=MissionStatus.ACTIVE) -> Mission:
    now = datetime.now(UTC)
    return Mission(
        id=uuid4(),
        user_id=user_id,
        title="RTX 5070 Ti",
        status=status,
        state_version=3,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )


def _build_app() -> FastAPI:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    return app


@pytest.fixture
def owner() -> User:
    return _user()


@pytest.fixture
def async_session() -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def client(
    async_session: MagicMock, owner: User, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    app = _build_app()
    app.dependency_overrides[get_web_async_session] = lambda: async_session
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: owner
    )
    return TestClient(app)


def _cookies(**extra: str) -> dict[str, str]:
    return {
        WEB_SESSION_COOKIE_NAME: "raw-token-canary",
        CSRF_COOKIE_NAME: _CSRF_TOKEN,
        **extra,
    }


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _CSRF_TOKEN}


# --- Autenticação / CSRF (herdados de require_web_session) -----------------


def test_list_missions_without_session_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/missions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_create_mission_without_csrf_is_403(client: TestClient) -> None:
    response = client.post(
        "/api/v1/missions",
        json={"search_query": "rtx 5070 ti"},
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_list_missions_does_not_require_csrf(
    client: TestClient, async_session: MagicMock
) -> None:
    async_session.scalars = AsyncMock(return_value=[])
    async_session.scalar = AsyncMock(return_value=0)

    response = client.get(
        "/api/v1/missions", cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"}
    )

    assert response.status_code == 200


# --- Criação -----------------------------------------------------------


def test_create_mission_success(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _mission(user_id=owner.id, status=MissionStatus.ACTIVE)
    monkeypatch.setattr(
        "app.webapp.missions_router.create_mission_from_criteria_async",
        AsyncMock(return_value=(created, ("pichau", "terabyte", "amazon", "kabum"))),
    )

    response = client.post(
        "/api/v1/missions",
        json={"search_query": "rtx 5070 ti", "source_codes": []},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(created.id)
    assert body["status"] == "active"


def test_create_mission_rejects_unpaired_target(client: TestClient) -> None:
    response = client.post(
        "/api/v1/missions",
        json={"search_query": "rtx 5070 ti", "target_amount": "100.00"},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 422


def test_create_mission_rejects_unknown_source_code(client: TestClient) -> None:
    """Achado (auditoria TASK-112 fase 3B): este teste usava "magalu" como
    fonte inválida -- desde a TASK-104A, `magalu` é uma fonte V1 real
    (`MISSION_SOURCE_CODES`), então já não testava mais rejeição nenhuma
    (defasagem já existente antes desta fase, não é regressão). `shopee`
    continua fora do escopo comercial da V1 (CLAUDE.md: "Futuro"), então
    é o valor certo para exercitar a validação de fonte desconhecida."""
    response = client.post(
        "/api/v1/missions",
        json={"search_query": "rtx 5070 ti", "source_codes": ["shopee"]},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 422


def test_create_mission_maps_creation_error_to_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fail(*args: object, **kwargs: object) -> None:
        raise MissionCreationError("stores not seeded for codes: pichau")

    monkeypatch.setattr(
        "app.webapp.missions_router.create_mission_from_criteria_async", _fail
    )

    response = client.post(
        "/api/v1/missions",
        json={"search_query": "rtx 5070 ti"},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "mission_creation_failed"
    assert "stores not seeded" not in response.text  # detalhe interno não vaza


# --- Listagem ------------------------------------------------------------


def test_list_missions_rejects_unknown_status_filter(
    client: TestClient, async_session: MagicMock
) -> None:
    response = client.get(
        "/api/v1/missions?status=draft",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_status_filter"


def test_list_missions_returns_envelope(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id)
    async_session.scalars = AsyncMock(return_value=[mission])
    async_session.scalar = AsyncMock(return_value=1)
    # Subtask 14: `list_missions` também chama `load_mission_list_extras`
    # (consultas próprias de MissionCriteria/MissionSource/
    # MissionOfferRelevance, cobertas de verdade só contra PostgreSQL real
    # em `tests/integration/test_webapp_missions_list_extras.py`) -- aqui,
    # teste de contrato HTTP, é mockada como camada, mesmo padrão já usado
    # para `get_mission_detail_for_user` neste arquivo.
    monkeypatch.setattr(
        "app.webapp.missions_router.load_mission_list_extras",
        AsyncMock(
            return_value={
                mission.id: MissionListExtras(
                    target_amount=None,
                    target_currency=None,
                    sources=(),
                    relevant_offer_count=0,
                ),
            }
        ),
    )

    response = client.get(
        "/api/v1/missions?status=active&limit=5&offset=0",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert body["items"][0]["id"] == str(mission.id)
    assert body["items"][0]["target_amount"] is None
    assert body["items"][0]["sources"] == []
    assert body["items"][0]["relevant_offer_count"] == 0


# --- Detalhe / posse -------------------------------------------------------


def test_get_mission_not_found_or_not_owned_is_403(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.missions_router.get_mission_detail_for_user",
        AsyncMock(return_value=None),
    )

    response = client.get(
        f"/api/v1/missions/{uuid4()}",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "mission_access_denied"


def test_get_mission_exposes_links_to_relevant_offers(
    client: TestClient, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission = _mission(user_id=owner.id)
    detail = MissionDetail(mission, None, [], None, [])
    store = Store(
        id=uuid4(), code="pichau", name="Pichau", base_url="https://pichau.com.br"
    )
    product = Product(id=uuid4(), name="RTX 5070 Ti")
    offer = Offer(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        url="https://pichau.com.br/produto",
        last_seen_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        "app.webapp.missions_router.get_mission_detail_for_user",
        AsyncMock(return_value=detail),
    )
    monkeypatch.setattr(
        "app.webapp.missions_router.list_current_offer_links_for_mission",
        AsyncMock(
            return_value=(
                MissionOfferLink(offer, product, store, condition=OfferCondition.USED),
            )
        ),
    )

    response = client.get(
        f"/api/v1/missions/{mission.id}",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 200
    assert response.json()["offers"] == [
        {
            "id": str(offer.id),
            "title": product.name,
            "store_code": "pichau",
            "store_name": "Pichau",
            "last_seen_at": offer.last_seen_at.isoformat(),
            "condition": "used",
        }
    ]


def test_get_mission_offer_link_condition_is_none_without_observation(
    client: TestClient, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subtask 3 (auditoria GG Oferta): oferta recém-descoberta, ainda sem
    nenhuma `PriceObservation` -- a API nunca inventa uma condição, expõe
    `null` explicitamente (nunca confundido com `unknown`, que significa
    'houve observação, mas sem evidência de condição')."""
    mission = _mission(user_id=owner.id)
    detail = MissionDetail(mission, None, [], None, [])
    store = Store(
        id=uuid4(), code="pichau", name="Pichau", base_url="https://pichau.com.br"
    )
    product = Product(id=uuid4(), name="RTX 5070 Ti")
    offer = Offer(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        url="https://pichau.com.br/produto",
        last_seen_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        "app.webapp.missions_router.get_mission_detail_for_user",
        AsyncMock(return_value=detail),
    )
    monkeypatch.setattr(
        "app.webapp.missions_router.list_current_offer_links_for_mission",
        AsyncMock(
            return_value=(MissionOfferLink(offer, product, store, condition=None),)
        ),
    )

    response = client.get(
        f"/api/v1/missions/{mission.id}",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 200
    assert response.json()["offers"][0]["condition"] is None


def test_select_multiple_family_variants_uses_owned_mission(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id)
    product_ids = [uuid4(), uuid4()]
    async_session.get = AsyncMock(return_value=mission)
    selection = AsyncMock(return_value=())
    monkeypatch.setattr(
        "app.webapp.missions_router.set_mission_product_selection_async", selection
    )

    response = client.put(
        f"/api/v1/missions/{mission.id}/variants",
        json={
            "expected_state_version": mission.state_version,
            "product_ids": [str(item) for item in product_ids],
        },
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 200
    selection.assert_awaited_once()
    call = selection.await_args.kwargs
    assert call["user_id"] == owner.id
    assert call["mission_id"] == mission.id
    assert call["product_ids"] == tuple(product_ids)
    assert call["select_all"] is False


def test_select_family_variants_of_another_user_is_denied(
    client: TestClient, async_session: MagicMock
) -> None:
    mission = _mission(user_id=uuid4())
    async_session.get = AsyncMock(return_value=mission)

    response = client.put(
        f"/api/v1/missions/{mission.id}/variants",
        json={"expected_state_version": 3, "select_all": True},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 403


def test_pause_mission_owned_by_another_user_is_403(
    client: TestClient, async_session: MagicMock
) -> None:
    other_users_mission = _mission(user_id=uuid4())
    async_session.get = AsyncMock(return_value=other_users_mission)

    response = client.post(
        f"/api/v1/missions/{other_users_mission.id}/pause",
        json={"expected_state_version": 3},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "mission_access_denied"


def test_pause_mission_nonexistent_is_403_not_404(
    client: TestClient, async_session: MagicMock
) -> None:
    """Mesma convenção do domínio inteiro: nunca distinguir "não existe"
    de "não é sua" (evita enumeração de IDs de missão de terceiros)."""
    async_session.get = AsyncMock(return_value=None)

    response = client.post(
        f"/api/v1/missions/{uuid4()}/pause",
        json={"expected_state_version": 0},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "mission_access_denied"


# --- Transições (pausar/retomar/cancelar) ----------------------------------


def test_pause_mission_success(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id, status=MissionStatus.ACTIVE)
    paused_mission = _mission(user_id=owner.id, status=MissionStatus.PAUSED)
    async_session.get = AsyncMock(side_effect=[mission, paused_mission])
    monkeypatch.setattr(
        "app.webapp.missions_router.transition_mission_async",
        AsyncMock(return_value=MagicMock()),
    )

    response = client.post(
        f"/api/v1/missions/{mission.id}/pause",
        json={"expected_state_version": mission.state_version},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paused"


def test_cancel_mission_version_conflict_is_409(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id, status=MissionStatus.ACTIVE)
    async_session.get = AsyncMock(return_value=mission)

    async def _fail(*args: object, **kwargs: object) -> None:
        raise MissionVersionConflictError("Versão de estado desatualizada.")

    monkeypatch.setattr("app.webapp.missions_router.transition_mission_async", _fail)

    response = client.post(
        f"/api/v1/missions/{mission.id}/cancel",
        json={"expected_state_version": 0},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_version_conflict"


def test_resume_mission_condition_error_is_409(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id, status=MissionStatus.PAUSED)
    async_session.get = AsyncMock(return_value=mission)

    async def _fail(*args: object, **kwargs: object) -> None:
        raise MissionTransitionConditionError(
            "Uma missão expirada não pode ser retomada."
        )

    monkeypatch.setattr("app.webapp.missions_router.transition_mission_async", _fail)

    response = client.post(
        f"/api/v1/missions/{mission.id}/resume",
        json={"expected_state_version": mission.state_version},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_transition_rejected"


def test_pause_mission_without_csrf_is_403(
    client: TestClient, async_session: MagicMock, owner: User
) -> None:
    mission = _mission(user_id=owner.id)
    async_session.get = AsyncMock(return_value=mission)

    response = client.post(
        f"/api/v1/missions/{mission.id}/pause",
        json={"expected_state_version": mission.state_version},
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


# --- Edição ------------------------------------------------------------


def test_edit_mission_requires_paused_state_maps_to_409(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id, status=MissionStatus.ACTIVE)
    async_session.get = AsyncMock(return_value=mission)

    async def _fail(*args: object, **kwargs: object) -> None:
        raise MissionEditConditionError(
            "Só é possível editar uma missão pausada. Pause a missão primeiro."
        )

    monkeypatch.setattr("app.webapp.missions_router.edit_mission_criteria", _fail)

    response = client.patch(
        f"/api/v1/missions/{mission.id}",
        json={"expected_state_version": mission.state_version, "clear_target": True},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_transition_rejected"


def test_edit_mission_requires_at_least_one_field(client: TestClient) -> None:
    response = client.patch(
        f"/api/v1/missions/{uuid4()}",
        json={"expected_state_version": 0},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 422


def test_edit_mission_success(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id, status=MissionStatus.PAUSED)
    async_session.get = AsyncMock(return_value=mission)
    monkeypatch.setattr(
        "app.webapp.missions_router.edit_mission_criteria",
        AsyncMock(return_value=(mission, ("pichau",))),
    )

    response = client.patch(
        f"/api/v1/missions/{mission.id}",
        json={
            "expected_state_version": mission.state_version,
            "source_codes": ["pichau"],
        },
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 200


def test_edit_mission_without_csrf_is_403(
    client: TestClient, async_session: MagicMock, owner: User
) -> None:
    """Ponto 16 da auditoria: PATCH herda sessão + CSRF de
    `require_web_session` do mesmo jeito que POST/DELETE -- nenhum
    mecanismo próprio."""
    mission = _mission(user_id=owner.id, status=MissionStatus.PAUSED)
    async_session.get = AsyncMock(return_value=mission)

    response = client.patch(
        f"/api/v1/missions/{mission.id}",
        json={"expected_state_version": mission.state_version, "clear_target": True},
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_edit_mission_without_session_is_401(client: TestClient) -> None:
    """`401` precede `403 csrf_invalid` -- sessão é resolvida antes de
    qualquer checagem de CSRF (mesma ordem de `require_web_session`,
    TASK-091)."""
    response = client.patch(
        f"/api/v1/missions/{uuid4()}",
        json={"expected_state_version": 0, "clear_target": True},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


# --- Política de posse: inexistente x pertence a outro usuário (ponto 2) --
#
# As duas situações precisam produzir uma resposta idêntica -- nunca
# distinguir "não existe" de "não é sua" (evita enumeração de IDs de
# missão de terceiros). O padrão (403, não 404) já era o único usado no
# canal web antes desta TASK (`require_admin_web_session`, TASK-091) --
# não é uma política nova, é a mesma reaproveitada.


def test_get_mission_missing_and_not_owned_are_identical(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.missions_router.get_mission_detail_for_user",
        AsyncMock(return_value=None),
    )

    missing = client.get(
        f"/api/v1/missions/{uuid4()}",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )
    not_owned = client.get(
        f"/api/v1/missions/{uuid4()}",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert missing.status_code == not_owned.status_code == 403
    assert missing.json() == not_owned.json()
    assert missing.json()["error"]["code"] == "mission_access_denied"


def test_edit_mission_missing_and_not_owned_are_identical(
    client: TestClient, async_session: MagicMock
) -> None:
    body = {"expected_state_version": 0, "clear_target": True}

    async_session.get = AsyncMock(return_value=None)  # inexistente
    missing = client.patch(
        f"/api/v1/missions/{uuid4()}",
        json=body,
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    async_session.get = AsyncMock(
        return_value=_mission(user_id=uuid4())
    )  # de outro usuário
    not_owned = client.patch(
        f"/api/v1/missions/{uuid4()}",
        json=body,
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert missing.status_code == not_owned.status_code == 403
    assert missing.json() == not_owned.json()
    assert missing.json()["error"]["code"] == "mission_access_denied"


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_mission_command_missing_and_not_owned_are_identical(
    client: TestClient, async_session: MagicMock, action: str
) -> None:
    body = {"expected_state_version": 0}

    async_session.get = AsyncMock(return_value=None)  # inexistente
    missing = client.post(
        f"/api/v1/missions/{uuid4()}/{action}",
        json=body,
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    async_session.get = AsyncMock(
        return_value=_mission(user_id=uuid4())
    )  # de outro usuário
    not_owned = client.post(
        f"/api/v1/missions/{uuid4()}/{action}",
        json=body,
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert missing.status_code == not_owned.status_code == 403
    assert missing.json() == not_owned.json()
    assert missing.json()["error"]["code"] == "mission_access_denied"


# --- Máquina de estados: cancelamento é terminal no backend (ponto 10) ----


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_terminal_mission_rejects_lifecycle_commands(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """A confirmação de cancelamento no React é só UX -- o backend precisa
    continuar recusando uma transição que o domínio não permite, mesmo
    que alguém chame o endpoint direto sem passar pela SPA."""
    from app.missions.service import InvalidMissionTransitionError

    mission = _mission(user_id=owner.id, status=MissionStatus.CANCELLED)
    async_session.get = AsyncMock(return_value=mission)

    async def _fail(*args: object, **kwargs: object) -> None:
        raise InvalidMissionTransitionError(
            f"Comando {action} inválido para o estado cancelled."
        )

    monkeypatch.setattr("app.webapp.missions_router.transition_mission_async", _fail)

    response = client.post(
        f"/api/v1/missions/{mission.id}/{action}",
        json={"expected_state_version": mission.state_version},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_transition_rejected"


def test_edit_rejected_on_cancelled_mission(
    client: TestClient,
    async_session: MagicMock,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(user_id=owner.id, status=MissionStatus.CANCELLED)
    async_session.get = AsyncMock(return_value=mission)

    async def _fail(*args: object, **kwargs: object) -> None:
        raise MissionEditConditionError(
            "Só é possível editar uma missão pausada. Pause a missão primeiro."
        )

    monkeypatch.setattr("app.webapp.missions_router.edit_mission_criteria", _fail)

    response = client.patch(
        f"/api/v1/missions/{mission.id}",
        json={"expected_state_version": mission.state_version, "clear_target": True},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_transition_rejected"


# --- Contrato de filtro de status (ponto 1) --------------------------------


@pytest.mark.parametrize(
    "status_param",
    ["active", "paused", "cancelled", "completed", "expired", "all"],
)
def test_list_missions_accepts_every_documented_status_filter(
    client: TestClient, async_session: MagicMock, status_param: str
) -> None:
    async_session.scalars = AsyncMock(return_value=[])
    async_session.scalar = AsyncMock(return_value=0)

    response = client.get(
        f"/api/v1/missions?status={status_param}",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 200


def test_list_missions_enforces_max_limit(
    client: TestClient, async_session: MagicMock
) -> None:
    async_session.scalars = AsyncMock(return_value=[])
    async_session.scalar = AsyncMock(return_value=0)

    response = client.get(
        "/api/v1/missions?limit=1000",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 422  # acima do limite máximo documentado
