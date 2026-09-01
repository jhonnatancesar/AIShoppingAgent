"""Camada única de domínio para bug/suporte/sugestão de loja.

`create_feedback_async` é o único caminho de escrita e é compartilhado
entre Telegram (`app.telegram.router`) e Web (`app.webapp.feedback_router`)
-- nenhum dos dois canais grava direto no modelo, e nenhum dos dois
recebe `user_id`/`channel`/`status` do cliente: `user_id` vem sempre da
identidade já autenticada (sessão Web ou usuário resolvido do Telegram),
`channel` é fixo por chamador, `status` nunca é parâmetro aqui -- nasce
sempre `FeedbackStatus.NEW` pelo default do modelo.

As invariantes (campo obrigatório por `kind`, limites de tamanho, esquema
de `store_url`) são reforçadas aqui, não só no formulário Web ou no fluxo
guiado do Telegram -- um formulário React (ou uma resposta livre do
Telegram) não é fronteira de confiança; esta função é.

`list_feedback`/`count_feedback`/`update_feedback_status` são síncronas
porque o único chamador (`app.webapp.admin_router`) já usa `Session`
síncrona em todo o resto do arquivo (mesmo padrão de
`app.missions.service.transition_mission` vs. `transition_mission_async`).
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.feedback.models import (
    FeedbackChannel,
    FeedbackKind,
    FeedbackStatus,
    UserFeedback,
)

_MAX_MESSAGE_LENGTH = 4000
_MAX_STORE_NAME_LENGTH = 160
_MAX_STORE_URL_LENGTH = 2048
_ALLOWED_STORE_URL_SCHEMES = ("http://", "https://")


class FeedbackNotFoundError(Exception):
    """Nenhum `UserFeedback` com o id informado."""


class FeedbackValidationError(ValueError):
    """Dados inválidos para criar um `UserFeedback` -- mensagem já pronta
    para exibir ao usuário final (Web ou Telegram)."""


def validate_feedback_message(message: str) -> None:
    if len(message) > _MAX_MESSAGE_LENGTH:
        raise FeedbackValidationError("A mensagem é longa demais.")


def validate_store_name(name: str) -> None:
    if len(name) > _MAX_STORE_NAME_LENGTH:
        raise FeedbackValidationError("O nome da loja é longo demais.")


def validate_store_url(url: str) -> None:
    if len(url) > _MAX_STORE_URL_LENGTH:
        raise FeedbackValidationError("O link é longo demais.")
    if not url.lower().startswith(_ALLOWED_STORE_URL_SCHEMES):
        raise FeedbackValidationError("O link precisa começar com http:// ou https://.")


def _validate_required_fields(
    *, kind: FeedbackKind, message: str | None, store_name: str | None
) -> None:
    if kind is FeedbackKind.STORE_SUGGESTION:
        if not store_name or not store_name.strip():
            raise FeedbackValidationError("O nome da loja é obrigatório.")
    elif not message or not message.strip():
        raise FeedbackValidationError("A descrição é obrigatória.")


async def create_feedback_async(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    kind: FeedbackKind,
    channel: FeedbackChannel,
    message: str | None,
    store_name: str | None = None,
    store_url: str | None = None,
) -> UserFeedback:
    _validate_required_fields(kind=kind, message=message, store_name=store_name)
    if message is not None:
        validate_feedback_message(message)
    if store_name is not None:
        validate_store_name(store_name)
    if store_url is not None:
        validate_store_url(store_url)
    feedback = UserFeedback(
        user_id=user_id,
        kind=kind,
        channel=channel,
        message=message,
        store_name=store_name,
        store_url=store_url,
    )
    session.add(feedback)
    await session.flush()
    return feedback


_MAX_LIST_LIMIT = 100


def _apply_filters(statement, *, status: FeedbackStatus | None, kind: FeedbackKind | None):
    if status is not None:
        statement = statement.where(UserFeedback.status == status)
    if kind is not None:
        statement = statement.where(UserFeedback.kind == kind)
    return statement


def list_feedback(
    session: Session,
    *,
    status: FeedbackStatus | None = None,
    kind: FeedbackKind | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[UserFeedback]:
    """`limit` é sempre travado em `_MAX_LIST_LIMIT` (mesmo padrão de
    `app.missions.query.list_missions_for_user_by_status`) -- não existe
    listagem ilimitada. Ordenação por `created_at desc, id desc`: mesmo
    `created_at` colidido é possível, `id` desempata de forma
    determinística e estável sob paginação por `offset`."""
    statement = _apply_filters(select(UserFeedback), status=status, kind=kind)
    statement = (
        statement.order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
        .limit(min(limit, _MAX_LIST_LIMIT))
        .offset(offset)
    )
    return list(session.scalars(statement))


def count_feedback(
    session: Session,
    *,
    status: FeedbackStatus | None = None,
    kind: FeedbackKind | None = None,
) -> int:
    """Total sob o mesmo filtro de `list_feedback`, para o `total` do
    envelope de coleção (mesmo contrato de `count_missions_for_user_by_status`)."""
    statement = _apply_filters(
        select(func.count(UserFeedback.id)), status=status, kind=kind
    )
    return session.scalar(statement) or 0


def update_feedback_status(
    session: Session, *, feedback_id: UUID, status: FeedbackStatus
) -> UserFeedback:
    feedback = session.get(UserFeedback, feedback_id)
    if feedback is None:
        raise FeedbackNotFoundError(f"feedback {feedback_id} not found")
    feedback.status = status
    return feedback
