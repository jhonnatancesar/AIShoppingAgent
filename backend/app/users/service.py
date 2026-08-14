"""Resolução determinística de identidade a partir do Telegram.

`telegram_user_id` identifica a pessoa (`message.from.id` no `Update` do
Telegram), nunca a conversa (`chat.id`); este módulo não conhece nem persiste
`chat_id`. A fronteira da TASK-046 decide se a identidade do canal é aceitável
antes de chamar este resolvedor. Senha, OAuth, sessão e lógica de missão não
pertencem a este serviço.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.users.models import User, UserRole


def get_or_create_telegram_user(
    session: Session,
    *,
    telegram_user_id: int,
    display_name: str,
) -> tuple[User, bool]:
    """Resolve, de forma idempotente, o `User` de uma pessoa no Telegram.

    O segundo valor (`created_now`, TASK-078) é `True` só quando este
    exato retorno criou o registro -- permite ao chamador distinguir
    primeiro contato de retorno sem nenhuma consulta adicional."""
    if not display_name.strip():
        raise ValueError("display_name não pode ser vazio.")

    user = _find_by_telegram_user_id(session, telegram_user_id)
    if user is not None:
        return user, False

    try:
        with session.begin_nested():
            user = User(
                display_name=display_name,
                role=UserRole.USER,
                telegram_user_id=telegram_user_id,
            )
            session.add(user)
            session.flush()
    except IntegrityError:
        user = _find_by_telegram_user_id(session, telegram_user_id)
        if user is None:
            raise
        return user, False
    return user, True


async def get_or_create_telegram_user_async(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    display_name: str,
) -> tuple[User, bool]:
    """Equivalente assíncrono de `get_or_create_telegram_user` (extensão da
    TASK-079). Usado pelo webhook Telegram;
    `scripts/validate_telegram_authentication.py` continua na versão
    síncrona."""
    if not display_name.strip():
        raise ValueError("display_name não pode ser vazio.")

    user = await _find_by_telegram_user_id_async(session, telegram_user_id)
    if user is not None:
        return user, False

    try:
        async with session.begin_nested():
            user = User(
                display_name=display_name,
                role=UserRole.USER,
                telegram_user_id=telegram_user_id,
            )
            session.add(user)
            await session.flush()
    except IntegrityError:
        user = await _find_by_telegram_user_id_async(session, telegram_user_id)
        if user is None:
            raise
        return user, False
    return user, True


def _find_by_telegram_user_id(session: Session, telegram_user_id: int) -> User | None:
    return session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))


async def _find_by_telegram_user_id_async(
    session: AsyncSession, telegram_user_id: int
) -> User | None:
    return await session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
