"""Autenticação persistente por senha da TASK-061."""

from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)

__all__ = [
    "CredentialAction",
    "CredentialActionToken",
    "UserAuthSession",
    "UserCredential",
]
