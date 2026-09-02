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

from app.authentication.models import UserCredential
from app.authentication.passwords import hash_password, validate_password
from app.users.models import User, UserRole
from app.users.registration import validate_username


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


class UserAlreadyExistsError(ValueError):
    """`username` ou `email` já pertencem a outra conta -- `field` diz
    qual (Subtask 9: cadastro Web nunca pode virar 500 nem criar/mesclar
    uma segunda conta por causa disso)."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"{field} already exists")


def create_user_with_password(
    session: Session,
    *,
    username: str,
    email: str | None,
    password: str,
    role: UserRole = UserRole.USER,
    display_name: str | None = None,
) -> User:
    """Único caminho de criação de `User` com senha própria (Subtask 9) --
    reaproveitado pelo cadastro Web self-service e por
    `app.webapp.admin_router.create_user` (mesma validação de
    username/senha, mesmo hashing, mesma proteção real contra corrida:
    `begin_nested()` + `IntegrityError`, idêntico ao padrão já usado por
    `get_or_create_telegram_user` acima). `email` só tem `UNIQUE` a partir
    da migration desta subtask -- antes, colisão de e-mail no endpoint
    admin não era detectada e podia gerar 500 (achado do preflight);
    reaproveitar este service corrige os dois pontos de criação juntos,
    sem abrir uma segunda tarefa. `email` é normalizado aqui
    (`strip().lower()`, Subtask 9) -- estratégia de unicidade "e-mail
    sempre armazenado normalizado": `uq_users_email` é uma constraint
    simples (não um índice case-insensitive) porque nenhuma variação de
    maiúsculas chega a ser persistida."""
    normalized_username = validate_username(username)
    normalized_password = validate_password(password, username=normalized_username)
    password_hash = hash_password(normalized_password)
    normalized_email = email.strip().lower() if email else None
    try:
        with session.begin_nested():
            user = User(
                display_name=(display_name or normalized_username).strip()
                or normalized_username,
                username=normalized_username,
                email=normalized_email,
                role=role,
            )
            session.add(user)
            session.flush()
            session.add(UserCredential(user_id=user.id, password_hash=password_hash))
            session.flush()
    except IntegrityError as error:
        raise _resolve_creation_conflict(
            session, username=normalized_username, email=normalized_email
        ) from error
    return user


async def create_user_with_password_async(
    session: AsyncSession,
    *,
    username: str,
    email: str | None,
    password: str,
    role: UserRole = UserRole.USER,
    display_name: str | None = None,
) -> User:
    """Equivalente assíncrono de `create_user_with_password`, usado pelo
    endpoint público de cadastro Web (`app.webapp.registration_router`).
    Mesma normalização de `email` (`strip().lower()`) do gêmeo síncrono."""
    normalized_username = validate_username(username)
    normalized_password = validate_password(password, username=normalized_username)
    password_hash = hash_password(normalized_password)
    normalized_email = email.strip().lower() if email else None
    try:
        async with session.begin_nested():
            user = User(
                display_name=(display_name or normalized_username).strip()
                or normalized_username,
                username=normalized_username,
                email=normalized_email,
                role=role,
            )
            session.add(user)
            await session.flush()
            session.add(UserCredential(user_id=user.id, password_hash=password_hash))
            await session.flush()
    except IntegrityError as error:
        raise await _resolve_creation_conflict_async(
            session, username=normalized_username, email=normalized_email
        ) from error
    return user


def _resolve_creation_conflict(
    session: Session, *, username: str, email: str | None
) -> UserAlreadyExistsError:
    if session.scalar(select(User.id).where(User.username == username)) is not None:
        return UserAlreadyExistsError("username")
    if email is not None and (
        session.scalar(select(User.id).where(User.email == email)) is not None
    ):
        return UserAlreadyExistsError("email")
    return UserAlreadyExistsError("unknown")


async def _resolve_creation_conflict_async(
    session: AsyncSession, *, username: str, email: str | None
) -> UserAlreadyExistsError:
    if (
        await session.scalar(select(User.id).where(User.username == username))
    ) is not None:
        return UserAlreadyExistsError("username")
    if email is not None and (
        await session.scalar(select(User.id).where(User.email == email))
    ) is not None:
        return UserAlreadyExistsError("email")
    return UserAlreadyExistsError("unknown")
