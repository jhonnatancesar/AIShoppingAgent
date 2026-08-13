"""Reserva transacional de update_id, replay e rate limit do webhook.

Assíncrono desde a extensão da TASK-079: único chamador é
`app.telegram.router`, então não há versão síncrona a manter. A partir
dessa extensão, todo o processamento de uma mensagem de um mesmo usuário já
é serializado ponta a ponta por `app.telegram.concurrency.
user_serialization_lock` (advisory lock do Postgres) -- o `FOR UPDATE` de
`User` abaixo deixa de ser a garantia de serialização em si (não há mais
concorrência real entre duas reservas do mesmo usuário para ele evitar) e
passa a ser só uma segunda camada defensiva, barata porque nunca mais fica
presa durante um `await` externo (a seção continua curta e roda dentro da
Fase A)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.telegram.models import TelegramUpdateDisposition, TelegramUpdateReceipt
from app.users.models import User

_UPDATE_ID_UNIQUE_CONSTRAINT = "uq_telegram_update_receipts_update_id"


@dataclass(frozen=True, slots=True)
class TelegramUpdateReservation:
    disposition: TelegramUpdateDisposition
    replay: bool = False
    warn_rate_limit: bool = False


async def reserve_telegram_update(
    session: AsyncSession,
    *,
    update_id: int,
    user_id: UUID,
    accepted_per_minute: int,
    forced_disposition: TelegramUpdateDisposition | None = None,
) -> TelegramUpdateReservation:
    """Reserva o update na transação corrente; não cria estado processing."""
    if update_id < 0:
        raise ValueError("update_id must not be negative")
    if not 1 <= accepted_per_minute <= 1000:
        raise ValueError("accepted_per_minute must be between 1 and 1000")

    # Segunda camada defensiva (ver docstring do módulo) -- a serialização
    # por usuário já é garantida por app.telegram.concurrency antes deste
    # ponto. A unicidade global de update_id continua sendo a autoridade
    # final para updates forjados/concorrentes entre usuários.
    locked_user = (
        await session.execute(
            select(User.id).where(User.id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if locked_user is None:
        raise ValueError("user must exist before reserving an update")

    existing = await session.scalar(
        select(TelegramUpdateReceipt).where(
            TelegramUpdateReceipt.update_id == update_id
        )
    )
    if existing is not None:
        return TelegramUpdateReservation(
            TelegramUpdateDisposition(existing.disposition), replay=True
        )

    warn_rate_limit = False
    disposition = forced_disposition
    if disposition is None:
        accepted_count = await session.scalar(
            select(func.count(TelegramUpdateReceipt.id)).where(
                TelegramUpdateReceipt.user_id == user_id,
                TelegramUpdateReceipt.disposition
                == TelegramUpdateDisposition.ACCEPTED.value,
                TelegramUpdateReceipt.recorded_at > func.now() - timedelta(minutes=1),
            )
        )
        if int(accepted_count or 0) >= accepted_per_minute:
            disposition = TelegramUpdateDisposition.RATE_LIMITED
            recent_warning = await session.scalar(
                select(func.count(TelegramUpdateReceipt.id)).where(
                    TelegramUpdateReceipt.user_id == user_id,
                    TelegramUpdateReceipt.disposition
                    == TelegramUpdateDisposition.RATE_LIMITED.value,
                    TelegramUpdateReceipt.recorded_at
                    > func.now() - timedelta(minutes=1),
                )
            )
            warn_rate_limit = int(recent_warning or 0) == 0
        else:
            disposition = TelegramUpdateDisposition.ACCEPTED

    receipt = TelegramUpdateReceipt(
        update_id=update_id,
        user_id=user_id,
        disposition=disposition.value,
    )
    try:
        async with session.begin_nested():
            session.add(receipt)
            await session.flush()
    except IntegrityError as error:
        if _constraint_name(error) != _UPDATE_ID_UNIQUE_CONSTRAINT:
            raise
        existing = await session.scalar(
            select(TelegramUpdateReceipt).where(
                TelegramUpdateReceipt.update_id == update_id
            )
        )
        if existing is None:
            raise
        return TelegramUpdateReservation(
            TelegramUpdateDisposition(existing.disposition), replay=True
        )
    return TelegramUpdateReservation(disposition, warn_rate_limit=warn_rate_limit)


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    return getattr(diagnostic, "constraint_name", None)
