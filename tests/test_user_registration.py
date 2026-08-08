"""Testes do fluxo de cadastro inicial dirigido por comando (TASK-060)."""

import pytest
from app.users.models import User, UserRole
from app.users.registration import (
    REGISTRATION_STEPS,
    RegistrationError,
    advance_registration,
    start_registration,
)


def _user() -> User:
    return User(display_name="Usuário de teste", role=UserRole.USER)


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
        advance_registration(user, answer="joaosilva")


def test_advance_registration_rejects_skip_on_username() -> None:
    user = _user()
    start_registration(user)

    with pytest.raises(RegistrationError):
        advance_registration(user, answer="pular")
    assert user.registration_step == "username"


@pytest.mark.parametrize("bad_username", ["", "   ", "a" * 33, "com espaco", "/oi"])
def test_advance_registration_rejects_invalid_username(bad_username: str) -> None:
    user = _user()
    start_registration(user)

    with pytest.raises(RegistrationError):
        advance_registration(user, answer=bad_username)
    assert user.registration_step == "username"


def test_advance_registration_accepts_valid_username_and_moves_to_email() -> None:
    user = _user()
    start_registration(user)

    prompt = advance_registration(user, answer="  joaosilva  ")

    assert user.username == "joaosilva"
    assert user.registration_step == "email"
    assert "e-mail" in prompt.lower()


def test_advance_registration_allows_skipping_email() -> None:
    user = _user()
    user.registration_step = "email"

    advance_registration(user, answer="pular")

    assert user.email is None
    assert user.registration_step == "favorite_stores"


@pytest.mark.parametrize("bad_email", ["sem-arroba", "a@b", "a@b.c" + "x" * 250])
def test_advance_registration_rejects_invalid_email(bad_email: str) -> None:
    user = _user()
    user.registration_step = "email"

    with pytest.raises(RegistrationError):
        advance_registration(user, answer=bad_email)
    assert user.registration_step == "email"


def test_advance_registration_accepts_valid_email() -> None:
    user = _user()
    user.registration_step = "email"

    advance_registration(user, answer="joao@example.com")

    assert user.email == "joao@example.com"
    assert user.registration_step == "favorite_stores"


def test_advance_registration_parses_known_favorite_stores() -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    advance_registration(user, answer="Kabum, pichau pichau")

    assert user.favorite_stores == ["kabum", "pichau"]
    assert user.registration_step == "preferred_categories"


def test_advance_registration_rejects_favorite_stores_with_no_known_match() -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    with pytest.raises(RegistrationError):
        advance_registration(user, answer="shopee, mercado livre")
    assert user.registration_step == "favorite_stores"


def test_advance_registration_allows_skipping_favorite_stores() -> None:
    user = _user()
    user.registration_step = "favorite_stores"

    advance_registration(user, answer="pular")

    assert user.favorite_stores == []
    assert user.registration_step == "preferred_categories"


def test_advance_registration_parses_preferred_categories_and_completes() -> None:
    user = _user()
    user.registration_step = "preferred_categories"

    completion = advance_registration(user, answer="games, moveis,  livros ")

    assert user.preferred_categories == ["games", "moveis", "livros"]
    assert user.registration_step is None
    assert "concluído" in completion.lower()


def test_advance_registration_rejects_too_many_categories() -> None:
    user = _user()
    user.registration_step = "preferred_categories"

    with pytest.raises(RegistrationError):
        advance_registration(user, answer=",".join(f"cat{i}" for i in range(21)))
    assert user.registration_step == "preferred_categories"


def test_advance_registration_rejects_category_too_long() -> None:
    user = _user()
    user.registration_step = "preferred_categories"

    with pytest.raises(RegistrationError):
        advance_registration(user, answer="a" * 65)
    assert user.registration_step == "preferred_categories"
