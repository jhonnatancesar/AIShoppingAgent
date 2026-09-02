import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.ai_provider import AIProviderQuotaExceeded, AIProviderUnavailable
from app.core.config import Settings
from app.feedback.models import FeedbackChannel, FeedbackKind
from app.intent import Intent, IntentKind, IntentParameters
from app.main import app
from app.missions.models import MissionStatus
from app.missions.query import MissionReferenceError
from app.missions.service import MissionCreationError, MissionTransitionConditionError
from app.quotas import QuotaExceededError, QuotaKind
from app.telegram.confirmation import ConfirmationError
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.telegram.limits import TelegramUpdateReservation
from app.telegram.models import TelegramUpdateDisposition
from app.telegram.router import (
    TelegramUpdate,
    _apply_store_suggestion_name,
    _apply_support_description,
    _dispatch_intent,
    _handle_message,
    _resolve_pending_intent,
    _TelegramChat,
    _TelegramIncomingMessage,
    _TelegramSender,
    receive_telegram_webhook,
)
from app.users.models import UserRole

from backend.scripts.register_telegram_commands import _COMMANDS


def _async_session() -> MagicMock:
    """`AsyncSession` simulada -- extensão da TASK-079: `scalar`/`scalars`/
    `execute`/`get`/`flush`/`commit`/`rollback` são awaitables; `add`
    continua síncrono, como na `AsyncSession` real."""
    session = MagicMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def _reserve_update_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_reserve(
        *args: object, **kwargs: object
    ) -> TelegramUpdateReservation:
        return TelegramUpdateReservation(TelegramUpdateDisposition.ACCEPTED)

    monkeypatch.setattr("app.telegram.router.reserve_telegram_update", _fake_reserve)


@pytest.fixture(autouse=True)
def _user_serialization_lock_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extensão da TASK-079: estes testes chamam `receive_telegram_webhook`
    direto (sem passar por `Depends`), então `engine` nunca é um
    `AsyncEngine` real -- o advisory lock em si é coberto pelos testes de
    integração reais (`tests/integration/test_telegram_webhook.py`)."""

    @asynccontextmanager
    async def _fake_lock(*args: object, **kwargs: object):
        yield

    monkeypatch.setattr("app.telegram.router.user_serialization_lock", _fake_lock)


class _FakeAdapter:
    def __init__(self, outcome: Intent | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[TelegramMessage, UserRole]] = []
        self.manager = object()

    async def interpret(
        self, message: TelegramMessage, *, profile: UserRole = UserRole.USER
    ) -> Intent:
        self.calls.append((message, profile))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _patch_resolve_answer(
    monkeypatch: pytest.MonkeyPatch, outcome: bool | Exception
) -> None:
    async def _fake_resolve_answer(text: str, **kwargs: object):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("app.telegram.router.resolve_answer", _fake_resolve_answer)


def _fake_user(
    *,
    role: object = UserRole.USER,
    registration_step: str | None = None,
    pending_intent: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        role=role,
        telegram_user_id=222,
        telegram_chat_id=None,
        notify_price_decreases=True,
        notify_target_reached=True,
        is_active=True,
        registration_step=registration_step,
        pending_intent=pending_intent,
        username=None,
        email=None,
        favorite_stores=[],
        preferred_categories=[],
    )


def _awaiting_mission_description() -> dict[str, str]:
    return {
        "kind": "await_create_mission_description",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }


@pytest.mark.anyio
async def test_family_variant_numbers_select_multiple_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_ids = (uuid4(), uuid4())
    products = tuple(
        SimpleNamespace(id=product_id, display_name=label, name=label)
        for product_id, label in zip(product_ids, ("iPhone 17", "iPhone 17 Pro"))
    )
    user = _fake_user(
        pending_intent={
            "kind": "await_mission_variants",
            "event_id": str(uuid4()),
            "mission_id": str(uuid4()),
            "mission_title": "iPhone 17",
            "expected_state_version": 2,
            "variants": [
                {"product_id": str(product.id), "label": product.display_name}
                for product in products
            ],
        }
    )
    select_variants = AsyncMock(return_value=products)
    monkeypatch.setattr(
        "app.telegram.router.set_mission_product_selection_async", select_variants
    )

    response = await _resolve_pending_intent(
        TelegramMessage(
            chat_id=222,
            chat_type=TelegramChatType.PRIVATE,
            user_id=222,
            text="1,2",
            received_at=datetime.now(UTC),
        ),
        adapters={},
        session=_async_session(),
        user=user,
    )

    assert response == "✅ Variantes selecionadas: iPhone 17, iPhone 17 Pro."
    assert select_variants.await_args.kwargs["product_ids"] == product_ids
    assert user.pending_intent is None


def _adapters(adapter: _FakeAdapter, *, role: UserRole = UserRole.USER) -> dict:
    return {role: adapter}


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "telegram_webhook_secret": "correct-secret",
        "telegram_bot_token": "bot-token",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _update(**overrides: object) -> TelegramUpdate:
    defaults: dict[str, object] = {
        "update_id": 1,
        "message": _TelegramIncomingMessage(
            text="Quero um notebook até R$ 5000",
            date=1754586000,
            chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
            from_=_TelegramSender(id=222, first_name="Fulano"),
        ),
    }
    defaults.update(overrides)
    return TelegramUpdate(**defaults)  # type: ignore[arg-type]


def _message(text: str, *, user_id: int = 222) -> TelegramMessage:
    return TelegramMessage(
        chat_id=user_id,
        chat_type=TelegramChatType.PRIVATE,
        user_id=user_id,
        text=text,
        received_at=datetime.now(UTC),
    )


def _registered_user(**overrides: object) -> SimpleNamespace:
    user = _fake_user(**overrides)
    user.username = "cliente"
    return user


def _intent(**overrides: object) -> Intent:
    defaults: dict[str, object] = {
        "correlation_id": uuid4(),
        "kind": IntentKind.CREATE_MISSION,
        "raw_message": "Quero um notebook até R$ 5000",
        "interpreted_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Intent(**defaults)  # type: ignore[arg-type]


def _patch_user(
    monkeypatch: pytest.MonkeyPatch,
    user: SimpleNamespace,
    *,
    created_now: bool = False,
) -> list[tuple[int, str]]:
    resolve_calls: list[tuple[int, str]] = []

    async def _fake_get_or_create(
        session: object, *, telegram_user_id: int, display_name: str
    ) -> tuple[SimpleNamespace, bool]:
        resolve_calls.append((telegram_user_id, display_name))
        return user, created_now

    monkeypatch.setattr(
        "app.telegram.authentication.get_or_create_telegram_user_async",
        _fake_get_or_create,
    )
    return resolve_calls


def _patch_send_message(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    send_calls: list[tuple[int, str]] = []

    async def _fake_send_message(
        chat_id: int, text: str, *, bot_token: object, **kwargs: object
    ) -> None:
        send_calls.append((chat_id, text))

    monkeypatch.setattr("app.telegram.router.send_message", _fake_send_message)
    return send_calls


@pytest.mark.anyio
async def test_valid_secret_and_text_message_returns_204_and_calls_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(
        monkeypatch,
        _fake_user(pending_intent=_awaiting_mission_description()),
    )
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(telegram_bot_token=None),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1
    message, profile = adapter.calls[0]
    assert message.chat_id == 222
    assert message.user_id == 222
    assert message.text == "Quero um notebook até R$ 5000"
    assert message.received_at == datetime.fromtimestamp(1754586000, tz=UTC)
    assert profile is UserRole.USER


@pytest.mark.anyio
async def test_privacy_command_is_static_and_does_not_require_ai_or_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _fake_user()
    _patch_user(monkeypatch, user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(AssertionError("AI must not be called"))

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/privacidade",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Pessoa"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert len(send_calls) == 1
    assert "UUID interno pseudônimo" in send_calls[0][1]
    assert "anonimização irreversível" in send_calls[0][1]


@pytest.mark.anyio
async def test_admin_user_uses_admin_dev_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(
        monkeypatch,
        _fake_user(
            role=UserRole.ADMIN,
            pending_intent=_awaiting_mission_description(),
        ),
    )
    _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter, role=UserRole.ADMIN),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1] is UserRole.ADMIN


@pytest.mark.anyio
@pytest.mark.parametrize("token", [None, "wrong-secret"])
async def test_missing_or_wrong_secret_returns_401_without_calling_adapter(
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
) -> None:
    resolve_calls = _patch_user(monkeypatch, _fake_user())
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token=token,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 401
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "telegram_webhook_unauthorized"
    assert resolve_calls == []
    assert adapter.calls == []


@pytest.mark.anyio
async def test_unconfigured_secret_rejects_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_calls = _patch_user(monkeypatch, _fake_user())
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="anything",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(telegram_webhook_secret=None),
        session=_async_session(),
    )

    assert response.status_code == 401
    assert resolve_calls == []
    assert adapter.calls == []


@pytest.mark.anyio
async def test_replayed_update_is_a_no_op_without_calling_adapter_or_replying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extensão da TASK-079: replay é detectado dentro da Fase A (banco),
    depois de já ter comitado -- a requisição termina aí, sem IA nem
    resposta ao Telegram."""
    _patch_user(
        monkeypatch,
        _fake_user(pending_intent=_awaiting_mission_description()),
    )

    async def _fake_reserve_replay(
        *args: object, **kwargs: object
    ) -> TelegramUpdateReservation:
        return TelegramUpdateReservation(
            TelegramUpdateDisposition.ACCEPTED, replay=True
        )

    monkeypatch.setattr(
        "app.telegram.router.reserve_telegram_update", _fake_reserve_replay
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert send_calls == []


@pytest.mark.anyio
async def test_authorization_denied_replay_skips_denial_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quando a reserva forçada como `DISCARDED` (após negação de
    `TELEGRAM_INTERACT`) indica que este `update_id` já havia sido
    reservado antes (replay -- ex.: reentrega do Telegram), a requisição
    termina no aviso de replay, sem chamar `_log_authorization_denial`
    de novo."""
    fake_user = _fake_user(role="OWNER")
    _patch_user(monkeypatch, fake_user)

    async def _fake_reserve_replay(
        *args: object, **kwargs: object
    ) -> TelegramUpdateReservation:
        return TelegramUpdateReservation(
            TelegramUpdateDisposition.DISCARDED, replay=True
        )

    monkeypatch.setattr(
        "app.telegram.router.reserve_telegram_update", _fake_reserve_replay
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent(kind=IntentKind.QUERY_MISSION))
    session = _async_session()

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert send_calls == []
    session.add.assert_called_once()  # auditoria da 1a negação, não desta


@pytest.mark.anyio
async def test_rate_limited_update_warns_once_and_skips_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(
        monkeypatch,
        _fake_user(pending_intent=_awaiting_mission_description()),
    )

    async def _fake_reserve_rate_limited(
        *args: object, **kwargs: object
    ) -> TelegramUpdateReservation:
        return TelegramUpdateReservation(
            TelegramUpdateDisposition.RATE_LIMITED, warn_rate_limit=True
        )

    monkeypatch.setattr(
        "app.telegram.router.reserve_telegram_update", _fake_reserve_rate_limited
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert len(send_calls) == 1
    assert "Muitas mensagens" in send_calls[0][1]


@pytest.mark.anyio
async def test_rate_limited_update_without_warning_does_not_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Janelas de rate limit subsequentes na mesma janela de 1 minuto não
    repetem o aviso (`warn_rate_limit=False`)."""
    _patch_user(monkeypatch, _fake_user())

    async def _fake_reserve_rate_limited(
        *args: object, **kwargs: object
    ) -> TelegramUpdateReservation:
        return TelegramUpdateReservation(
            TelegramUpdateDisposition.RATE_LIMITED, warn_rate_limit=False
        )

    monkeypatch.setattr(
        "app.telegram.router.reserve_telegram_update", _fake_reserve_rate_limited
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert send_calls == []


@pytest.mark.anyio
async def test_deadline_exceeded_cancels_processing_and_returns_204(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Extensão da TASK-079: `telegram_message_deadline_seconds` limita o
    processamento inteiro -- uma IA que nunca responde não trava a
    requisição para sempre nem o loop de eventos."""
    _patch_user(
        monkeypatch,
        _fake_user(pending_intent=_awaiting_mission_description()),
    )
    send_calls = _patch_send_message(monkeypatch)

    class _HangingAdapter:
        manager = object()

        async def interpret(self, message: object, *, profile: object) -> Intent:
            import asyncio

            await asyncio.sleep(10)
            raise AssertionError("must have timed out before this point")

    with caplog.at_level("ERROR", logger="app.telegram"):
        response = await receive_telegram_webhook(
            update=_update(),
            x_telegram_bot_api_secret_token="correct-secret",
            adapters=_adapters(_HangingAdapter()),  # type: ignore[arg-type]
            settings=_settings(telegram_message_deadline_seconds=0.05),
            session=_async_session(),
        )

    assert response.status_code == 204
    assert send_calls == []
    assert caplog.records[-1].message == "telegram_webhook_deadline_exceeded"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("chat_type", "reason"),
    [
        (TelegramChatType.GROUP, "non_private_chat"),
        (TelegramChatType.SUPERGROUP, "non_private_chat"),
        (TelegramChatType.CHANNEL, "non_private_chat"),
        (TelegramChatType.PRIVATE, "identity_mismatch"),
    ],
)
async def test_rejected_telegram_identity_is_sanitized_no_op(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    chat_type: TelegramChatType,
    reason: str,
) -> None:
    user = _fake_user()
    resolve_calls = _patch_user(monkeypatch, user)
    send_calls = _patch_send_message(monkeypatch)
    remember = MagicMock(side_effect=AssertionError("must not remember chat"))
    monkeypatch.setattr(
        "app.telegram.router.remember_private_notification_chat", remember
    )
    adapter = _FakeAdapter(_intent())

    with caplog.at_level("WARNING", logger="app.telegram"):
        response = await receive_telegram_webhook(
            update=_update(
                message=_TelegramIncomingMessage(
                    text="segredo-canario",
                    date=1754586000,
                    chat=_TelegramChat(id=111, type=chat_type),
                    from_=_TelegramSender(id=222, first_name="Fulano"),
                )
            ),
            x_telegram_bot_api_secret_token="correct-secret",
            adapters=_adapters(adapter),  # type: ignore[arg-type]
            settings=_settings(),
            session=_async_session(),
        )

    assert response.status_code == 204
    assert resolve_calls == []
    assert adapter.calls == []
    assert send_calls == []
    remember.assert_not_called()
    record = caplog.records[-1]
    assert record.message == "telegram_authentication_rejected"
    assert record.authentication_reason == reason  # type: ignore[attr-defined]
    assert "111" not in record.getMessage()
    assert "222" not in record.getMessage()
    assert "segredo-canario" not in record.getMessage()


@pytest.mark.anyio
async def test_inactive_user_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _fake_user()
    user.is_active = False
    resolve_calls = _patch_user(monkeypatch, user)
    send_calls = _patch_send_message(monkeypatch)
    remember = MagicMock(side_effect=AssertionError("must not remember chat"))
    monkeypatch.setattr(
        "app.telegram.router.remember_private_notification_chat", remember
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert resolve_calls == [(222, "Fulano")]
    assert adapter.calls == []
    assert send_calls == []
    remember.assert_not_called()
    assert user.telegram_chat_id is None


@pytest.mark.anyio
async def test_update_without_message_is_a_no_op_204() -> None:
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(message=None),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []


@pytest.mark.anyio
async def test_message_without_text_is_a_no_op_204() -> None:
    adapter = _FakeAdapter(_intent())
    update = _update(
        message=_TelegramIncomingMessage(
            text=None,
            date=1754586000,
            chat=_TelegramChat(id=111, type=TelegramChatType.GROUP),
            from_=_TelegramSender(id=222, first_name="Fulano"),
        )
    )

    response = await receive_telegram_webhook(
        update=update,
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("error", [AIProviderQuotaExceeded(), AIProviderUnavailable()])
async def test_ai_provider_failure_still_returns_204(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _patch_user(
        monkeypatch,
        _fake_user(pending_intent=_awaiting_mission_description()),
    )
    adapter = _FakeAdapter(error)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1


def test_telegram_webhook_route_is_exposed_in_openapi() -> None:
    operation = app.openapi()["paths"]["/telegram/webhook"]["post"]

    assert operation["tags"] == ["telegram"]
    assert operation["operationId"] == "receive_telegram_webhook"
    assert operation["summary"] == "Receber atualização do Telegram"
    assert (
        operation["responses"]["204"]["description"]
        == "Atualização autenticada e processada."
    )


@pytest.mark.anyio
async def test_create_mission_intent_stages_confirmation_without_creating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(pending_intent=_awaiting_mission_description())
    create_calls: list[dict[str, object]] = []

    resolve_calls = _patch_user(monkeypatch, fake_user)

    async def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        raise AssertionError("create_mission_from_criteria should not run yet")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _fake_create_mission
    )
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(
        kind=IntentKind.CREATE_MISSION,
        parameters=IntentParameters(
            search_query="notebook gamer",
            target_amount=Decimal("5000.00"),
            target_currency="BRL",
            sources=("pichau", "kabum"),
        ),
    )
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert resolve_calls == [(222, "Fulano")]
    assert create_calls == []
    assert fake_user.pending_intent == {
        "kind": "create_mission",
        "search_query": "notebook gamer",
        "model": None,
        "display_query": None,
        "target_amount": "5000.00",
        "target_currency": "BRL",
        "sources": ["pichau", "kabum"],
    }
    assert send_calls[0][0] == 222
    assert "notebook gamer" in send_calls[0][1]
    assert "sim" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_confirmed_pending_create_mission_executes_and_clears_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": "5000.00",
            "target_currency": "BRL",
            "sources": ["pichau", "kabum"],
        }
    )
    fake_mission = SimpleNamespace(title="notebook gamer")
    create_calls: list[dict[str, object]] = []

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    async def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        return fake_mission, ("pichau", "kabum")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _fake_create_mission
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    # confirmação usa um classificador de IA próprio, não o IntentInterpreter
    assert adapter.calls == []
    assert create_calls[0]["search_query"] == "notebook gamer"
    assert create_calls[0]["source_codes"] == ("pichau", "kabum")
    assert fake_user.pending_intent is None
    assert "notebook gamer" in send_calls[0][1]


@pytest.mark.anyio
async def test_confirmed_pending_create_mission_uses_display_query_as_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-083 (correção de regressão): `display_query` do `pending_intent`
    vira `title` na criação real -- `search_query` operacional (o que foi
    pesquisado nas lojas) continua intocado."""
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "9950X3D",
            "model": "9950X3D",
            "display_query": "AMD Ryzen 9 9950X3D",
            "target_amount": None,
            "target_currency": None,
            "sources": ["kabum"],
        }
    )
    fake_mission = SimpleNamespace(title="AMD Ryzen 9 9950X3D")
    create_calls: list[dict[str, object]] = []

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    async def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        return fake_mission, ("kabum",)

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _fake_create_mission
    )
    _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert create_calls[0]["search_query"] == "9950X3D"
    assert create_calls[0]["title"] == "AMD Ryzen 9 9950X3D"


