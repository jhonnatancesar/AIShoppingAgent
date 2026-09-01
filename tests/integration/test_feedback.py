"""Persistência real de UserFeedback (subtask 7 da auditoria GG Oferta) --
criação compartilhada (`create_feedback_async`, único caminho de escrita
usado por Web e Telegram), status inicial `new` e transição de status
pelo caminho ADMIN (`list_feedback`/`update_feedback_status`)."""

import asyncio

import pytest
from app.feedback import (
    FeedbackChannel,
    FeedbackKind,
    FeedbackStatus,
    count_feedback,
    create_feedback_async,
    list_feedback,
    update_feedback_status,
)
from app.users.models import User, UserRole

pytestmark = pytest.mark.integration


def _run(coro):
    return asyncio.run(coro)


def _seed_user(sessions, *, username: str) -> User:
    with sessions.begin() as session:
        user = User(display_name=username, role=UserRole.USER, username=username)
        session.add(user)
        session.flush()
        session.expunge(user)
    return user


def test_create_feedback_from_web_persists_new_status(integration_database) -> None:
    user = _seed_user(integration_database.sessions, username="feedback_web1")

    async def run():
        async with integration_database.async_sessions.begin() as session:
            feedback = await create_feedback_async(
                session,
                user_id=user.id,
                kind=FeedbackKind.BUG,
                channel=FeedbackChannel.WEB,
                message="O gráfico de preço não carrega.",
            )
            return feedback.id

    feedback_id = _run(run())

    with integration_database.sessions() as session:
        items = list_feedback(session)
    assert len(items) == 1
    assert items[0].id == feedback_id
    assert items[0].status is FeedbackStatus.NEW
    assert items[0].channel is FeedbackChannel.WEB
    assert items[0].kind is FeedbackKind.BUG


def test_create_feedback_from_telegram_persists_channel(integration_database) -> None:
    user = _seed_user(integration_database.sessions, username="feedback_tg1")

    async def run():
        async with integration_database.async_sessions.begin() as session:
            await create_feedback_async(
                session,
                user_id=user.id,
                kind=FeedbackKind.SUPPORT,
                channel=FeedbackChannel.TELEGRAM,
                message="Como funciona o preço-alvo?",
            )

    _run(run())

    with integration_database.sessions() as session:
        items = list_feedback(session, kind=FeedbackKind.SUPPORT)
    assert len(items) == 1
    assert items[0].channel is FeedbackChannel.TELEGRAM


def test_create_store_suggestion_without_comment_persists_null_message(
    integration_database,
) -> None:
    user = _seed_user(integration_database.sessions, username="feedback_sug1")

    async def run():
        async with integration_database.async_sessions.begin() as session:
            await create_feedback_async(
                session,
                user_id=user.id,
                kind=FeedbackKind.STORE_SUGGESTION,
                channel=FeedbackChannel.TELEGRAM,
                message=None,
                store_name="Nova Loja",
                store_url="https://novaloja.example.com",
            )

    _run(run())

    with integration_database.sessions() as session:
        items = list_feedback(session, kind=FeedbackKind.STORE_SUGGESTION)
    assert len(items) == 1
    assert items[0].message is None
    assert items[0].store_name == "Nova Loja"
    assert items[0].store_url == "https://novaloja.example.com"


def test_update_feedback_status_transitions_new_to_reviewed_to_closed(
    integration_database,
) -> None:
    user = _seed_user(integration_database.sessions, username="feedback_status1")

    async def run():
        async with integration_database.async_sessions.begin() as session:
            feedback = await create_feedback_async(
                session,
                user_id=user.id,
                kind=FeedbackKind.BUG,
                channel=FeedbackChannel.WEB,
                message="Erro ao salvar preferências.",
            )
            return feedback.id

    feedback_id = _run(run())

    with integration_database.sessions.begin() as session:
        updated = update_feedback_status(
            session, feedback_id=feedback_id, status=FeedbackStatus.REVIEWED
        )
        assert updated.status is FeedbackStatus.REVIEWED

    with integration_database.sessions.begin() as session:
        updated = update_feedback_status(
            session, feedback_id=feedback_id, status=FeedbackStatus.CLOSED
        )
        assert updated.status is FeedbackStatus.CLOSED

    with integration_database.sessions() as session:
        items = list_feedback(session, status=FeedbackStatus.CLOSED)
    assert len(items) == 1
    assert items[0].id == feedback_id


def test_list_feedback_paginates_most_recent_first(integration_database) -> None:
    """Nunca listagem ilimitada: `limit`/`offset` navegam a mesma
    ordenação (`created_at desc`), sem pular nem repetir registros.
    Cada registro nasce numa transação própria -- `now()` do Postgres é
    fixo por transação, então criar os três na mesma transação empataria
    `created_at` e o desempate por `id` (aleatório, é UUID) invalidaria a
    ordem esperada."""
    user = _seed_user(integration_database.sessions, username="feedback_page1")

    async def create_one(index: int):
        async with integration_database.async_sessions.begin() as session:
            feedback = await create_feedback_async(
                session,
                user_id=user.id,
                kind=FeedbackKind.BUG,
                channel=FeedbackChannel.WEB,
                message=f"erro {index}",
            )
            return feedback.id

    created_ids = [_run(create_one(index)) for index in range(3)]

    with integration_database.sessions() as session:
        total = count_feedback(session)
        first_page = list_feedback(session, limit=2, offset=0)
        second_page = list_feedback(session, limit=2, offset=2)

    assert total == 3
    assert len(first_page) == 2
    assert len(second_page) == 1
    all_ids_in_order = [item.id for item in first_page] + [
        item.id for item in second_page
    ]
    assert all_ids_in_order == list(reversed(created_ids))
