"""Cadastro Web self-service, alteração/recuperação de senha e
`VerificationChallenge` (Subtask 9, auditoria GG Oferta) contra
PostgreSQL real -- prova especificamente os dois requisitos que só fazem
sentido com banco de verdade: (1) duas requisições concorrentes tentando
criar a mesma identidade (username ou e-mail) resultam em exatamente uma
conta, nunca 500; (2) duas confirmações concorrentes do mesmo challenge
resultam em exatamente um sucesso."""

import asyncio
import hashlib

import pytest
from app.authentication.models import (
    VerificationChallenge,
    VerificationChannel,
    VerificationPurpose,
)
from app.authentication.service import (
    AuthenticationError,
    authenticate_web_login,
    change_password_with_current,
    set_new_password_async,
)
from app.authentication.verification import (
    ChallengeInvalid,
    code_digest,
    confirm_challenge_async,
    create_challenge_async,
)
from app.core.errors import ApiError
from app.users.models import User, UserRole
from app.users.service import UserAlreadyExistsError, create_user_with_password_async
from app.webapp.admin_router import CreateUserRequest, create_user
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

_TEST_PEPPER = "pepper-de-teste-nao-real-para-integracao"


def _run(coro):
    return asyncio.run(coro)


def _seed_admin(sessions) -> User:
    with sessions.begin() as session:
        actor = User(display_name="Admin", role=UserRole.ADMIN, username="admin_seed")
        session.add(actor)
        session.flush()
        session.expunge(actor)
    return actor


def test_concurrent_registration_same_username_creates_only_one_account(
    integration_database,
) -> None:
    async def attempt(index: int):
        async with integration_database.async_sessions.begin() as session:
            try:
                await create_user_with_password_async(
                    session,
                    username="corrida_username",
                    email=f"corrida{index}@example.com",
                    password="Senha#Forte123",
                )
                return "ok"
            except UserAlreadyExistsError as error:
                return error.field

    async def run_both():
        return await asyncio.gather(attempt(1), attempt(2))

    results = _run(run_both())

    assert sorted(results) == ["ok", "username"]
    with integration_database.sessions() as session:
        count = session.scalar(
            select(func.count(User.id)).where(User.username == "corrida_username")
        )
    assert count == 1


def test_concurrent_registration_same_email_creates_only_one_account(
    integration_database,
) -> None:
    async def attempt(index: int):
        async with integration_database.async_sessions.begin() as session:
            try:
                await create_user_with_password_async(
                    session,
                    username=f"corrida_email_user{index}",
                    email="corrida_email@example.com",
                    password="Senha#Forte123",
                )
                return "ok"
            except UserAlreadyExistsError as error:
                return error.field

    async def run_both():
        return await asyncio.gather(attempt(1), attempt(2))

    results = _run(run_both())

    assert sorted(results) == ["email", "ok"]
    with integration_database.sessions() as session:
        count = session.scalar(
            select(func.count(User.id)).where(
                User.email == "corrida_email@example.com"
            )
        )
    assert count == 1