@pytest.mark.anyio
async def test_cancelled_pending_create_mission_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, False)

    async def _fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("create_mission_from_criteria should not run")

    monkeypatch.setattr("app.telegram.router.create_mission_from_criteria_async", _fail)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="não",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent is None
    assert "não vou criar essa missão" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_create_mission_without_sources_stages_source_selection_and_preserves_criteria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(pending_intent=_awaiting_mission_description())
    create_calls: list[dict[str, object]] = []

    _patch_user(monkeypatch, fake_user)

    async def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        raise AssertionError("create_mission_from_criteria should not run yet")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _fake_create_mission
    )
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(
        kind=IntentKind.CREATE_MISSION,
        parameters=IntentParameters(
            search_query="notebook gamer",
            target_amount=Decimal("5000.00"),
            target_currency="BRL",
        ),
    )
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert create_calls == []
    assert fake_user.pending_intent == {
        "kind": "await_create_mission_sources",
        "search_query": "notebook gamer",
        "model": None,
        "display_query": None,
        "target_amount": "5000.00",
        "target_currency": "BRL",
    }
    reply = send_calls[0][1]
    assert "1 — Pichau" in reply
    assert "2 — Terabyte" in reply
    assert "3 — Amazon" in reply
    assert "4 — Kabum" in reply
    assert "5 — Magalu" in reply
    assert "6 — Mercado Livre" in reply
    assert "7 — Todas" in reply


@pytest.mark.anyio
async def test_valid_source_selection_answer_advances_to_normal_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_create_mission_sources",
            "search_query": "notebook gamer",
            "target_amount": "5000.00",
            "target_currency": "BRL",
        }
    )
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1,4",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    # a resposta numérica nunca passa pelo IntentInterpreter
    assert adapter.calls == []
    assert fake_user.pending_intent == {
        "kind": "create_mission",
        "search_query": "notebook gamer",
        "model": None,
        "display_query": None,
        "target_amount": "5000.00",
        "target_currency": "BRL",
        "sources": ["pichau", "kabum"],
    }
    reply = send_calls[0][1]
    assert "Pichau, Kabum" in reply
    assert "sim" in reply.lower()


@pytest.mark.anyio
async def test_invalid_source_selection_answer_keeps_pending_state_and_asks_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "kind": "await_create_mission_sources",
        "search_query": "notebook gamer",
        "target_amount": None,
        "target_currency": None,
    }
    fake_user = _fake_user(pending_intent=dict(pending))
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1,9",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent == pending
    assert "Não entendi essa opção" in send_calls[0][1]


