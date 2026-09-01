"""Suporte e sugestão de loja pela aplicação web (subtask 7 da auditoria GG Oferta).

Único ponto de escrita compartilhado com o Telegram
(`app.feedback.create_feedback_async`) -- nenhuma regra de negócio nova
aqui, só validação de payload e tradução para o mesmo domínio.
Autenticado por `WebSession` (`Depends(require_web_session)`): sessão e
CSRF automáticos, mesmo padrão de `app.webapp.missions_router`.

`user_id`, `channel` e `status` nunca vêm do cliente: `FeedbackCreateRequest`
usa `extra="forbid"` (mesmo padrão de `CreateMissionRequest`) -- um payload
com qualquer um desses três campos é rejeitado com 422 antes mesmo de
chegar ao corpo do endpoint. `user_id` vem de `require_web_session`,
`channel` é fixo em `FeedbackChannel.WEB`, e `status` nunca é aceito como
parâmetro por `create_feedback_async` -- nasce sempre `new`."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.dependency import get_web_async_session
from app.feedback import (
    FeedbackChannel,
    FeedbackKind,
    FeedbackStatus,
    FeedbackValidationError,
    create_feedback_async,
)
from app.users.models import User
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FeedbackKind
    message: str | None = Field(default=None, max_length=4000)
    store_name: str | None = Field(default=None, max_length=160)
    store_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _validate_required_fields(self) -> FeedbackCreateRequest:
        if self.kind is FeedbackKind.STORE_SUGGESTION:
            if not self.store_name or not self.store_name.strip():
                raise ValueError("O nome da loja é obrigatório.")
        elif not self.message or not self.message.strip():
            raise ValueError("A descrição é obrigatória.")
        return self


class FeedbackCreated(BaseModel):
    id: UUID
    status: FeedbackStatus


@router.post("", response_model=FeedbackCreated, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    payload: FeedbackCreateRequest,
    session: AsyncSession = Depends(get_web_async_session),
    user: User = Depends(require_web_session),
) -> FeedbackCreated:
    try:
        feedback = await create_feedback_async(
            session,
            user_id=user.id,
            kind=payload.kind,
            channel=FeedbackChannel.WEB,
            message=payload.message.strip() if payload.message else None,
            store_name=payload.store_name.strip() if payload.store_name else None,
            store_url=payload.store_url.strip() if payload.store_url else None,
        )
    except FeedbackValidationError as error:
        raise ApiError(
            status_code=422, code="feedback_invalid", message=str(error)
        ) from error
    await session.commit()
    return FeedbackCreated(id=feedback.id, status=feedback.status)
