"""Política de senha e Argon2id."""

import unicodedata

import pytest
from app.authentication.passwords import (
    MAX_PASSWORD_LENGTH,
    PASSWORD_HASHER,
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    normalize_password,
    validate_password,
    verify_password,
)
from argon2.low_level import Type


def test_argon2id_parameters_are_explicit_and_salted() -> None:
    first = hash_password("frase secreta segura 01")
    second = hash_password("frase secreta segura 01")

    assert first != second
    assert first.startswith("$argon2id$")
    assert PASSWORD_HASHER.type is Type.ID
    assert PASSWORD_HASHER.memory_cost == 19_456
    assert PASSWORD_HASHER.time_cost == 2
    assert PASSWORD_HASHER.parallelism == 1
    assert verify_password(first, "frase secreta segura 01")
    assert not verify_password(first, "frase secreta incorreta")
    assert not verify_password("hash-invalido", "frase secreta segura 01")
    assert not needs_rehash("hash-invalido")


def test_password_unicode_is_nfc_and_spaces_are_preserved() -> None:
    decomposed = "  frase muito segura cafe\u0301  "
    normalized = validate_password(decomposed)

    assert normalized == unicodedata.normalize("NFC", decomposed)
    assert normalized.startswith("  ") and normalized.endswith("  ")
    assert normalize_password(decomposed) == normalized


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("curta demais", "pelo menos 15"),
        ("x" * (MAX_PASSWORD_LENGTH + 1), "no máximo 128"),
        ("passwordpassword", "comum ou previsível"),
        ("aishoppingagent", "comum ou previsível"),
    ],
)
def test_password_policy_rejects_invalid_values(password: str, message: str) -> None:
    with pytest.raises(PasswordPolicyError, match=message):
        validate_password(password)


def test_password_policy_blocks_username_derivative() -> None:
    with pytest.raises(PasswordPolicyError, match="comum"):
        validate_password("cliente01cliente01", username="cliente01")


def test_hash_is_marked_for_rehash_when_parameters_change() -> None:
    legacy = PASSWORD_HASHER.__class__(
        time_cost=1, memory_cost=8_192, parallelism=1, type=Type.ID
    ).hash("frase secreta segura 02")

    assert needs_rehash(legacy)