def test_admin_create_user_duplicate_returns_conflict_never_500(
    integration_database,
) -> None:
    """Achado do preflight corrigido de forma compartilhada: o endpoint
    ADMIN agora reaproveita `create_user_with_password` e herda a mesma
    proteção real contra corrida/duplicidade."""
    actor = _seed_admin(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        create_user(
            CreateUserRequest(
                display_name="Original",
                username="admin_dup",
                email="admin_dup@example.com",
                role=UserRole.USER,
                password="Senha#Forte123",
            ),
            session,
            actor,
        )
    with integration_database.sessions.begin() as session:
        with pytest.raises(ApiError) as exc_info:
            create_user(
                CreateUserRequest(
                    display_name="Duplicata",
                    username="admin_dup",
                    email="outro@example.com",
                    role=UserRole.USER,
                    password="Senha#Forte123",
                ),
                session,
                actor,
            )
        assert exc_info.value.status_code == 409


def test_challenge_confirm_concurrent_only_one_succeeds(integration_database) -> None:
    async def setup():
        async with integration_database.async_sessions.begin() as session:
            user = User(display_name="Alvo", role=UserRole.USER, username="alvo_challenge")
            session.add(user)
            await session.flush()
            challenge, code = await create_challenge_async(
                session,
                user_id=user.id,
                purpose=VerificationPurpose.PASSWORD_RESET,
                channel=VerificationChannel.EMAIL,
                pepper=_TEST_PEPPER,
            )
            return challenge.id, code

    challenge_id, code = _run(setup())

    async def attempt():
        async with integration_database.async_sessions.begin() as session:
            try:
                await confirm_challenge_async(
                    session,
                    challenge_id=challenge_id,
                    code=code,
                    purposes=frozenset({VerificationPurpose.PASSWORD_RESET}),
                    pepper=_TEST_PEPPER,
                )
                return "ok"
            except ChallengeInvalid:
                return "invalid"

    async def run_both():
        return await asyncio.gather(attempt(), attempt())

    results = _run(run_both())

    assert sorted(results) == ["invalid", "ok"]


def test_full_registration_login_change_and_recover_cycle(integration_database) -> None:
    async def register():
        async with integration_database.async_sessions.begin() as session:
            return await create_user_with_password_async(
                session,
                username="ciclo_completo",
                email="ciclo@example.com",
                password="Senha#Original1",
            )

    user = _run(register())

    with integration_database.sessions.begin() as session:
        authenticate_web_login(session, username="ciclo_completo", password="Senha#Original1")

    with integration_database.sessions.begin() as session:
        db_user = session.get(User, user.id)
        change_password_with_current(
            session,
            user=db_user,
            current_password="Senha#Original1",
            new_password="Senha#Trocada2",
        )

    with integration_database.sessions.begin() as session:
        with pytest.raises(AuthenticationError):  # senha antiga não autentica mais
            authenticate_web_login(
                session, username="ciclo_completo", password="Senha#Original1"
            )
    with integration_database.sessions.begin() as session:
        authenticate_web_login(session, username="ciclo_completo", password="Senha#Trocada2")

    async def request_recovery():
        async with integration_database.async_sessions.begin() as session:
            challenge, code = await create_challenge_async(
                session,
                user_id=user.id,
                purpose=VerificationPurpose.PASSWORD_RESET,
                channel=VerificationChannel.EMAIL,
                pepper=_TEST_PEPPER,
            )
            return challenge.id, code

    challenge_id, code = _run(request_recovery())

    async def confirm_and_reset():
        async with integration_database.async_sessions.begin() as session:
            challenge = await confirm_challenge_async(
                session,
                challenge_id=challenge_id,
                code=code,
                purposes=frozenset(
                    {VerificationPurpose.PASSWORD_RESET, VerificationPurpose.PASSWORD_CHANGE}
                ),
                pepper=_TEST_PEPPER,
            )
            reset_user = await session.get(User, challenge.user_id)
            await set_new_password_async(
                session, user=reset_user, new_password="Senha#Recuperada3"
            )

    _run(confirm_and_reset())

    with integration_database.sessions.begin() as session:
        with pytest.raises(AuthenticationError):
            authenticate_web_login(
                session, username="ciclo_completo", password="Senha#Trocada2"
            )
    with integration_database.sessions.begin() as session:
        authenticate_web_login(
            session, username="ciclo_completo", password="Senha#Recuperada3"
        )


def test_verification_challenge_code_hash_uses_hmac_not_plain_digest(
    integration_database,
) -> None:
    """Validação de segurança: o código tem só 1.000.000 de valores
    possíveis (6 dígitos) -- um digest sem segredo seria forçável offline
    em frações de segundo a partir de um vazamento só do banco. Prova
    (1) o código em claro nunca é persistido, (2) `code_hash` não é
    SHA-256(code) puro nem recuperável com um pepper errado -- só o
    pepper real reproduz o hash armazenado -- e (3) a confirmação
    continua funcionando corretamente para código certo e errado."""

    async def setup():
        async with integration_database.async_sessions.begin() as session:
            user = User(display_name="Alvo Hash", role=UserRole.USER, username="alvo_hash")
            session.add(user)
            await session.flush()
            challenge, code = await create_challenge_async(
                session,
                user_id=user.id,
                purpose=VerificationPurpose.PASSWORD_RESET,
                channel=VerificationChannel.EMAIL,
                pepper=_TEST_PEPPER,
            )
            return challenge.id, code

    challenge_id, code = _run(setup())

    with integration_database.sessions() as session:
        stored_hash = session.scalar(
            select(VerificationChallenge.code_hash).where(
                VerificationChallenge.id == challenge_id
            )
        )

    assert code not in stored_hash  # (1) nunca em claro no banco

    plain_digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert stored_hash != plain_digest  # (2a) não é SHA-256 puro sem segredo

    wrong_pepper_digest = code_digest(code, pepper="pepper-errado-nao-e-o-real")
    assert stored_hash != wrong_pepper_digest  # (2b) pepper errado não reproduz
    assert stored_hash == code_digest(code, pepper=_TEST_PEPPER)  # só o pepper real

    async def confirm_wrong_code():
        async with integration_database.async_sessions.begin() as session:
            wrong_code = "000000" if code != "000000" else "111111"
            try:
                await confirm_challenge_async(
                    session,
                    challenge_id=challenge_id,
                    code=wrong_code,
                    purposes=frozenset({VerificationPurpose.PASSWORD_RESET}),
                    pepper=_TEST_PEPPER,
                )
                return "accepted"
            except ChallengeInvalid:
                return "rejected"

    assert _run(confirm_wrong_code()) == "rejected"  # (3a) código errado

    async def confirm_right_code():
        async with integration_database.async_sessions.begin() as session:
            await confirm_challenge_async(
                session,
                challenge_id=challenge_id,
                code=code,
                purposes=frozenset({VerificationPurpose.PASSWORD_RESET}),
                pepper=_TEST_PEPPER,
            )
            return "accepted"

    assert _run(confirm_right_code()) == "accepted"  # (3b) código certo
