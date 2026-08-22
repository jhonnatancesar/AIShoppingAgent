"""Testes do fluxo de cadastro inicial dirigido por comando (TASK-060)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.users.models import User, UserRole
from app.users.registration import (
    _NUMBERED_CATEGORIES,
    REGISTRATION_STEPS,
    RegistrationError,
    advance_registration,
    start_registration,
)


def _user() -> User:
    user = User(display_name="Usuário de teste", role=UserRole.USER)
    user.id = uuid4()
    return user


def _session(*, username_taken: bool = False) -> MagicMock:
    """TASK-072: por padrão, nenhum outro usuário tem o mesmo username."""
    session = MagicMock()
    session.scalar = AsyncMock(return_value=uuid4() if username_taken else None)
    return session


def test_start_registration_sets_first_step_and_prompts() -> None:
    user = _user()

    prompt = start_registration(user)

    assert user.registration_step == REGISTRATION_STEPS[0]
    assert "usuário" in prompt.lower()


def test_start_registration_restarts_even_mid_flow() -> None:
    user = _user()
    user.registration_step = "preferred_categories"

    start_registration(user)

    assert user.registration_step == "username"


def test_advance_registration_without_step_in_progress_raises() -> None:
    user = _user()

    with pytest.raises(RegistrationError):
        asyncio.run(advance_registration(user, answer="joaosilva", session=_session()))


def test_advance_registration_rejects_skip_on_username() -> None:
    user = _user()
    start_registration(user)

    with pytest.raises(RegistrationError):
        asyncio.run(advance_registration(user, answer="pular", session=_session()))
    assert user.registration_step == "username"


@pytest.mark.parametrize("bad_username", ["", "   ", "a" * 33, "com espaco", "/oi"])
def test_advance_registration_rejects_invalid_username(bad_username: str) -> None:
    user = _user()
    start_registration(user)

    with pytest.raises(RegistrationError):
        asyncio.run(advance_registration(user, answer=bad_username, session=_session()))
    assert user.registration_step == "username"


def test_advance_registration_accepts_valid_username_and_moves_to_email() -> None:
    user = _user()
    start_registration(user)

    prompt = asyncio.run(
        advance_registration(user, answer="  joaosilva  ", session=_session())
    )

    assert user.username == "joaosilva"
    assert user.registration_step == "email"
    assert "e-mail" in prompt.lower()


def test_advance_registration_rejects_username_already_taken() -> None:
    """TASK-072: checagem antecipada, mantém no passo `username`."""
    user = _user()
    start_registration(user)
    session = _session(username_taken=True)

    with pytest.raises(RegistrationError, match="já está em uso"):
        asyncio.run(advance_registration(user, answer="joaosilva", session=session))
    assert user.registration_step == "username"
    assert user.username is None


def test_advance_registration_username_check_queries_by_username_excluding_self() -> (
    None
):
    """A consulta filtra por `username` e exclui o próprio usuário -- só
    assim reenviar o mesmo nome nunca soa como "já em uso"."""
    user = _user()
    start_registration(user)
    session = _session()

    asyncio.run(advance_registration(user, answer="joaosilva", session=session))

    session.scalar.assert_called_once()
    statement = str(session.scalar.call_args[0][0])
    assert "users.username" in statement
    assert "users.id" in statement


def test_advance_registration_allows_skipping_email() -> None:
    user = _user()
    user.registration_step = "email"

    asyncio.run(advance_registration(user, answer="pular", session=_session()))

    assert user.email is None
    assert user.registration_step == "favorite_stores"


@pytest.mark.parametrize("bad_email", ["sem-arroba", "a@b", "a@b.c" + "x" * 250])
def test_advance_registration_rejects_invalid_email(bad_email: str) -> None:
    user = _user()
    user.registration_step = "email"

    with pytest.raises(RegistrationError):
        asyncio.run(advance_registration(user, answer=bad_email, session=_session()))
    assert user.registration_step == "email"


def test_advance_registration_accepts_valid_email() -> None:
    user = _user()
    user.registration_step = "email"

    asyncio.run(
        advance_registration(user, answer="joao@example.com", session=_session())
    )

    assert user.email == "joao@example.com"
    assert user.registration_step == "favorite_stores"


def test_advance_registration_parses_known_favorite_stores() -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    asyncio.run(
        advance_registration(user, answer="Kabum, pichau pichau", session=_session())
    )

    assert user.favorite_stores == ["kabum", "pichau"]
    assert user.registration_step == "preferred_categories"


def test_registration_prompt_offers_numbered_stores_and_all_option() -> None:
    user = _user()
    user.registration_step = "email"

    prompt = asyncio.run(advance_registration(user, answer="pular", session=_session()))

    assert "1 — Kabum" in prompt
    assert "4 — Amazon" in prompt
    assert "5 — Magalu" in prompt
    assert "6 — Todas" in prompt


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("1,2", ["kabum", "pichau"]),
        ("2, 4", ["amazon", "pichau"]),
        ("5", ["magalu"]),
        ("6", ["amazon", "kabum", "magalu", "pichau", "terabyte"]),
        ("todas", ["amazon", "kabum", "magalu", "pichau", "terabyte"]),
    ],
)
def test_advance_registration_parses_numbered_stores(
    answer: str, expected: list[str]
) -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    asyncio.run(advance_registration(user, answer=answer, session=_session()))

    assert user.favorite_stores == expected


def test_advance_registration_rejects_favorite_stores_with_no_known_match() -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    with pytest.raises(RegistrationError):
        asyncio.run(
            advance_registration(
                user, answer="shopee, mercado livre", session=_session()
            )
        )
    assert user.registration_step == "favorite_stores"


def test_advance_registration_allows_skipping_favorite_stores() -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    asyncio.run(advance_registration(user, answer="pular", session=_session()))

    assert user.favorite_stores == []
    assert user.registration_step == "preferred_categories"


def test_registration_prompt_offers_numbered_categories_and_all_option() -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    prompt = asyncio.run(advance_registration(user, answer="pular", session=_session()))

    assert "1 — Hardware / Componentes de PC" in prompt
    assert "15 — Geek e Colecionáveis" in prompt
    assert "16 — Todas" in prompt


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("1,4,8", ["hardware", "notebooks", "video_games"]),
        ("2, 6", ["celulares", "perifericos"]),
        ("16", sorted(_NUMBERED_CATEGORIES.values())),
        ("todas", sorted(_NUMBERED_CATEGORIES.values())),
    ],
)
def test_advance_registration_parses_numbered_categories(
    answer: str, expected: list[str]
) -> None:
    user = _user()
    user.registration_step = "preferred_categories"

    completion = asyncio.run(
        advance_registration(user, answer=answer, session=_session())
    )

    assert user.preferred_categories == expected
    assert user.registration_step is None
    assert "confirmado" in completion.lower()
    assert "senha" in completion.lower()


def test_advance_registration_rejects_categories_with_no_known_match() -> None:
    user = _user()
    user.registration_step = "preferred_categories"

    with pytest.raises(RegistrationError):
        asyncio.run(
            advance_registration(
                user, answer="games, moveis, livros", session=_session()
            )
        )
    assert user.registration_step == "preferred_categories"


def test_advance_registration_allows_skipping_preferred_categories() -> None:
    user = _user()
    user.registration_step = "preferred_categories"

    asyncio.run(advance_registration(user, answer="pular", session=_session()))

    assert user.preferred_categories == []
    assert user.registration_step is None