@pytest.mark.anyio
async def test_full_flow_from_empty_sources_to_created_mission_only_after_valid_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-070: encena -> pede lojas -> só cria a missão depois de uma
    seleção válida e da confirmação normal. Nunca cria uma segunda missão
    nem passa pelo `IntentInterpreter` de novo nos passos intermediários."""
    fake_user = _fake_user(pending_intent=_awaiting_mission_description())
    fake_mission = SimpleNamespace(title="notebook gamer")
    create_calls: list[dict[str, object]] = []

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    async def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        return fake_mission, ("pichau", "kabum")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _fake_create_mission
    )
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(
        kind=IntentKind.CREATE_MISSION,
        parameters=IntentParameters(search_query="notebook gamer"),
    )
    adapter = _FakeAdapter(intent)

    # 1) sem lojas -> encena a pergunta, não cria nada.
    await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )
    assert fake_user.pending_intent["kind"] == "await_create_mission_sources"
    assert create_calls == []

    # 2) seleção válida -> avança para a confirmação normal, não cria ainda.
    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1,4",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )
    assert fake_user.pending_intent["kind"] == "create_mission"
    assert fake_user.pending_intent["sources"] == ["pichau", "kabum"]
    assert create_calls == []

    # 3) confirmação sim/não -> só agora cria a missão.
    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert len(create_calls) == 1
    assert create_calls[0]["source_codes"] == ("pichau", "kabum")
    assert fake_user.pending_intent is None
    # o IntentInterpreter só foi chamado uma vez, no passo 1.
    assert len(adapter.calls) == 1
    assert "notebook gamer" in send_calls[-1][1]


@pytest.mark.anyio
async def test_unrecognized_answer_to_pending_intent_keeps_it_staged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "kind": "create_mission",
        "search_query": "notebook gamer",
        "target_amount": None,
        "target_currency": None,
        "sources": [],
    }
    fake_user = _fake_user(pending_intent=pending)
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, ConfirmationError("Não entendi. Tente de novo."))
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="talvez",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent == pending
    assert "não entendi" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_create_mission_intent_without_search_query_is_a_known_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(
        monkeypatch,
        _fake_user(pending_intent=_awaiting_mission_description()),
    )
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(kind=IntentKind.CREATE_MISSION)  # sem parameters.search_query
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert send_calls  # respondeu explicando o problema, sem propagar exceção


@pytest.mark.anyio
async def test_query_mission_intent_lists_missions_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mission = SimpleNamespace(title="notebook gamer", status=MissionStatus.ACTIVE)

    fake_user = _fake_user()
    monkeypatch.setattr(
        "app.telegram.router.list_missions_for_user",
        AsyncMock(return_value=[fake_mission]),
    )
    intent = _intent(kind=IntentKind.QUERY_MISSION)
    reply = await _dispatch_intent(intent, session=_async_session(), user=fake_user)

    assert "notebook gamer" in reply
    # TASK-063: rótulo em português, nunca o valor bruto do enum ("active")
    assert "ativa" in reply
    assert "active" not in reply


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command", ["/listar_missoes", "/listar-missoes", "missoes", "missões"]
)
async def test_list_missions_command_is_numbered_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    fake_user = _fake_user()
    fake_user.username = "cliente"
    missions = [
        _fake_mission(title="Processador Ryzen", status=MissionStatus.ACTIVE),
        _fake_mission(title="Monitor 4K", status=MissionStatus.PAUSED),
        _fake_mission(title="Memória DDR5", status=MissionStatus.CANCELLED),
    ]
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    list_mock = AsyncMock(return_value=missions)
    monkeypatch.setattr("app.telegram.router.list_visible_missions_for_user", list_mock)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))
    session = _async_session()

    reply = await _handle_message(
        _message(command),
        user=fake_user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://auth.example.test",
        created_now=False,
    )

    assert reply == (
        "📋 Suas missões:\n\n"
        "1 — Processador Ryzen — 🟢 ativa\n"
        "2 — Monitor 4K — ⏸️ pausada\n"
        "3 — Memória DDR5 — ❌ cancelada"
    )
    assert adapter.calls == []
    list_mock.assert_awaited_once_with(session, user_id=fake_user.id, limit=16)


@pytest.mark.anyio
async def test_list_missions_command_handles_empty_result_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    fake_user.username = "cliente"
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router.list_visible_missions_for_user",
        AsyncMock(return_value=[]),
    )
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    reply = await _handle_message(
        _message("missões"),
        user=fake_user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://auth.example.test",
        created_now=False,
    )

    assert reply == "Você não tem missões ativas, pausadas ou canceladas."
    assert adapter.calls == []


@pytest.mark.anyio
async def test_mission_command_intent_stages_confirmation_without_transitioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.models import MissionCommand

    fake_user = _fake_user()
    fake_mission = SimpleNamespace(id=uuid4(), title="notebook gamer", state_version=1)

    monkeypatch.setattr(
        "app.telegram.router.list_mission_command_candidates",
        AsyncMock(return_value=[fake_mission]),
    )

    async def _fail_transition(*args: object, **kwargs: object) -> object:
        raise AssertionError("transition_mission should not run yet")

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fail_transition
    )
    intent = _intent(
        kind=IntentKind.MISSION_COMMAND,
        command=MissionCommand.PAUSE,
    )
    reply = await _dispatch_intent(intent, session=_async_session(), user=fake_user)
    assert fake_user.pending_intent == {
        "kind": "mission_command",
        "mission_id": str(fake_mission.id),
        "mission_title": "notebook gamer",
        "command": "pause",
        "expected_state_version": 1,
    }
    assert "pausar" in reply
    assert "notebook gamer" in reply


@pytest.mark.anyio
async def test_confirmed_pending_mission_command_executes_and_clears_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = uuid4()
    fake_user = _fake_user(
        pending_intent={
            "kind": "mission_command",
            "mission_id": str(mission_id),
            "mission_title": "notebook gamer",
            "command": "pause",
            "expected_state_version": 1,
        }
    )
    fake_transition = SimpleNamespace(to_status=MissionStatus.PAUSED)

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async",
        AsyncMock(return_value=fake_transition),
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="confirmo",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent is None
    assert "pausada" in send_calls[0][1]


# --- TASK-085: seleção numérica de missão para comandos ambíguos ---


def _fake_mission(*, title: str, status: MissionStatus, state_version: int = 1):
    return SimpleNamespace(
        id=uuid4(), title=title, status=status, state_version=state_version
    )


@pytest.mark.anyio
async def test_mission_command_multiple_candidates_stages_numbered_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.models import MissionCommand

    fake_user = _fake_user()
    candidates = [
        _fake_mission(title="Ryzen 7 9800X3D", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.ACTIVE),
        _fake_mission(title="Mouse Logitech", status=MissionStatus.PAUSED),
    ]

    _patch_user(monkeypatch, fake_user)
    monkeypatch.setattr(
        "app.telegram.router.list_mission_command_candidates",
        AsyncMock(return_value=candidates),
    )
    intent = _intent(kind=IntentKind.MISSION_COMMAND, command=MissionCommand.CANCEL)
    body = await _dispatch_intent(intent, session=_async_session(), user=fake_user)
    assert "1 — Ryzen 7 9800X3D — ativa" in body
    assert "3 — Mouse Logitech — pausada" in body
    assert fake_user.pending_intent["kind"] == "mission_command_choice"
    assert len(fake_user.pending_intent["missions"]) == 3


def _staged_choice_user(*, missions: list, command: str = "cancel") -> SimpleNamespace:
    return _fake_user(
        pending_intent={
            "kind": "mission_command_choice",
            "command": command,
            "missions": [
                {
                    "mission_id": str(mission.id),
                    "mission_title": mission.title,
                    "expected_state_version": mission.state_version,
                }
                for mission in missions
            ],
        }
    )


@pytest.mark.anyio
async def test_mission_command_choice_single_selection_executes_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missions = [
        _fake_mission(title="Ryzen 7 9800X3D", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.ACTIVE),
    ]
    fake_user = _staged_choice_user(missions=missions)
    fake_transition = SimpleNamespace(to_status=MissionStatus.CANCELLED)

    _patch_user(monkeypatch, fake_user)
    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async",
        AsyncMock(return_value=fake_transition),
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []  # nenhuma chamada de IA para resolver o número
    assert fake_user.pending_intent is None
    body = send_calls[0][1]
    assert "✅ 1 —" in body and "Ryzen 7 9800X3D" in body
    assert "Cadeira gamer" not in body


@pytest.mark.anyio
async def test_mission_command_choice_multi_selection_processes_individually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missions = [
        _fake_mission(title="Ryzen 7 9800X3D", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.ACTIVE),
        _fake_mission(title="Mouse Logitech", status=MissionStatus.ACTIVE),
    ]
    fake_user = _staged_choice_user(missions=missions)
    fake_transition = SimpleNamespace(to_status=MissionStatus.CANCELLED)

    _patch_user(monkeypatch, fake_user)
    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async",
        AsyncMock(return_value=fake_transition),
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1, 3",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    body = send_calls[0][1]
    assert "1 —" in body and "Ryzen 7 9800X3D" in body
    assert "3 —" in body and "Mouse Logitech" in body
    assert "Cadeira gamer" not in body
    assert fake_user.pending_intent is None


@pytest.mark.anyio
async def test_mission_command_choice_invalid_index_keeps_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missions = [_fake_mission(title="Ryzen 7 9800X3D", status=MissionStatus.ACTIVE)]
    fake_user = _staged_choice_user(missions=missions)
    original_payload = fake_user.pending_intent

    _patch_user(monkeypatch, fake_user)

    async def _fail_transition(*args: object, **kwargs: object) -> object:
        raise AssertionError("transition_mission_async should not run")

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fail_transition
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="9",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent == original_payload
    assert "Não entendi" in send_calls[0][1]


@pytest.mark.anyio
async def test_mission_command_choice_mixed_results_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.service import MissionVersionConflictError

    missions = [
        _fake_mission(title="Ryzen 7 9800X3D", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.PAUSED),
        _fake_mission(title="Mouse Logitech", status=MissionStatus.ACTIVE),
    ]
    fake_user = _staged_choice_user(missions=missions)

    _patch_user(monkeypatch, fake_user)

    async def _transition(
        _session: object, *, mission_id: object, **kwargs: object
    ) -> object:
        if str(mission_id) == str(missions[0].id):
            return SimpleNamespace(to_status=MissionStatus.CANCELLED)
        if str(mission_id) == str(missions[1].id):
            raise MissionVersionConflictError("mudou")
        raise AssertionError("unexpected mission")

    monkeypatch.setattr("app.telegram.router.transition_mission_async", _transition)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    # 1ª missão: ownership ok; 2ª: ownership ok mas conflito de versão; 3ª:
    # não encontrada (get devolve None) -- prova que um resultado ruim não
    # impede os demais (nunca tudo-ou-nada).
    session.get.side_effect = [
        SimpleNamespace(user_id=fake_user.id, status=MissionStatus.PAUSED),
        SimpleNamespace(user_id=fake_user.id, status=MissionStatus.PAUSED),
        None,
    ]

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1,2,3",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    body = send_calls[0][1]
    assert "✅ 1 —" in body
    assert "⚠️ 2 —" in body
    assert "❌ 3 —" in body
    assert fake_user.pending_intent is None


@pytest.mark.anyio
async def test_mission_command_choice_ownership_mismatch_marks_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missions = [_fake_mission(title="Ryzen 7 9800X3D", status=MissionStatus.ACTIVE)]
    fake_user = _staged_choice_user(missions=missions)

    _patch_user(monkeypatch, fake_user)

    async def _fail_transition(*args: object, **kwargs: object) -> object:
        raise AssertionError("transition_mission_async should not run")

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fail_transition
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=uuid4())  # outro usuário

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert "❌" in send_calls[0][1]
    assert fake_user.pending_intent is None


@pytest.mark.anyio
async def test_edit_mission_intent_via_free_text_redirects_to_editar_missao_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-071: o caminho antigo (texto livre interpretado pela IA) foi
    desativado -- nunca mais encena nada nem toca em
    `list_mission_command_candidates`, só orienta a usar `/editar-missao`."""
    fake_user = _fake_user()

    _patch_user(monkeypatch, fake_user)

    async def _fail_resolve(*args: object, **kwargs: object) -> object:
        raise AssertionError("list_mission_command_candidates should not run")

    monkeypatch.setattr(
        "app.telegram.router.list_mission_command_candidates", _fail_resolve
    )
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(
        kind=IntentKind.EDIT_MISSION,
        parameters=IntentParameters(
            mission_reference="teclado",
            target_amount=Decimal("300.00"),
            target_currency="BRL",
        ),
    )
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    assert adapter.calls == []
    assert "/criar_missao" in send_calls[0][1]


@pytest.mark.anyio
async def test_confirmed_pause_for_edit_pauses_and_opens_edit_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-090: em vez de terminar o fluxo pedindo /editar_missao de novo,
    a confirmação de pausa segue direto para o menu de edição, na mesma
    cadeia de `pending_intent`, marcada como `auto_paused=True`."""
    mission_id = uuid4()
    fake_user = _fake_user(
        pending_intent={
            "kind": "pause_for_edit",
            "mission_id": str(mission_id),
            "mission_title": "notebook gamer",
            "expected_state_version": 1,
        }
    )

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    transition_calls: list[dict] = []

    async def _fake_transition(session: object, **kwargs: object) -> None:
        transition_calls.append(kwargs)

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async",
        _fake_transition,
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert transition_calls[0]["command"].value == "pause"
    assert fake_user.pending_intent == {
        "kind": "await_edit_menu_choice",
        "mission_id": str(mission_id),
        "mission_title": "notebook gamer",
        "expected_state_version": 2,
        "auto_paused": True,
    }
    reply = send_calls[0][1]
    assert "notebook gamer" in reply
    assert "1 — Lojas" in reply
    assert "2 — Preço-alvo" in reply


@pytest.mark.anyio
async def test_declined_pause_for_edit_does_not_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "pause_for_edit",
            "mission_id": str(uuid4()),
            "mission_title": "notebook gamer",
            "expected_state_version": 1,
        }
    )

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, False)

    async def _fail_transition(*args: object, **kwargs: object) -> object:
        raise AssertionError("transition_mission should not run on cancel")

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fail_transition
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="2",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    assert "cancelei" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_confirmed_pending_edit_mission_executes_and_clears_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = uuid4()
    fake_user = _fake_user(
        pending_intent={
            "kind": "edit_mission",
            "mission_id": str(mission_id),
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "changes_target": True,
            "target_amount": "300.00",
            "target_currency": "BRL",
            "changes_sources": True,
            "sources": ["kabum", "pichau"],
            "previous_target_amount": "500.00",
            "previous_target_currency": "BRL",
            "previous_sources": ["kabum"],
        }
    )

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    edit_calls: list[dict] = []

    async def _fake_edit(session: object, **kwargs: object):
        edit_calls.append(kwargs)
        return SimpleNamespace(id=mission_id), ("kabum", "pichau")

    monkeypatch.setattr("app.telegram.router.edit_mission_criteria", _fake_edit)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    assert edit_calls[0]["mission_id"] == mission_id
    assert edit_calls[0]["expected_state_version"] == 2
    assert edit_calls[0]["target_update"] == (Decimal("300.00"), "BRL")
    assert edit_calls[0]["source_codes"] == ("kabum", "pichau")
    assert "R$ 300,00" in send_calls[0][1]
    assert "Kabum, Pichau" in send_calls[0][1]
    assert "continua pausada" in send_calls[0][1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("auto_paused", "expected_fragment"),
    [
        (True, "A missão continua pausada."),
        (False, "como já estava antes desta edição"),
    ],
)
async def test_execute_edit_mission_message_reflects_auto_paused_origin(
    monkeypatch: pytest.MonkeyPatch, auto_paused: bool, expected_fragment: str
) -> None:
    """TASK-090: audita o comportamento de `/editar_missao` -- a mensagem
    final distingue se a pausa foi provocada agora pela própria edição
    (`auto_paused=True`, missão estava `ACTIVE`) ou se a missão já estava
    `PAUSED` por escolha anterior do usuário (`auto_paused=False`), sem
    jamais retomar sozinho em nenhum dos dois casos. As duas variantes
    apontam para o comando real `/retomar` -- nunca para um comando
    inexistente."""
    mission_id = uuid4()
    fake_user = _fake_user(
        pending_intent={
            "kind": "edit_mission",
            "mission_id": str(mission_id),
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "changes_target": True,
            "target_amount": "300.00",
            "target_currency": "BRL",
            "changes_sources": False,
            "sources": [],
            "previous_target_amount": "500.00",
            "previous_target_currency": "BRL",
            "previous_sources": [],
            "auto_paused": auto_paused,
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    async def _fake_edit(session: object, **kwargs: object):
        return SimpleNamespace(id=mission_id), ()

    monkeypatch.setattr("app.telegram.router.edit_mission_criteria", _fake_edit)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="1",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    reply = send_calls[0][1]
    assert expected_fragment in reply
    assert "/retomar" in reply
    assert "continua pausada" in reply


@pytest.mark.anyio
async def test_resolve_pending_intent_preserves_new_state_set_by_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressão direta do bug corrigido nesta TASK: quando o executor do
    `pending_intent` (ex.: `_execute_pause_for_edit`) substitui
    `user.pending_intent` por um NOVO estado antes de retornar (para
    encadear no menu de edição), o cleanup de `_resolve_pending_intent`
    não pode apagar esse novo estado. Com a implementação anterior ao fix
    (`user.pending_intent = None` incondicional após executar), este
    teste falha -- verificado manualmente revertendo a correção."""
    original_payload = {"kind": "pause_for_edit", "mission_id": "x"}
    replacement = {"kind": "await_edit_menu_choice", "mission_id": "x"}
    user = _registered_user(pending_intent=dict(original_payload))

    async def _fake_execute(payload: dict, *, session: object, user: object) -> str:
        assert payload == original_payload
        user.pending_intent = replacement
        return "menu de edição"

    monkeypatch.setattr("app.telegram.router._execute_pending_intent", _fake_execute)
    adapter = _FakeAdapter(AssertionError("AI must not run"))

    reply = await _resolve_pending_intent(
        _message("sim"),
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        user=user,
    )

    assert reply == "menu de edição"
    assert user.pending_intent == replacement  # não foi apagado pelo cleanup
    assert adapter.calls == []


