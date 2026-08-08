"""Política e hashing de senhas, sem persistir ou registrar texto puro."""

import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128

# Baseline local e determinística da V1. A comparação é do segredo inteiro.
_COMMON_PASSWORDS = frozenset(
    {
        "123456789012345",
        "1234567890123456",
        "12345678901234567890",
        "abcdefghijklmnop",
        "adminadminadmin",
        "iloveyouiloveyou",
        "letmeinletmeinletmein",
        "passwordpassword",
        "qwertyuiopasdfgh",
        "senha123senha123",
        "senha muito fraca",
        "welcome123welcome",
    }
)

PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


class PasswordPolicyError(ValueError):
    """A senha proposta não atende à política fechada da V1."""


def normalize_password(password: str) -> str:
    """Normaliza Unicode sem aparar ou transformar caracteres significativos."""
    return unicodedata.normalize("NFC", password)


def validate_password(password: str, *, username: str | None = None) -> str:
    normalized = normalize_password(password)
    if len(normalized) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError("A senha precisa ter pelo menos 15 caracteres.")
    if len(normalized) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError("A senha pode ter no máximo 128 caracteres.")
    comparable = normalized.casefold()
    blocked = comparable in _COMMON_PASSWORDS
    if username:
        folded_username = username.casefold()
        blocked = blocked or comparable in {
            folded_username,
            folded_username * 2,
            f"{folded_username}123456789",
        }
    if blocked or comparable in {"aishoppingagent", "aishoppingagent123"}:
        raise PasswordPolicyError("Essa senha é muito comum ou previsível.")
    return normalized


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, normalize_password(password))
    except InvalidHashError, VerificationError, VerifyMismatchError:
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False
