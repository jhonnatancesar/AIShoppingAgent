"""Fluxo real do webhook do Telegram contra PostgreSQL real -- criação de
missão quando a cota de missões ativas já está no limite.

Regressão: `create_mission_from_criteria_async` grava (`flush`) a missão
`DRAFT`, os critérios e as fontes ANTES de `transition_mission_async`
checar a cota e levantar `QuotaExceededError`. O handler do webhook
(`_resolve_pending_intent`) captura esse erro e devolve uma mensagem
amigável ao usuário, mas o commit final de "Fase C"
(`_process_authenticated_message`, comentário "Fase C concluída.") roda
incondicionalmente depois -- sem isso, a missão `DRAFT`/critérios/fontes
já seriam persistidos de verdade, mesmo com a criação recusada
("missão parcial")."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from app.authentication.models import UserAuthSession
from app.core.config import Settings
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.stores.models import Store
from app.telegram.contracts import TelegramChatType
from app.telegram.limits import TelegramUpdateReservation
from app.telegram.models import TelegramUpdateDisposition
from app.telegram.router import (
    TelegramUpdate,
    _TelegramChat,
    _TelegramIncomingMessage,
    _TelegramSender,
    receive_telegram_webhook,
)
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _seed_user(sessions, *, telegram_user_id: int, pending_intent: dict | None) -> User:
    with sessions.begin() as session:
        user = User(
            display_name="quota-user",
            role=UserRole.USER,
            username=f"quota-user-{telegram_user_id}",
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_user_id,
            is_active=True,
            pending_intent=pending_intent,
        )
        session.add(user)
        session.flush()
        authenticated_at = datetime.now(UTC)
        session.add(
            UserAuthSession(
                user_id=user.id,
                telegram_user_id=telegram_user_id,
                authenticated_at=authenticated_at,
                expires_at=authenticated_at + timedelta(hours=12),
            )
        )
        session.flush()
        session.expunge(user)
    return user


def _seed_active_mission(sessions, *, user_id, title: str) -> Mission:
    with sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        mission = Mission(
            user_id=user_id, title=title, status=MissionStatus.ACTIVE, state_version=0
        )
        session.add(mission)
        session.flush()
        session.add(MissionCriteria(mission_id=mission.id, search_query=title))
        session.add(MissionSource(mission_id=mission.id, store_id=store.id))
        session.add(
            MissionSchedule(
                mission_id=mission.id, interval_minutes=60, next_run_at=datetime.now(UTC)
            )
        )
        session.flush()
        session.expunge(mission)
    return mission


def _run_confirmed_create_mission_webhook(
    monkeypatch: pytest.MonkeyPatch, *, integration_database, user: User
) -> tuple[list[tuple[int, str]], object]:
    """Simula 'usuário confirma a criação da missão' chegando no webhook
    real: autenticação e trava de concorrência mockadas (não é o que este
    teste audita), sessão/engine/quota/persistência 100% reais."""

    async def _fake_get_or_create(session, *, telegram_user_id, display_name):
        return user, False

    monkeypatch.setattr(
        "app.telegram.authentication.get_or_create_telegram_user_async",
        _fake_get_or_create,
    )

    async def _fake_resolve_answer(text, **kwargs):
        return True

    monkeypatch.setattr("app.telegram.router.resolve_answer", _fake_resolve_answer)

    async def _fake_reserve(*args, **kwargs):
        return TelegramUpdateReservation(TelegramUpdateDisposition.ACCEPTED)

    monkeypatch.setattr("app.telegram.router.reserve_telegram_update", _fake_reserve)

    @asynccontextmanager
    async def _fake_lock(*args, **kwargs):
        yield

    monkeypatch.setattr("app.telegram.router.user_serialization_lock", _fake_lock)

    send_calls: list[tuple[int, str]] = []

    async def _fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text))

    monkeypatch.setattr("app.telegram.router.send_message", _fake_send_message)

    settings = Settings(
        _env_file=None,
        telegram_webhook_secret="correct-secret",
        telegram_bot_token="bot-token",
    )
    update = TelegramUpdate(
        update_id=1,
        message=_TelegramIncomingMessage(
            text="sim",
            date=1754586000,
            chat=_TelegramChat(id=user.telegram_user_id, type=TelegramChatType.PRIVATE),
            from_=_TelegramSender(id=user.telegram_user_id, first_name="Fulano"),
        ),
    )

    async def run():
        session = integration_database.async_sessions()
        try:
            return await receive_telegram_webhook(
                update=update,
                x_telegram_bot_api_secret_token="correct-secret",
                adapters={},
                settings=settings,
                session=session,
                engine=integration_database.async_engine,
            )
        finally:
            await session.rollback()
            await session.close()

    response = asyncio.run(run())
    return send_calls, response


def test_confirmed_create_mission_at_quota_limit_leaves_no_partial_mission(
    monkeypatch: pytest.MonkeyPatch, integration_database
) -> None:
    """Cota padrão (`default_max_active_missions=5`): usuário já tem 5
    missões ATIVAS reais e confirma a criação de uma 6ª -- deve ser
    recusada, o Telegram deve responder claramente, e NENHUMA missão
    nova (nem DRAFT) pode sobrar no banco."""
    user = _seed_user(
        integration_database.sessions,
        telegram_user_id=987654321,
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": ["pichau"],
        },
    )
    for index in range(5):
        _seed_active_mission(
            integration_database.sessions, user_id=user.id, title=f"missao ativa {index}"
        )

    send_calls, response = _run_confirmed_create_mission_webhook(
        monkeypatch, integration_database=integration_database, user=user
    )

    assert response.status_code == 204
    assert send_calls, "esperava uma resposta explicando a cota cheia"
    reply = send_calls[0][1]
    assert "5/5" in reply
    assert "missões ativas" in reply

    with integration_database.sessions.begin() as session:
        missions = session.scalars(select(Mission).where(Mission.user_id == user.id)).all()
        assert len(missions) == 5, (
            "nenhuma missao nova (nem DRAFT) deveria ter sido persistida "
            f"quando a criacao foi recusada por cota; encontradas: "
            f"{[(m.title, m.status) for m in missions]}"
        )


def test_confirmed_create_mission_within_quota_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, integration_database
) -> None:
    """Regressão inversa: a correção da cota não pode quebrar o caminho
    feliz -- dentro do limite, a missão continua sendo criada e ativada
    de verdade."""
    user = _seed_user(
        integration_database.sessions,
        telegram_user_id=123456789,
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": ["pichau"],
        },
    )

    send_calls, response = _run_confirmed_create_mission_webhook(
        monkeypatch, integration_database=integration_database, user=user
    )

    assert response.status_code == 204
    assert send_calls
    assert "Missão criada" in send_calls[0][1]

    with integration_database.sessions.begin() as session:
        missions = session.scalars(select(Mission).where(Mission.user_id == user.id)).all()
        assert len(missions) == 1
        assert missions[0].status == MissionStatus.ACTIVE


def test_confirmed_create_mission_exactly_at_quota_boundary_succeeds(
    monkeypatch: pytest.MonkeyPatch, integration_database
) -> None:
    """Limite exato: com 4/5 ativas, a 5ª confirmação ainda cabe na cota
    (`4 + 1 <= 5`) -- só a 6ª tentativa (teste acima) deve ser recusada."""
    user = _seed_user(
        integration_database.sessions,
        telegram_user_id=555555555,
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": ["pichau"],
        },
    )
    for index in range(4):
        _seed_active_mission(
            integration_database.sessions, user_id=user.id, title=f"missao ativa {index}"
        )

    send_calls, response = _run_confirmed_create_mission_webhook(
        monkeypatch, integration_database=integration_database, user=user
    )

    assert response.status_code == 204
    assert send_calls
    assert "Missão criada" in send_calls[0][1]

    with integration_database.sessions.begin() as session:
        missions = session.scalars(select(Mission).where(Mission.user_id == user.id)).all()
        assert len(missions) == 5
        active = [m for m in missions if m.status == MissionStatus.ACTIVE]
        assert len(active) == 5