@pytest.mark.anyio
async def test_resolve_pending_intent_clears_state_when_executor_does_not_replace_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contraparte do teste acima: quando o executor NÃO substitui
    `pending_intent` (o caso comum -- create_mission, edit_mission,
    mission_command), o cleanup ainda precisa limpar o estado."""
    original_payload = {"kind": "mission_command", "mission_id": "x"}
    user = _registered_user(pending_intent=dict(original_payload))

    async def _fake_execute(payload: dict, *, session: object, user: object) -> str:
        return "cancelada"

    monkeypatch.setattr("app.telegram.router._execute_pending_intent", _fake_execute)
    adapter = _FakeAdapter(AssertionError("AI must not run"))

    reply = await _resolve_pending_intent(
        _message("sim"),
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        user=user,
    )

    assert reply == "cancelada"
    assert user.pending_intent is None
    assert adapter.calls == []


@pytest.mark.anyio
async def test_full_cycle_active_pause_edit_resume_returns_to_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cenário A da auditoria do usuário (2026-08-21): missão ACTIVE ->
    /editar_missao pausa para editar -> edição concluída -> PAUSED ->
    /retomar -> ACTIVE de novo, com status/`auto_paused` corretos em cada
    etapa, a transição oficial (`transition_mission_async`) sendo usada
    tanto para pausar quanto para retomar, e nenhuma chamada à IA em
    nenhum dos 7 turnos."""
    from app.missions.models import MissionCommand

    mission = _fake_mission(
        title="RTX 5070", status=MissionStatus.ACTIVE, state_version=1
    )
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run in this cycle"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )

    async def _fake_query_by_status(
        session: object, *, user_id: object, status_value: MissionStatus
    ) -> list:
        return [mission] if mission.status == status_value else []

    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status", _fake_query_by_status
    )
    session = _async_session()
    session.get = AsyncMock(return_value=SimpleNamespace(user_id=user.id))
    session.scalar = AsyncMock(return_value=None)  # MissionCriteria: sem alvo anterior
    transition_calls: list[dict] = []

    async def _fake_transition(session: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["expected_state_version"] == mission.state_version
        transition_calls.append(kwargs)
        if kwargs["command"] == MissionCommand.PAUSE:
            mission.status = MissionStatus.PAUSED
        elif kwargs["command"] == MissionCommand.RESUME:
            mission.status = MissionStatus.ACTIVE
        mission.state_version += 1
        return SimpleNamespace(to_status=mission.status)

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fake_transition
    )
    edit_calls: list[dict] = []

    async def _fake_edit(session: object, **kwargs: object):
        edit_calls.append(kwargs)
        return SimpleNamespace(id=mission.id), ()

    monkeypatch.setattr("app.telegram.router.edit_mission_criteria", _fake_edit)

    async def _send(text: str) -> str:
        return await _handle_message(
            _message(text),
            user=user,
            adapters=_adapters(adapter),  # type: ignore[arg-type]
            session=session,
            auth_public_base_url="https://example.test",
            created_now=False,
        )

    # 1) /editar_missao com a única missão ACTIVE -> oferece pausar primeiro
    await _send("/editar_missao")
    assert mission.status == MissionStatus.ACTIVE
    assert user.pending_intent["kind"] == "pause_for_edit"

    # 2) confirma a pausa -> pausa de verdade (transição oficial) e já abre
    #    o menu de edição na mesma resposta
    reply_2 = await _send("sim")
    assert mission.status == MissionStatus.PAUSED
    assert transition_calls[0]["command"] == MissionCommand.PAUSE
    assert user.pending_intent == {
        "kind": "await_edit_menu_choice",
        "mission_id": str(mission.id),
        "mission_title": "RTX 5070",
        "expected_state_version": 2,
        "auto_paused": True,
    }
    assert "1 — Lojas" in reply_2 and "2 — Preço-alvo" in reply_2

    # 3) escolhe "Preço-alvo"
    await _send("2")
    assert user.pending_intent["kind"] == "await_edit_target_amount"
    assert user.pending_intent["auto_paused"] is True

    # 4) digita o novo valor
    await _send("300")
    assert user.pending_intent["kind"] == "edit_mission"
    assert user.pending_intent["auto_paused"] is True

    # 5) confirma a edição -> executa, missão CONTINUA pausada (nunca
    #    retoma sozinha só por causa da edição)
    reply_5 = await _send("sim")
    assert edit_calls[0]["expected_state_version"] == 2
    assert edit_calls[0]["target_update"] == (Decimal("300"), "BRL")
    assert mission.status == MissionStatus.PAUSED
    assert len(transition_calls) == 1  # só a pausa do passo 2 até aqui
    assert "A missão continua pausada." in reply_5
    assert "Use /retomar" in reply_5
    assert user.pending_intent is None

    # 6) /retomar -- única missão PAUSED
    await _send("/retomar")
    assert user.pending_intent["kind"] == "mission_command"
    assert user.pending_intent["command"] == "resume"

    # 7) confirma -- usa a mesma transição oficial, volta a ACTIVE
    reply_7 = await _send("sim")
    assert transition_calls[-1]["command"] == MissionCommand.RESUME
    assert len(transition_calls) == 2
    assert mission.status == MissionStatus.ACTIVE  # elegível para coleta de novo
    assert "ativa" in reply_7.lower()
    assert user.pending_intent is None

    assert adapter.calls == []  # zero chamadas à IA nos 7 turnos


@pytest.mark.anyio
async def test_full_cycle_paused_edit_stays_paused_without_auto_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cenário B da auditoria do usuário (2026-08-21): missão já PAUSED ->
    /editar_missao -> edição concluída -> continua PAUSED,
    `auto_paused=False`, nenhuma retomada automática, mensagem final não
    dá a entender que o sistema acabou de pausar, nenhuma chamada à IA."""
    mission = _fake_mission(
        title="Monitor 4K", status=MissionStatus.PAUSED, state_version=5
    )
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run in this cycle"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )

    async def _fake_query_by_status(
        session: object, *, user_id: object, status_value: MissionStatus
    ) -> list:
        return [mission] if mission.status == status_value else []

    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status", _fake_query_by_status
    )
    session = _async_session()
    session.get = AsyncMock(return_value=SimpleNamespace(user_id=user.id))
    session.scalar = AsyncMock(return_value=None)
    transition = AsyncMock(
        side_effect=AssertionError("pausar/retomar não deve ser chamado neste cenário")
    )
    monkeypatch.setattr("app.telegram.router.transition_mission_async", transition)
    edit_calls: list[dict] = []

    async def _fake_edit(session: object, **kwargs: object):
        edit_calls.append(kwargs)
        return SimpleNamespace(id=mission.id), ()

    monkeypatch.setattr("app.telegram.router.edit_mission_criteria", _fake_edit)

    async def _send(text: str) -> str:
        return await _handle_message(
            _message(text),
            user=user,
            adapters=_adapters(adapter),  # type: ignore[arg-type]
            session=session,
            auth_public_base_url="https://example.test",
            created_now=False,
        )

    # 1) /editar_missao com a única missão já PAUSED -> vai direto ao menu,
    #    sem pausar de novo
    await _send("/editar_missao")
    assert user.pending_intent == {
        "kind": "await_edit_menu_choice",
        "mission_id": str(mission.id),
        "mission_title": "Monitor 4K",
        "expected_state_version": 5,
        "auto_paused": False,
    }

    # 2) Preço-alvo
    await _send("2")
    assert user.pending_intent["auto_paused"] is False

    # 3) novo valor
    await _send("500")
    assert user.pending_intent["auto_paused"] is False

    # 4) confirma
    reply = await _send("sim")

    transition.assert_not_awaited()  # nenhuma pausa/retomada disparada
    assert mission.status == MissionStatus.PAUSED  # nunca muda sozinho
    assert edit_calls[0]["expected_state_version"] == 5
    assert "como já estava antes desta edição" in reply
    assert "pausada.\n\nUse /retomar" not in reply  # não é a variante auto_paused=True
    assert user.pending_intent is None
    assert adapter.calls == []


def _patch_missions_by_status(
    monkeypatch: pytest.MonkeyPatch, by_status: dict[MissionStatus, list]
) -> None:
    async def _fake_query(
        session: object, *, user_id: object, status_value: MissionStatus
    ):
        return by_status.get(status_value, [])

    monkeypatch.setattr("app.telegram.router._query_missions_by_status", _fake_query)


def _editar_missao_update() -> TelegramUpdate:
    return _update(
        message=_TelegramIncomingMessage(
            text="/editar-missao",
            date=1754586000,
            chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
            from_=_TelegramSender(id=222, first_name="Fulano"),
        )
    )


def _numeric_reply_update(text: str) -> TelegramUpdate:
    return _update(
        message=_TelegramIncomingMessage(
            text=text,
            date=1754586000,
            chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
            from_=_TelegramSender(id=222, first_name="Fulano"),
        )
    )


async def _send_editar_missao(monkeypatch: pytest.MonkeyPatch, session: object) -> list:
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)
    await receive_telegram_webhook(
        update=_editar_missao_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )
    assert adapter.calls == []  # TASK-071: nunca passa pelo IntentInterpreter
    return send_calls


@pytest.mark.anyio
async def test_editar_missao_with_one_paused_mission_shows_main_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    mission = SimpleNamespace(
        id=uuid4(),
        title="teclado mecanico",
        state_version=2,
        status=MissionStatus.PAUSED,
    )
    _patch_user(monkeypatch, fake_user)
    _patch_missions_by_status(monkeypatch, {MissionStatus.PAUSED: [mission]})

    send_calls = await _send_editar_missao(monkeypatch, _async_session())

    assert fake_user.pending_intent == {
        "kind": "await_edit_menu_choice",
        "mission_id": str(mission.id),
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "auto_paused": False,
    }
    reply = send_calls[0][1]
    assert "teclado mecanico" in reply
    assert "1 — Lojas" in reply
    assert "2 — Preço-alvo" in reply


@pytest.mark.anyio
async def test_editar_missao_with_multiple_paused_missions_asks_which_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    first = SimpleNamespace(
        id=uuid4(),
        title="teclado mecanico",
        state_version=1,
        status=MissionStatus.PAUSED,
    )
    second = SimpleNamespace(
        id=uuid4(), title="monitor curvo", state_version=3, status=MissionStatus.PAUSED
    )
    _patch_user(monkeypatch, fake_user)
    _patch_missions_by_status(monkeypatch, {MissionStatus.PAUSED: [first, second]})

    send_calls = await _send_editar_missao(monkeypatch, _async_session())

    assert fake_user.pending_intent == {
        "kind": "await_edit_paused_choice",
        "mission_ids": [str(first.id), str(second.id)],
        "mission_titles": ["teclado mecanico", "monitor curvo"],
        "mission_state_versions": [1, 3],
    }
    reply = send_calls[0][1]
    assert "1 — teclado mecanico" in reply
    assert "2 — monitor curvo" in reply


@pytest.mark.anyio
async def test_editar_missao_with_no_paused_but_one_active_offers_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    mission = SimpleNamespace(
        id=uuid4(), title="monitor curvo", state_version=1, status=MissionStatus.ACTIVE
    )
    _patch_user(monkeypatch, fake_user)
    _patch_missions_by_status(
        monkeypatch, {MissionStatus.PAUSED: [], MissionStatus.ACTIVE: [mission]}
    )

    send_calls = await _send_editar_missao(monkeypatch, _async_session())

    assert fake_user.pending_intent == {
        "kind": "pause_for_edit",
        "mission_id": str(mission.id),
        "mission_title": "monitor curvo",
        "expected_state_version": 1,
    }
    assert "ativa" in send_calls[0][1]


@pytest.mark.anyio
async def test_editar_missao_with_no_paused_and_multiple_active_asks_which_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    first = SimpleNamespace(
        id=uuid4(), title="ssd nvme", state_version=1, status=MissionStatus.ACTIVE
    )
    second = SimpleNamespace(
        id=uuid4(), title="rtx 4060", state_version=1, status=MissionStatus.ACTIVE
    )
    _patch_user(monkeypatch, fake_user)
    _patch_missions_by_status(
        monkeypatch, {MissionStatus.PAUSED: [], MissionStatus.ACTIVE: [first, second]}
    )

    send_calls = await _send_editar_missao(monkeypatch, _async_session())

    assert fake_user.pending_intent["kind"] == "await_edit_active_choice"
    assert "ssd nvme" in send_calls[0][1]
    assert "rtx 4060" in send_calls[0][1]


@pytest.mark.anyio
async def test_editar_missao_with_no_missions_says_nothing_to_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    _patch_missions_by_status(
        monkeypatch, {MissionStatus.PAUSED: [], MissionStatus.ACTIVE: []}
    )

    send_calls = await _send_editar_missao(monkeypatch, _async_session())

    assert fake_user.pending_intent is None
    assert "pausada ou ativa" in send_calls[0][1]


@pytest.mark.anyio
async def test_mission_choice_answer_advances_to_menu_for_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_ids = [str(uuid4()), str(uuid4())]
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_paused_choice",
            "mission_ids": mission_ids,
            "mission_titles": ["teclado mecanico", "monitor curvo"],
            "mission_state_versions": [1, 3],
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("2"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert adapter.calls == []
    assert fake_user.pending_intent == {
        "kind": "await_edit_menu_choice",
        "mission_id": mission_ids[1],
        "mission_title": "monitor curvo",
        "expected_state_version": 3,
        "auto_paused": False,
    }
    assert "monitor curvo" in send_calls[0][1]


@pytest.mark.anyio
async def test_mission_choice_answer_advances_to_pause_offer_for_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_active_choice",
            "mission_ids": [mission_id],
            "mission_titles": ["ssd nvme"],
            "mission_state_versions": [1],
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == {
        "kind": "pause_for_edit",
        "mission_id": mission_id,
        "mission_title": "ssd nvme",
        "expected_state_version": 1,
    }
    assert "ativa" in send_calls[0][1]


@pytest.mark.anyio
async def test_mission_choice_invalid_answer_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "kind": "await_edit_paused_choice",
        "mission_ids": [str(uuid4())],
        "mission_titles": ["teclado mecanico"],
        "mission_state_versions": [1],
    }
    fake_user = _fake_user(pending_intent=dict(pending))
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("9"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == pending
    assert "Não entendi" in send_calls[0][1]


@pytest.mark.anyio
async def test_edit_menu_choice_lojas_shows_lojas_submenu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_menu_choice",
            "mission_id": mission_id,
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
        }
    )
    _patch_user(monkeypatch, fake_user)

    async def _fake_source_codes(session: object, mid: object) -> tuple[str, ...]:
        return ("pichau",)

    monkeypatch.setattr(
        "app.telegram.router._query_mission_source_codes",
        _fake_source_codes,
    )
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == {
        "kind": "await_edit_lojas_choice",
        "mission_id": mission_id,
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "current_sources": ["pichau"],
        "auto_paused": False,
    }
    reply = send_calls[0][1]
    assert "Adicionar" in reply
    assert "Remover" in reply


@pytest.mark.anyio
async def test_edit_menu_choice_preco_shows_price_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_menu_choice",
            "mission_id": mission_id,
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
        }
    )
    _patch_user(monkeypatch, fake_user)
    session = _async_session()
    session.scalar.return_value = SimpleNamespace(
        target_amount=Decimal("500.00"), target_currency="BRL"
    )
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("2"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert fake_user.pending_intent == {
        "kind": "await_edit_target_amount",
        "mission_id": mission_id,
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "previous_target_amount": "500.00",
        "previous_target_currency": "BRL",
        "auto_paused": False,
    }
    assert "novo preço-alvo" in send_calls[0][1]


@pytest.mark.anyio
async def test_edit_menu_choice_invalid_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "kind": "await_edit_menu_choice",
        "mission_id": str(uuid4()),
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
    }
    fake_user = _fake_user(pending_intent=dict(pending))
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("abc"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == pending
    assert "Não entendi" in send_calls[0][1]


@pytest.mark.anyio
async def test_lojas_choice_add_shows_missing_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_lojas_choice",
            "mission_id": mission_id,
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "current_sources": ["pichau"],
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == {
        "kind": "await_edit_add_sources",
        "mission_id": mission_id,
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "current_sources": ["pichau"],
        "option_map": {
            "1": "terabyte",
            "2": "amazon",
            "3": "kabum",
            "4": "magalu",
            "5": "mercadolivre",
        },
        "auto_paused": False,
    }
    reply = send_calls[0][1]
    assert "1 — Terabyte" in reply
    assert "2 — Amazon" in reply
    assert "3 — Kabum" in reply
    assert "4 — Magalu" in reply
    assert "Pichau" not in reply


@pytest.mark.anyio
async def test_lojas_choice_remove_shows_current_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_lojas_choice",
            "mission_id": mission_id,
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "current_sources": ["pichau", "kabum"],
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("2"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == {
        "kind": "await_edit_remove_sources",
        "mission_id": mission_id,
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "current_sources": ["pichau", "kabum"],
        "option_map": {"1": "pichau", "2": "kabum"},
        "auto_paused": False,
    }
    reply = send_calls[0][1]
    assert "1 — Pichau" in reply
    assert "2 — Kabum" in reply


@pytest.mark.anyio
async def test_lojas_choice_remove_blocked_when_only_one_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_lojas_choice",
            "mission_id": str(uuid4()),
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "current_sources": ["pichau"],
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("2"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent is None
    assert "Não dá para remover a última loja" in send_calls[0][1]


@pytest.mark.anyio
async def test_lojas_choice_add_blocked_when_all_stores_already_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_lojas_choice",
            "mission_id": str(uuid4()),
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "current_sources": [
                "pichau",
                "terabyte",
                "amazon",
                "kabum",
                "magalu",
                "mercadolivre",
            ],
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent is None
    assert "já está vinculada a todas" in send_calls[0][1]


@pytest.mark.anyio
async def test_add_sources_valid_selection_advances_to_edit_mission_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_add_sources",
            "mission_id": mission_id,
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "current_sources": ["pichau"],
            "option_map": {"1": "terabyte", "2": "amazon", "3": "kabum"},
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1,3"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == {
        "kind": "edit_mission",
        "mission_id": mission_id,
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "changes_target": False,
        "target_amount": None,
        "target_currency": None,
        "changes_sources": True,
        "sources": ["pichau", "terabyte", "kabum"],
        "previous_target_amount": None,
        "previous_target_currency": None,
        "previous_sources": ["pichau"],
        "auto_paused": False,
    }
    reply = send_calls[0][1]
    assert "Pichau" in reply
    assert "Terabyte, Kabum" in reply or "Pichau, Terabyte, Kabum" in reply


@pytest.mark.anyio
async def test_add_sources_invalid_selection_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "kind": "await_edit_add_sources",
        "mission_id": str(uuid4()),
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "current_sources": ["pichau"],
        "option_map": {"1": "terabyte", "2": "amazon", "3": "kabum"},
    }
    fake_user = _fake_user(pending_intent=dict(pending))
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1,9"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == pending
    assert "Não entendi essa opção" in send_calls[0][1]


@pytest.mark.anyio
async def test_remove_sources_valid_selection_advances_to_edit_mission_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_remove_sources",
            "mission_id": mission_id,
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "current_sources": ["pichau", "kabum"],
            "option_map": {"1": "pichau", "2": "kabum"},
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == {
        "kind": "edit_mission",
        "mission_id": mission_id,
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "changes_target": False,
        "target_amount": None,
        "target_currency": None,
        "changes_sources": True,
        "sources": ["kabum"],
        "previous_target_amount": None,
        "previous_target_currency": None,
        "previous_sources": ["pichau", "kabum"],
        "auto_paused": False,
    }
    assert "Kabum" in send_calls[0][1]


@pytest.mark.anyio
async def test_remove_sources_would_empty_all_keeps_pending_and_asks_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "kind": "await_edit_remove_sources",
        "mission_id": str(uuid4()),
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "current_sources": ["pichau", "kabum"],
        "option_map": {"1": "pichau", "2": "kabum"},
    }
    fake_user = _fake_user(pending_intent=dict(pending))
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("1,2"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == pending
    assert "pelo menos uma" in send_calls[0][1]


@pytest.mark.anyio
async def test_target_amount_valid_number_advances_to_edit_mission_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = str(uuid4())
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_target_amount",
            "mission_id": mission_id,
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "previous_target_amount": "500.00",
            "previous_target_currency": "BRL",
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("300"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == {
        "kind": "edit_mission",
        "mission_id": mission_id,
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "changes_target": True,
        "target_amount": "300",
        "target_currency": "BRL",
        "changes_sources": False,
        "sources": [],
        "previous_target_amount": "500.00",
        "previous_target_currency": "BRL",
        "previous_sources": [],
        "auto_paused": False,
    }
    reply = send_calls[0][1]
    assert "R$ 500,00" in reply
    assert "R$ 300,00" in reply


@pytest.mark.anyio
async def test_target_amount_zero_clears_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_target_amount",
            "mission_id": str(uuid4()),
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "previous_target_amount": "500.00",
            "previous_target_currency": "BRL",
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("0"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent["changes_target"] is True
    assert fake_user.pending_intent["target_amount"] is None
    assert fake_user.pending_intent["target_currency"] is None
    assert "removido" in send_calls[0][1] or "sem alvo" in send_calls[0][1]


@pytest.mark.anyio
async def test_target_amount_accepts_comma_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "await_edit_target_amount",
            "mission_id": str(uuid4()),
            "mission_title": "teclado mecanico",
            "expected_state_version": 2,
            "previous_target_amount": None,
            "previous_target_currency": None,
        }
    )
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("300,50"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent["target_amount"] == "300.50"
    assert "R$ 300,50" in send_calls[0][1]


@pytest.mark.anyio
async def test_target_amount_invalid_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = {
        "kind": "await_edit_target_amount",
        "mission_id": str(uuid4()),
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "previous_target_amount": None,
        "previous_target_currency": None,
    }
    fake_user = _fake_user(pending_intent=dict(pending))
    _patch_user(monkeypatch, fake_user)
    adapter = _FakeAdapter(_intent())
    send_calls = _patch_send_message(monkeypatch)

    await receive_telegram_webhook(
        update=_numeric_reply_update("não sei quanto"),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert fake_user.pending_intent == pending
    assert "Não entendi" in send_calls[0][1]


@pytest.mark.anyio
async def test_unknown_role_is_denied_without_functional_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(role="OWNER")
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent(kind=IntentKind.QUERY_MISSION))
    session = _async_session()

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert send_calls == []
    assert fake_user.telegram_chat_id is None
    audit = session.add.call_args.args[0]
    assert audit.action == "authorization.denied"
    assert audit.entry_metadata == {
        "permission": "telegram.interact",
        "reason": "unknown_role",
        "role": "unknown",
    }


@pytest.mark.anyio
async def test_forged_pending_mission_keeps_state_and_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = uuid4()
    pending = {
        "kind": "mission_command",
        "mission_id": str(mission_id),
        "mission_title": "missao de outro usuario",
        "command": "pause",
        "expected_state_version": 1,
    }
    fake_user = _fake_user(pending_intent=pending)
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=uuid4())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert fake_user.pending_intent == pending
    assert fake_user.telegram_chat_id is None
    assert send_calls == []
    audit = session.add.call_args.args[0]
    assert audit.resource_id == mission_id
    assert audit.entry_metadata["reason"] == "resource_unavailable"


@pytest.mark.anyio
async def test_loose_message_is_deterministic_and_never_calls_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(kind=IntentKind.UNKNOWN)
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "/criar_missao" in send_calls[0][1]


@pytest.mark.anyio
async def test_mission_reference_error_at_staging_is_replied_without_pending_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.models import MissionCommand

    fake_user = _fake_user()
    error = MissionReferenceError("Não encontrei nenhuma missão correspondente.")

    async def _raise(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr("app.telegram.router.list_mission_command_candidates", _raise)
    intent = _intent(kind=IntentKind.MISSION_COMMAND, command=MissionCommand.PAUSE)
    with pytest.raises(MissionReferenceError, match="Não encontrei"):
        await _dispatch_intent(intent, session=_async_session(), user=fake_user)
    assert fake_user.pending_intent is None


@pytest.mark.anyio
async def test_known_error_at_confirmed_execution_is_replied_and_clears_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = MissionTransitionConditionError("A missão precisa de fontes.")
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    async def _raise(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _raise
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    assert send_calls[0][1] == str(error)


@pytest.mark.anyio
async def test_unexpected_error_at_confirmed_execution_is_not_masked_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qualquer erro de fato inesperado (não catalogado) continua subindo
    sem virar sucesso nem resposta genérica -- diferente de
    `MissionCreationError`/`QuotaExceededError`, que TASK-118/subtask 1
    passaram a tratar explicitamente por serem erros de domínio
    conhecidos (ver os testes abaixo)."""
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    async def _raise(*args: object, **kwargs: object) -> object:
        raise RuntimeError("conexão com o banco perdida no meio da transação")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _raise
    )
    adapter = _FakeAdapter(_intent())

    with pytest.raises(RuntimeError, match="conexão com o banco"):
        await receive_telegram_webhook(
            update=_update(
                message=_TelegramIncomingMessage(
                    text="sim",
                    date=1754586000,
                    chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                    from_=_TelegramSender(id=222, first_name="Fulano"),
                )
            ),
            x_telegram_bot_api_secret_token="correct-secret",
            adapters=_adapters(adapter),  # type: ignore[arg-type]
            settings=_settings(),
            session=_async_session(),
        )


@pytest.mark.anyio
async def test_mission_creation_error_at_confirmed_execution_is_replied_and_clears_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressão do bug relatado: antes desta correção, `MissionCreationError`
    caía no `except Exception` genérico e subia sem resposta nenhuma ao
    Telegram (silêncio total). Agora recebe a mesma mensagem amigável que
    a Web já usa para o mesmo erro, sem vazar o texto técnico interno."""
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    async def _raise(*args: object, **kwargs: object) -> object:
        raise MissionCreationError("stores not seeded for codes: pichau")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _raise
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    assert (
        send_calls[0][1]
        == "Não foi possível criar a missão. Tente novamente mais tarde."
    )
    assert "pichau" not in send_calls[0][1]


@pytest.mark.anyio
async def test_quota_exceeded_error_at_mission_creation_is_replied_with_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressão do bug relatado: `QuotaExceededError` também caía no
    `except Exception` genérico e o usuário nunca sabia por que a missão
    não foi criada."""
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    error = QuotaExceededError(
        QuotaKind.ACTIVE_MISSIONS,
        limit=5,
        current=5,
        message="Você já tem 5/5 missões ativas.",
        actions=("pause_mission", "cancel_mission", "manage_missions"),
    )

    async def _raise(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria_async", _raise
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    reply = send_calls[0][1]
    assert "Você já tem 5/5 missões ativas." in reply
    assert "/pausar" in reply
    assert "/cancelar_missao" in reply
    assert "/listar_missoes" in reply


@pytest.mark.anyio
async def test_quota_exceeded_error_at_mission_resume_is_replied_with_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesmo bug, mas no caminho de `/retomar`: `transition_mission_async`
    também pode levantar `QuotaExceededError` (RESUME reativa consumo de
    cota) e passava pelo mesmo `except Exception` mudo."""
    mission_id = uuid4()
    fake_user = _fake_user(
        pending_intent={
            "kind": "mission_command",
            "mission_id": str(mission_id),
            "mission_title": "notebook gamer",
            "command": "resume",
            "expected_state_version": 1,
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    error = QuotaExceededError(
        QuotaKind.ACTIVE_MISSIONS,
        limit=5,
        current=5,
        message="Você já tem 5/5 missões ativas.",
        actions=("pause_mission", "cancel_mission", "manage_missions"),
    )

    async def _raise(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr("app.telegram.router.transition_mission_async", _raise)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    reply = send_calls[0][1]
    assert "Você já tem 5/5 missões ativas." in reply
    assert "/pausar" in reply


@pytest.mark.anyio
async def test_cadastro_command_starts_registration_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/cadastro",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.registration_step == "username"
    assert "usuário" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_cadastro_command_blocked_when_already_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-072: sessão ativa -- não reinicia o fluxo nem toca no perfil."""
    fake_user = _fake_user(registration_step=None)
    fake_user.username = "joaosilva"
    fake_user.email = "joao@example.com"
    fake_user.favorite_stores = ["kabum"]
    fake_user.preferred_categories = ["hardware"]
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/cadastro",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    # nada do estado salvo foi tocado
    assert fake_user.registration_step is None
    assert fake_user.username == "joaosilva"
    assert fake_user.email == "joao@example.com"
    assert fake_user.favorite_stores == ["kabum"]
    assert fake_user.preferred_categories == ["hardware"]
    assert "já está cadastrado e autenticado" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_cadastro_command_blocked_when_already_registered_without_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-073: cadastro concluído (sem `registration_step`, com
    `username`) sem sessão ativa -- não reinicia o fluxo."""
    fake_user = _fake_user(registration_step=None)
    fake_user.username = "joaosilva"
    fake_user.email = "joao@example.com"
    fake_user.favorite_stores = ["kabum"]
    fake_user.preferred_categories = ["hardware"]
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/cadastro",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    # nada do estado salvo foi tocado
    assert fake_user.registration_step is None
    assert fake_user.username == "joaosilva"
    assert fake_user.email == "joao@example.com"
    assert fake_user.favorite_stores == ["kabum"]
    assert fake_user.preferred_categories == ["hardware"]
    assert "já tem cadastro" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_cadastro_command_resumes_in_progress_registration_without_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-073: cadastro em andamento (`registration_step` != None) não é
    tratado como "já registrado" -- continua reiniciando o fluxo, exatamente
    como antes desta TASK. Esse reinício em si é comportamento pré-existente,
    fora do escopo desta TASK."""
    fake_user = _fake_user(registration_step="email")
    fake_user.username = "joaosilva"
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/cadastro",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.registration_step == "username"
    assert "usuário" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_registration_in_progress_consumes_reply_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(registration_step="username")
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.scalar.return_value = None  # nenhum outro usuário com esse username

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="joaosilva",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.username == "joaosilva"
    assert fake_user.registration_step == "email"
    assert send_calls  # perguntou o próximo passo


@pytest.mark.anyio
async def test_registration_rejects_username_already_taken_by_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-072: mantém no passo `username` e pede outro nome."""
    fake_user = _fake_user(registration_step="username")
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = _async_session()
    session.scalar.return_value = uuid4()  # outro usuário já tem esse username

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="joaosilva",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.username is None
    assert fake_user.registration_step == "username"
    assert "já está em uso" in send_calls[0][1]


@pytest.mark.anyio
async def test_registration_full_flow_completes_and_clears_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent())
    adapters = _adapters(adapter)
    monkeypatch.setattr(
        "app.telegram.router.issue_action_link_async",
        AsyncMock(
            return_value=SimpleNamespace(
                url="https://auth.example.test/auth#set_password:opaque",
                expires_at=datetime.now(UTC),
            )
        ),
    )
    session = _async_session()
    session.scalar.return_value = None  # nenhum outro usuário com esse username

    answers = ["joaosilva", "pular", "pichau, kabum", "8, 1"]
    text = "/cadastro"
    for answer in [None, *answers]:
        if answer is not None:
            text = answer
        await receive_telegram_webhook(
            update=_update(
                message=_TelegramIncomingMessage(
                    text=text,
                    date=1754586000,
                    chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                    from_=_TelegramSender(id=222, first_name="Fulano"),
                )
            ),
            x_telegram_bot_api_secret_token="correct-secret",
            adapters=adapters,  # type: ignore[arg-type]
            settings=_settings(),
            session=session,
        )

    assert fake_user.username == "joaosilva"
    assert fake_user.email is None
    assert fake_user.favorite_stores == ["kabum", "pichau"]
    assert fake_user.preferred_categories == ["hardware", "video_games"]
    assert fake_user.registration_step is None
    assert adapter.calls == []
    assert "cadastro confirmado" in send_calls[-1][1].lower()
    assert "#set_password:opaque" in send_calls[-1][1]


@pytest.mark.anyio
async def test_first_contact_start_shows_presentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: primeiro contato real (`created_now=True`) via `/start`."""
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user, created_now=True)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/start",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "Cláudio" in send_calls[0][1]
    assert "/cadastro" in send_calls[0][1]


@pytest.mark.anyio
async def test_first_contact_free_text_shows_presentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: primeiro contato real via texto livre, sem chamar a IA."""
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user, created_now=True)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="oi, quero uma placa de vídeo",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "Cláudio" in send_calls[0][1]


@pytest.mark.anyio
async def test_first_contact_cadastro_is_not_intercepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: `/cadastro` funciona normalmente mesmo em primeiro contato."""
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user, created_now=True)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/cadastro",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert fake_user.registration_step == "username"
    assert "Cláudio" not in send_calls[0][1]


@pytest.mark.anyio
async def test_first_contact_ajuda_is_not_intercepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: `/ajuda` funciona normalmente mesmo em primeiro contato."""
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user, created_now=True)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/ajuda",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "COMPRAS" in send_calls[0][1]
    assert "Cláudio" not in send_calls[0][1]


@pytest.mark.anyio
async def test_start_returning_user_without_session_shows_login_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: usuário já cadastrado, sem sessão -- `/start` orienta a
    usar `/entrar`/`/recuperar` em vez do texto genérico antigo."""
    fake_user = _fake_user(registration_step=None)
    fake_user.username = "joaosilva"
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/start",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "você voltou" in send_calls[0][1].lower()
    assert "/entrar" in send_calls[0][1]


@pytest.mark.anyio
async def test_ajuda_command_shows_grouped_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: `/ajuda` organizado por grupos, sem mencionar `/senha`."""
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/ajuda",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    body = send_calls[0][1]
    assert "COMPRAS" in body
    assert "CONTA" in body
    assert "CONFIGURAÇÕES" in body
    assert "/criar_missao" in body
    assert "/cancelar_missao" in body
    assert "/senha" not in body


@pytest.mark.anyio
async def test_senha_is_not_a_public_command_or_authentication_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    fake_user.username = "cliente"
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    issue = AsyncMock()
    monkeypatch.setattr("app.telegram.router.issue_action_link_async", issue)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/senha",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(auth_public_base_url="https://auth.example.test"),
        session=_async_session(),
    )

    assert all(command["command"] != "senha" for command in _COMMANDS)
    issue.assert_not_awaited()
    assert adapter.calls == []
    assert "/recuperar" in send_calls[0][1]


@pytest.mark.anyio
async def test_missao_command_shows_mission_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: `/missao` explica com exemplos, sem exigir sessão ativa."""
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/missao",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "9800X3D" in send_calls[0][1]


@pytest.mark.anyio
async def test_recuperar_without_credential_offers_set_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: `/senha` deixou de existir -- `/recuperar` sem
    `UserCredential` cadastrado gera a primeira senha (`SET_PASSWORD`)."""
    fake_user = _fake_user()
    fake_user.username = "cliente"
    _patch_user(monkeypatch, fake_user)
    _patch_send_message(monkeypatch)
    calls: list[dict] = []

    async def issue(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            url="https://auth.example.test/auth#token",
            expires_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.telegram.router.issue_action_link_async", issue)
    session = _async_session()
    session.get.return_value = None  # nenhum UserCredential ainda
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/recuperar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert calls[0]["action"].value == "set_password"
    assert adapter.calls == []


@pytest.mark.anyio
async def test_recuperar_with_credential_offers_recover_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078: `/recuperar` com `UserCredential` já existente gera
    recuperação real (`RECOVER_PASSWORD`), nunca `CHANGE_PASSWORD`."""
    fake_user = _fake_user()
    fake_user.username = "cliente"
    _patch_user(monkeypatch, fake_user)
    _patch_send_message(monkeypatch)
    calls: list[dict] = []

    async def issue(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            url="https://auth.example.test/auth#token",
            expires_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.telegram.router.issue_action_link_async", issue)
    session = _async_session()
    session.get.return_value = SimpleNamespace()  # já existe UserCredential
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/recuperar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert calls[0]["action"].value == "recover_password"
    assert adapter.calls == []


@pytest.mark.anyio
async def test_upgrade_command_replies_statically_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/upgrade",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "em breve" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_preferences_command_queries_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/preferencias",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "quedas de preço: ativadas" in send_calls[0][1].lower()
    assert "preço-alvo atingido: ativadas" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_preferences_command_disables_only_requested_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/preferencias quedas desativar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert adapter.calls == []
    assert fake_user.notify_price_decreases is False
    assert fake_user.notify_target_reached is True


@pytest.mark.anyio
async def test_private_webhook_message_remembers_notification_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="oi",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert fake_user.telegram_chat_id == 222


@pytest.mark.anyio
async def test_start_remains_available_without_password_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/start",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert response.status_code == 204
    assert "/entrar" in sends[0][1]
    assert adapter.calls == []


@pytest.mark.anyio
async def test_sensitive_message_is_blocked_without_password_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    adapter = _FakeAdapter(_intent())

    await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert "sessão não está ativa" in sends[0][1].lower()
    assert adapter.calls == []


@pytest.mark.anyio
async def test_login_command_issues_server_bound_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    fake_user.username = "cliente"
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=False)
    )
    calls: list[dict] = []

    async def issue(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            url="https://auth.example.test/auth#login:opaque",
            expires_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.telegram.router.issue_action_link_async", issue)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/entrar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(auth_public_base_url="https://auth.example.test"),
        session=_async_session(),
    )

    assert calls[0]["user"] is fake_user
    assert calls[0]["action"].value == "login"
    assert "#login:opaque" in sends[0][1]
    assert adapter.calls == []


@pytest.mark.anyio
async def test_login_command_blocked_when_already_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078 (correção pós-deploy): `/entrar` com sessão ativa não gera
    novo link -- mesma proteção que `/cadastro` já tinha (TASK-072)."""
    fake_user = _fake_user()
    fake_user.username = "cliente"
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    calls: list[dict] = []

    async def issue(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            url="https://auth.example.test/auth#login:opaque",
            expires_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.telegram.router.issue_action_link_async", issue)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/entrar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(auth_public_base_url="https://auth.example.test"),
        session=_async_session(),
    )

    assert calls == []
    assert "já está autenticado" in sends[0][1].lower()
    assert adapter.calls == []


@pytest.mark.anyio
async def test_recuperar_still_works_when_already_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-078 (correção pós-deploy): diferente de `/entrar`, `/recuperar`
    continua liberado com sessão ativa -- trocar/recuperar senha logado é
    um caso legítimo."""
    fake_user = _fake_user()
    fake_user.username = "cliente"
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    calls: list[dict] = []

    async def issue(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            url="https://auth.example.test/auth#recover:opaque",
            expires_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.telegram.router.issue_action_link_async", issue)
    session = _async_session()
    session.get.return_value = None  # sem UserCredential ainda -> SET_PASSWORD
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/recuperar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(auth_public_base_url="https://auth.example.test"),
        session=session,
    )

    assert calls[0]["user"] is fake_user
    assert calls[0]["action"].value == "set_password"
    assert "#recover:opaque" in sends[0][1]
    assert adapter.calls == []


@pytest.mark.anyio
async def test_logout_revokes_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    logout_calls: list[object] = []

    async def _fake_logout(session: object, *, user: object) -> None:
        logout_calls.append(user)

    monkeypatch.setattr("app.telegram.router.logout_async", _fake_logout)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/sair",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    assert logout_calls == [fake_user]
    assert "Sessão encerrada" in sends[0][1]


# Correção pontual de entrada: IA somente após /criar_missao.


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/criar_missao", "/criar-missao"])
async def test_create_mission_command_starts_user_scoped_waiting_state(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must not run on command"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    reply = await _handle_message(
        _message(command),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert "me diga o que você quer encontrar" in reply.lower()
    assert user.pending_intent["kind"] == "await_create_mission_description"
    assert datetime.fromisoformat(user.pending_intent["expires_at"]) > datetime.now(UTC)


@pytest.mark.anyio
async def test_next_message_after_create_command_calls_ai_once_and_consumes_waiting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _registered_user(pending_intent=_awaiting_mission_description())
    intent = _intent(
        kind=IntentKind.CREATE_MISSION,
        parameters=IntentParameters(
            search_query="Ryzen 7 9800X3D",
            sources=("kabum",),
        ),
    )
    adapter = _FakeAdapter(intent)
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    reply = await _handle_message(
        _message("Quero um Ryzen 7 9800X3D"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert len(adapter.calls) == 1
    assert adapter.calls[0][0].text == "Quero um Ryzen 7 9800X3D"
    assert user.pending_intent["kind"] == "create_mission"
    assert "confirmar nova missão" in reply.lower()


@pytest.mark.anyio
async def test_create_waiting_state_of_user_a_does_not_affect_user_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a = _registered_user(pending_intent=_awaiting_mission_description())
    user_b = _registered_user()
    user_b.id = uuid4()
    user_b.telegram_user_id = 333
    adapter = _FakeAdapter(AssertionError("AI must not run for user B"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    reply = await _handle_message(
        _message("quero uma RTX 5070", user_id=333),
        user=user_b,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert "/criar_missao" in reply
    assert user_a.pending_intent["kind"] == "await_create_mission_description"
    assert user_b.pending_intent is None


@pytest.mark.anyio
async def test_expired_create_waiting_state_is_cleared_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _registered_user(
        pending_intent={
            "kind": "await_create_mission_description",
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )
    adapter = _FakeAdapter(AssertionError("AI must not run after expiry"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    reply = await _handle_message(
        _message("quero uma placa de vídeo"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent is None
    assert "tempo para descrever a missão acabou" in reply.lower()


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/cancelar_missao", "/cancelar-missao"])
async def test_cancel_mission_command_with_one_candidate_stages_without_ai(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    user = _registered_user()
    mission = _fake_mission(title="RTX 5070", status=MissionStatus.ACTIVE)
    adapter = _FakeAdapter(AIProviderUnavailable())
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router.list_mission_command_candidates",
        AsyncMock(return_value=[mission]),
    )
    reply = await _handle_message(
        _message(command),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent["command"] == "cancel"
    assert user.pending_intent["mission_id"] == str(mission.id)
    assert "cancelar" in reply.lower()


@pytest.mark.anyio
async def test_cancel_mission_numeric_choice_executes_immediately_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-090: `/cancelar_missao` com mais de uma candidata agora reusa a
    mesma infraestrutura genérica de seleção múltipla da TASK-085
    (`kind: "mission_command_choice"`) -- a resposta numérica já executa o
    cancelamento e devolve o resultado, sem um segundo par sim/não."""
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.ACTIVE),
        _fake_mission(title="RTX", status=MissionStatus.PAUSED),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run in cancellation"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router.list_mission_command_candidates",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    session.get = AsyncMock(return_value=SimpleNamespace(user_id=user.id))
    transition = SimpleNamespace(to_status=MissionStatus.CANCELLED)
    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async",
        AsyncMock(return_value=transition),
    )
    first = await _handle_message(
        _message("/cancelar_missao"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    second = await _handle_message(
        _message("2"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert "1 — Ryzen" in first and "2 — RTX" in first
    assert "cancelar" in first.lower()
    assert "Exemplo: 1,3" in first
    assert user.pending_intent is None
    assert '2 — "RTX" — cancelada' in second


@pytest.mark.anyio
async def test_cancel_mission_multi_selection_cancels_several_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prova explícita de #4/#5 do escopo: "1,3" cancela duas missões numa
    única resposta, sem duplicar e sem nenhuma chamada ao IntentInterpreter
    nem a provider de IA."""
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.ACTIVE),
        _fake_mission(title="RTX", status=MissionStatus.PAUSED),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run in cancellation"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router.list_mission_command_candidates",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    session.get = AsyncMock(return_value=SimpleNamespace(user_id=user.id))
    transition_calls: list[dict] = []

    async def _fake_transition(session: object, **kwargs: object) -> SimpleNamespace:
        transition_calls.append(kwargs)
        return SimpleNamespace(to_status=MissionStatus.CANCELLED)

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fake_transition
    )
    await _handle_message(
        _message("/cancelar_missao"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    reply = await _handle_message(
        _message("1,1,3"),  # duplicata proposital do índice 1
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert len(transition_calls) == 2  # "1" deduplicado, "3" processado uma vez
    assert transition_calls[0]["mission_id"] == missions[0].id
    assert transition_calls[1]["mission_id"] == missions[2].id
    assert '1 — "Ryzen" — cancelada' in reply
    assert '3 — "RTX" — cancelada' in reply
    assert "Cadeira gamer" not in reply
    assert user.pending_intent is None


@pytest.mark.anyio
async def test_invalid_cancel_mission_choice_is_deterministic_and_keeps_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _registered_user(
        pending_intent={
            "kind": "mission_command_choice",
            "command": "cancel",
            "missions": [
                {
                    "mission_id": str(uuid4()),
                    "mission_title": "Ryzen",
                    "expected_state_version": 1,
                },
                {
                    "mission_id": str(uuid4()),
                    "mission_title": "RTX",
                    "expected_state_version": 1,
                },
            ],
        }
    )
    original = dict(user.pending_intent)
    adapter = _FakeAdapter(AssertionError("AI must not run for invalid choice"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    reply = await _handle_message(
        _message("7"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent == original
    assert reply == (
        "Não entendi.\n\nDigite o número de uma ou mais missões da lista, "
        "separados por vírgula.\n\nExemplo: 1 ou 1,3"
    )


@pytest.mark.anyio
async def test_cancel_mission_partial_invalid_selection_rejects_entire_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auditoria do usuário: "1,3,99" com só 3 candidatas (99 inválido)
    não pode cancelar 1 e 3 parcialmente -- a resposta inteira é
    rejeitada, sem nenhuma transição executada."""
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.ACTIVE),
        _fake_mission(title="RTX", status=MissionStatus.PAUSED),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run in cancellation"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router.list_mission_command_candidates",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    transition = AsyncMock(
        side_effect=AssertionError("transition must not run on invalid selection")
    )
    monkeypatch.setattr("app.telegram.router.transition_mission_async", transition)
    await _handle_message(
        _message("/cancelar_missao"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    original = dict(user.pending_intent)

    reply = await _handle_message(
        _message("1,3,99"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    transition.assert_not_awaited()
    assert user.pending_intent == original
    assert "Não entendi" in reply


@pytest.mark.anyio
async def test_cancel_mission_choice_only_resolves_missions_from_own_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-090 (#11): o número digitado é só um índice para o payload já
    gravado no `pending_intent` no momento da listagem -- nunca um ID real
    nem uma nova consulta ao banco por outro usuário."""
    other_users_mission_id = uuid4()
    user = _registered_user(
        pending_intent={
            "kind": "mission_command_choice",
            "command": "cancel",
            "missions": [
                {
                    "mission_id": str(other_users_mission_id),
                    "mission_title": "Ryzen",
                    "expected_state_version": 1,
                },
            ],
        }
    )
    session = _async_session()
    session.get = AsyncMock(
        return_value=SimpleNamespace(user_id=uuid4())
    )  # dono diferente
    adapter = _FakeAdapter(AssertionError("AI must never run in cancellation"))

    reply = await _handle_message(
        _message("1"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert "não encontrada" in reply
    assert user.pending_intent is None


@pytest.mark.anyio
@pytest.mark.parametrize("answer", ["sim", "s", "1"])
async def test_confirmation_yes_vocabulary_executes_without_ai(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    user = _registered_user(
        pending_intent={
            "kind": "mission_command",
            "mission_id": str(uuid4()),
            "mission_title": "RTX",
            "command": "cancel",
            "expected_state_version": 1,
        }
    )
    execute = AsyncMock(return_value="cancelada")
    monkeypatch.setattr("app.telegram.router._execute_pending_intent", execute)
    adapter = _FakeAdapter(AIProviderUnavailable())
    reply = await _resolve_pending_intent(
        _message(answer),
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        user=user,
    )

    assert reply == "cancelada"
    assert execute.await_count == 1
    assert adapter.calls == []
    assert user.pending_intent is None


@pytest.mark.anyio
@pytest.mark.parametrize("answer", ["não", "nao", "n", "2"])
async def test_confirmation_no_vocabulary_declines_without_ai(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    user = _registered_user(
        pending_intent={
            "kind": "mission_command",
            "mission_id": str(uuid4()),
            "mission_title": "RTX",
            "command": "cancel",
            "expected_state_version": 1,
        }
    )
    execute = AsyncMock()
    monkeypatch.setattr("app.telegram.router._execute_pending_intent", execute)
    adapter = _FakeAdapter(AIProviderUnavailable())
    reply = await _resolve_pending_intent(
        _message(answer),
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        user=user,
    )

    assert "cancelei" in reply.lower()
    execute.assert_not_awaited()
    assert adapter.calls == []
    assert user.pending_intent is None


@pytest.mark.anyio
async def test_ambiguous_confirmation_retries_without_ai_and_keeps_state() -> None:
    pending = {
        "kind": "mission_command",
        "mission_id": str(uuid4()),
        "mission_title": "RTX",
        "command": "cancel",
        "expected_state_version": 1,
    }
    user = _registered_user(pending_intent=dict(pending))
    adapter = _FakeAdapter(AIProviderUnavailable())
    reply = await _resolve_pending_intent(
        _message("talvez"),
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        user=user,
    )

    assert "pode responder" in reply.lower()
    assert adapter.calls == []
    assert user.pending_intent == pending


@pytest.mark.anyio
async def test_pause_command_with_one_active_mission_stages_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _registered_user()
    mission = _fake_mission(title="RTX 5070", status=MissionStatus.ACTIVE)
    adapter = _FakeAdapter(AssertionError("AI must never run for /pausar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=[mission]),
    )
    reply = await _handle_message(
        _message("/pausar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent["kind"] == "mission_command"
    assert user.pending_intent["command"] == "pause"
    assert user.pending_intent["mission_id"] == str(mission.id)
    assert "pausar" in reply.lower()


@pytest.mark.anyio
async def test_pause_command_multi_selection_pauses_several_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.ACTIVE),
        _fake_mission(title="RTX", status=MissionStatus.ACTIVE),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run for /pausar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    session.get = AsyncMock(return_value=SimpleNamespace(user_id=user.id))
    transition_calls: list[dict] = []

    async def _fake_transition(session: object, **kwargs: object) -> SimpleNamespace:
        transition_calls.append(kwargs)
        return SimpleNamespace(to_status=MissionStatus.PAUSED)

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fake_transition
    )
    first = await _handle_message(
        _message("/pausar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    second = await _handle_message(
        _message("1, 3"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert "pausar" in first.lower()
    assert len(transition_calls) == 2
    assert all(call["command"].value == "pause" for call in transition_calls)
    assert '1 — "Ryzen" — pausada' in second
    assert '3 — "RTX" — pausada' in second
    assert "Cadeira gamer" not in second
    assert user.pending_intent is None


@pytest.mark.anyio
async def test_pause_command_invalid_selection_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.ACTIVE),
        _fake_mission(title="RTX", status=MissionStatus.ACTIVE),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run for /pausar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    await _handle_message(
        _message("/pausar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    original = dict(user.pending_intent)

    reply = await _handle_message(
        _message("1,abc"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent == original
    assert "Não entendi" in reply


@pytest.mark.anyio
async def test_pause_command_partial_invalid_selection_rejects_entire_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auditoria do usuário: "1,3,99" com só 3 candidatas (99 inválido)
    não pode pausar 1 e 3 parcialmente -- a resposta inteira é rejeitada,
    sem nenhuma transição executada."""
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.ACTIVE),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.ACTIVE),
        _fake_mission(title="RTX", status=MissionStatus.ACTIVE),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run for /pausar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    transition = AsyncMock(
        side_effect=AssertionError("transition must not run on invalid selection")
    )
    monkeypatch.setattr("app.telegram.router.transition_mission_async", transition)
    await _handle_message(
        _message("/pausar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    original = dict(user.pending_intent)

    reply = await _handle_message(
        _message("1,3,99"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    transition.assert_not_awaited()
    assert user.pending_intent == original
    assert "Não entendi" in reply


@pytest.mark.anyio
async def test_pause_command_with_no_active_missions_replies_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run for /pausar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=[]),
    )
    reply = await _handle_message(
        _message("/pausar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent is None
    assert "nenhuma missão ativa" in reply.lower()


@pytest.mark.anyio
async def test_resume_command_with_one_paused_mission_stages_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _registered_user()
    mission = _fake_mission(title="RTX 5070", status=MissionStatus.PAUSED)
    adapter = _FakeAdapter(AssertionError("AI must never run for /retomar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=[mission]),
    )
    reply = await _handle_message(
        _message("/retomar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent["kind"] == "mission_command"
    assert user.pending_intent["command"] == "resume"
    assert user.pending_intent["mission_id"] == str(mission.id)
    assert "retomar" in reply.lower()


@pytest.mark.anyio
async def test_resume_command_multi_selection_resumes_several_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.PAUSED),
        _fake_mission(title="RTX", status=MissionStatus.PAUSED),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run for /retomar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    session.get = AsyncMock(return_value=SimpleNamespace(user_id=user.id))
    transition_calls: list[dict] = []

    async def _fake_transition(session: object, **kwargs: object) -> SimpleNamespace:
        transition_calls.append(kwargs)
        return SimpleNamespace(to_status=MissionStatus.ACTIVE)

    monkeypatch.setattr(
        "app.telegram.router.transition_mission_async", _fake_transition
    )
    await _handle_message(
        _message("/retomar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    reply = await _handle_message(
        _message("1,2"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert len(transition_calls) == 2
    assert all(call["command"].value == "resume" for call in transition_calls)
    assert '1 — "Ryzen" — ativa' in reply
    assert '2 — "RTX" — ativa' in reply
    assert user.pending_intent is None


@pytest.mark.anyio
async def test_resume_command_partial_invalid_selection_rejects_entire_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auditoria do usuário: "1,3,99" com só 3 candidatas (99 inválido)
    não pode retomar 1 e 3 parcialmente -- a resposta inteira é
    rejeitada, sem nenhuma transição executada."""
    missions = [
        _fake_mission(title="Ryzen", status=MissionStatus.PAUSED),
        _fake_mission(title="Cadeira gamer", status=MissionStatus.PAUSED),
        _fake_mission(title="RTX", status=MissionStatus.PAUSED),
    ]
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run for /retomar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=missions),
    )
    session = _async_session()
    transition = AsyncMock(
        side_effect=AssertionError("transition must not run on invalid selection")
    )
    monkeypatch.setattr("app.telegram.router.transition_mission_async", transition)
    await _handle_message(
        _message("/retomar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )
    original = dict(user.pending_intent)

    reply = await _handle_message(
        _message("1,3,99"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=session,
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    transition.assert_not_awaited()
    assert user.pending_intent == original
    assert "Não entendi" in reply


@pytest.mark.anyio
async def test_resume_command_with_no_paused_missions_replies_without_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _registered_user()
    adapter = _FakeAdapter(AssertionError("AI must never run for /retomar"))
    monkeypatch.setattr(
        "app.telegram.router.has_active_session_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.telegram.router._query_missions_by_status",
        AsyncMock(return_value=[]),
    )
    reply = await _handle_message(
        _message("/retomar"),
        user=user,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert adapter.calls == []
    assert user.pending_intent is None
    assert "nenhuma missão pausada" in reply.lower()


def test_registered_telegram_commands_use_only_bot_api_compatible_names() -> None:
    names = {command["command"] for command in _COMMANDS}

    assert {
        "criar_missao",
        "cancelar_missao",
        "pausar",
        "retomar",
        "editar_missao",
        "listar_missoes",
    } <= names
    assert all(
        name.replace("_", "").isalnum() and name == name.lower() for name in names
    )
    assert all("-" not in name for name in names)


# --- Subtask 7 da auditoria GG Oferta: /ajuda honesto, /suporte,
# /sugerir_loja e Futuro determinístico de Shopee/AliExpress -------------


@pytest.mark.anyio
async def test_ajuda_lists_vincular_suporte_sugerir_loja_and_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/ajuda",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    body = send_calls[0][1]
    assert "/vincular" in body
    assert "/suporte" in body
    assert "/sugerir_loja" in body
    assert "ggoferta.com" in body
    assert adapter.calls == []


@pytest.mark.anyio
async def test_ajuda_labels_upgrade_as_futuro_not_functional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/ajuda",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=_async_session(),
    )

    body = send_calls[0][1]
    assert "/upgrade" in body
    upgrade_line = next(line for line in body.splitlines() if "/upgrade" in line)
    assert "futuro" in upgrade_line.lower()


@pytest.mark.anyio
async def test_support_command_starts_type_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _fake_user()
    reply = await _handle_message(
        _message("/suporte"),
        user=user,
        adapters=_adapters(_FakeAdapter(AssertionError("AI must not run"))),  # type: ignore[arg-type]
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert user.pending_intent == {"kind": "await_support_type"}
    assert "Erro/Bug" in reply


@pytest.mark.anyio
async def test_support_type_invalid_choice_retries_without_clearing_state() -> None:
    user = _registered_user(pending_intent={"kind": "await_support_type"})

    reply = await _resolve_pending_intent(
        _message("9"), adapters={}, session=_async_session(), user=user
    )

    assert user.pending_intent == {"kind": "await_support_type"}
    assert "não entendi" in reply.lower()


def test_support_description_empty_retries_without_clearing_state() -> None:
    """`TelegramMessage` já recusa texto em branco no contrato, então esta
    validação nunca é exercitada pelo webhook real -- testada direto na
    função pura, mesmo padrão de `parse_target_amount_entry("")` em
    `tests/test_telegram_confirmation.py`."""
    user = _registered_user(
        pending_intent={"kind": "await_support_description", "feedback_kind": "bug"}
    )

    reply = _apply_support_description("   ", user=user)

    assert user.pending_intent == {
        "kind": "await_support_description",
        "feedback_kind": "bug",
    }
    assert "vazia" in reply.lower()


@pytest.mark.anyio
async def test_support_flow_end_to_end_persists_only_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = AsyncMock()
    monkeypatch.setattr("app.telegram.router.create_feedback_async", create)
    user = _registered_user(pending_intent={"kind": "await_support_type"})
    session = _async_session()

    type_reply = await _resolve_pending_intent(
        _message("1"), adapters={}, session=session, user=user
    )
    assert user.pending_intent["kind"] == "await_support_description"
    assert "Descreva" in type_reply

    description_reply = await _resolve_pending_intent(
        _message("O gráfico de preço não carrega."),
        adapters={},
        session=session,
        user=user,
    )
    assert user.pending_intent["kind"] == "support_feedback"
    assert "Confirmar envio" in description_reply
    create.assert_not_awaited()

    _patch_resolve_answer(monkeypatch, True)
    confirm_reply = await _resolve_pending_intent(
        _message("sim"), adapters={}, session=session, user=user
    )

    create.assert_awaited_once()
    assert create.await_args.kwargs["kind"] == FeedbackKind.BUG
    assert create.await_args.kwargs["channel"] == FeedbackChannel.TELEGRAM
    assert create.await_args.kwargs["message"] == "O gráfico de preço não carrega."
    assert user.pending_intent is None
    assert "prazo" not in confirm_reply.lower()


@pytest.mark.anyio
async def test_support_flow_cancel_does_not_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = AsyncMock()
    monkeypatch.setattr("app.telegram.router.create_feedback_async", create)
    user = _registered_user(
        pending_intent={
            "kind": "support_feedback",
            "feedback_kind": "bug",
            "message": "erro qualquer",
        }
    )
    _patch_resolve_answer(monkeypatch, False)

    reply = await _resolve_pending_intent(
        _message("não"), adapters={}, session=_async_session(), user=user
    )

    create.assert_not_awaited()
    assert user.pending_intent is None
    assert "cancelei" in reply.lower()


@pytest.mark.anyio
async def test_suggest_store_command_starts_name_prompt() -> None:
    user = _fake_user()
    reply = await _handle_message(
        _message("/sugerir_loja"),
        user=user,
        adapters={},
        session=_async_session(),
        auth_public_base_url="https://example.test",
        created_now=False,
    )

    assert user.pending_intent == {"kind": "await_store_suggestion_name"}
    assert "loja" in reply.lower()


def test_suggest_store_name_empty_retries_without_clearing_state() -> None:
    """Mesma razão de `test_support_description_empty_retries_without_clearing_state`:
    `TelegramMessage` nunca entrega texto em branco, então a validação é
    testada direto na função pura."""
    user = _registered_user(pending_intent={"kind": "await_store_suggestion_name"})

    reply = _apply_store_suggestion_name("", user=user)

    assert user.pending_intent == {"kind": "await_store_suggestion_name"}
    assert "vazio" in reply.lower()


@pytest.mark.anyio
async def test_suggest_store_flow_skips_optional_url_and_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = AsyncMock()
    monkeypatch.setattr("app.telegram.router.create_feedback_async", create)
    user = _registered_user(pending_intent={"kind": "await_store_suggestion_name"})
    session = _async_session()

    await _resolve_pending_intent(
        _message("Loja Nova"), adapters={}, session=session, user=user
    )
    assert user.pending_intent["kind"] == "await_store_suggestion_url"

    await _resolve_pending_intent(
        _message("0"), adapters={}, session=session, user=user
    )
    assert user.pending_intent["kind"] == "await_store_suggestion_comment"
    assert user.pending_intent["store_url"] is None

    confirm_prompt = await _resolve_pending_intent(
        _message("pular"), adapters={}, session=session, user=user
    )
    assert user.pending_intent["kind"] == "store_suggestion"
    assert "Loja Nova" in confirm_prompt

    _patch_resolve_answer(monkeypatch, True)
    await _resolve_pending_intent(
        _message("sim"), adapters={}, session=session, user=user
    )

    create.assert_awaited_once()
    assert create.await_args.kwargs["kind"] == FeedbackKind.STORE_SUGGESTION
    assert create.await_args.kwargs["store_name"] == "Loja Nova"
    assert create.await_args.kwargs["store_url"] is None
    assert create.await_args.kwargs["message"] is None
    assert user.pending_intent is None


@pytest.mark.anyio
async def test_suggest_store_flow_with_url_and_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = AsyncMock()
    monkeypatch.setattr("app.telegram.router.create_feedback_async", create)
    user = _registered_user(
        pending_intent={
            "kind": "await_store_suggestion_url",
            "store_name": "Loja Nova",
        }
    )
    session = _async_session()

    await _resolve_pending_intent(
        _message("https://lojanova.example.com"),
        adapters={},
        session=session,
        user=user,
    )
    assert user.pending_intent["store_url"] == "https://lojanova.example.com"

    await _resolve_pending_intent(
        _message("Vende peças raras"), adapters={}, session=session, user=user
    )
    assert user.pending_intent["kind"] == "store_suggestion"
    assert user.pending_intent["comment"] == "Vende peças raras"

    _patch_resolve_answer(monkeypatch, True)
    await _resolve_pending_intent(
        _message("sim"), adapters={}, session=session, user=user
    )

    assert create.await_args.kwargs["store_url"] == "https://lojanova.example.com"
    assert create.await_args.kwargs["message"] == "Vende peças raras"


@pytest.mark.anyio
async def test_suggest_store_url_rejects_javascript_scheme_and_keeps_state() -> None:
    user = _registered_user(
        pending_intent={
            "kind": "await_store_suggestion_url",
            "store_name": "Loja Nova",
        }
    )

    reply = await _resolve_pending_intent(
        _message("javascript:alert(1)"),
        adapters={},
        session=_async_session(),
        user=user,
    )

    assert "http" in reply.lower()
    assert user.pending_intent == {
        "kind": "await_store_suggestion_url",
        "store_name": "Loja Nova",
    }


@pytest.mark.anyio
async def test_shopee_mention_during_create_mission_sources_replies_futuro() -> None:
    user = _registered_user(
        pending_intent={
            "kind": "await_create_mission_sources",
            "search_query": "rtx 5070",
            "model": None,
            "display_query": None,
            "target_amount": None,
            "target_currency": None,
        }
    )

    reply = await _resolve_pending_intent(
        _message("Shopee"), adapters={}, session=_async_session(), user=user
    )

    assert "shopee" in reply.lower()
    assert "futuro" in reply.lower()
    assert user.pending_intent["kind"] == "await_create_mission_sources"


@pytest.mark.anyio
async def test_aliexpress_mention_during_edit_add_sources_replies_futuro() -> None:
    user = _registered_user(
        pending_intent={
            "kind": "await_edit_add_sources",
            "mission_id": str(uuid4()),
            "mission_title": "RTX 5070 Ti",
            "expected_state_version": 1,
            "current_sources": ["pichau"],
            "option_map": {"1": "kabum", "2": "amazon"},
            "auto_paused": False,
        }
    )

    reply = await _resolve_pending_intent(
        _message("quero da aliexpress"),
        adapters={},
        session=_async_session(),
        user=user,
    )

    assert "aliexpress" in reply.lower()
    assert "futuro" in reply.lower()
    assert user.pending_intent["kind"] == "await_edit_add_sources"


@pytest.mark.anyio
async def test_change_password_command_delivers_code_in_reply_never_asks_for_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subtask 9: `/alterar_senha` nunca aceita a senha nova no chat -- só
    entrega um código curto (o próprio texto da resposta é a entrega) e
    direciona para a página Web."""
    user = _fake_user()
    user.telegram_chat_id = 222
    challenge = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        "app.telegram.router.create_challenge_async",
        AsyncMock(return_value=(challenge, "482913")),
    )

    reply = await _handle_message(
        _message("/alterar_senha"),
        user=user,
        adapters={},
        session=_async_session(),
        auth_public_base_url="https://ggoferta.com",
        created_now=False,
        verification_code_pepper="pepper-de-teste-nao-real",
    )

    assert "482913" in reply
    assert "senha" in reply.lower()
    assert "recuperar" in reply.lower()
    assert "nunca" in reply.lower()


@pytest.mark.anyio
async def test_change_password_command_without_linked_chat_replies_gracefully() -> None:
    user = _fake_user()
    user.telegram_chat_id = None

    reply = await _handle_message(
        _message("/alterar_senha"),
        user=user,
        adapters={},
        session=_async_session(),
        auth_public_base_url="https://ggoferta.com",
        created_now=False,
    )

    assert "não encontrei" in reply.lower()


@pytest.mark.anyio
async def test_change_password_command_rate_limited_replies_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.authentication.verification import ChallengeRateLimited

    user = _fake_user()
    user.telegram_chat_id = 222

    async def _fail(*a: object, **k: object) -> None:
        raise ChallengeRateLimited("Tente novamente mais tarde.")

    monkeypatch.setattr("app.telegram.router.create_challenge_async", _fail)

    reply = await _handle_message(
        _message("/alterar_senha"),
        user=user,
        adapters={},
        session=_async_session(),
        auth_public_base_url="https://ggoferta.com",
        created_now=False,
        verification_code_pepper="pepper-de-teste-nao-real",
    )

    assert "muitas solicitações" in reply.lower()


@pytest.mark.anyio
async def test_shopee_mention_in_free_text_description_skips_ai_entirely() -> None:
    user = _registered_user(pending_intent=_awaiting_mission_description())
    adapter = _FakeAdapter(AssertionError("AI must not run"))

    reply = await _resolve_pending_intent(
        _message("Quero um mouse da Shopee"),
        adapters=_adapters(adapter),
        session=_async_session(),
        user=user,
    )

    assert adapter.calls == []
    assert "shopee" in reply.lower()
    assert "futuro" in reply.lower()
    assert user.pending_intent is None
