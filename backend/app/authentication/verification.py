"""Domínio compartilhado de `VerificationChallenge` (Subtask 9).

Código curto (`purpose` x `channel`), usado por recuperação de senha
(Web ou Telegram), alteração de senha iniciada pelo Telegram e
verificação de e-mail. Nunca concede sessão sozinho -- confirmar um
challenge só autoriza o chamador a trocar a senha ou marcar
`User.email_verified_at`; quem grava a sessão continua sendo a
infraestrutura já existente (`issue_web_session`, `_revoke_*`).

A política de limite de emissão espelha os mesmos números já usados por
`CredentialActionToken` de recuperação
(`app.authentication.service.RECOVERY_WINDOW/RECOVERY_LIMIT/
RECOVERY_MIN_INTERVAL`), sem reimplementar uma segunda régua -- só uma
tabela nova porque o formato (código curto, não token de link) é
diferente.

`code_hash` usa HMAC-SHA256 com um segredo de servidor
(`Settings.verification_code_pepper`), não `token_digest`/SHA-256 puro
(validação de segurança, achado real): `CredentialActionToken` pode usar
hash simples porque o token em si tem 256 bits de entropia
(`secrets.token_urlsafe(32)`) -- inviável de adivinhar mesmo sem segredo.
Este código tem só 6 dígitos (1.000.000 de valores); com hash simples,
quem obtivesse uma cópia de `verification_challenges` calcularia
SHA-256 dos 1.000.000 de códigos possíveis em frações de segundo e
recuperaria qual é válido. HMAC exige o pepper (fora da tabela, nunca
logado, resolvido só via `Settings`/arquivo de secret) para validar
qualquer tentativa -- um vazamento só do banco não basta.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.models import (
    VerificationChallenge,
    VerificationChannel,
    VerificationPurpose,
)

CHALLENGE_TTL = timedelta(minutes=10)
CHALLENGE_CODE_LENGTH = 6
CHALLENGE_MAX_ATTEMPTS = 5
CHALLENGE_ISSUANCE_WINDOW = timedelta(hours=1)
CHALLENGE_ISSUANCE_LIMIT = 3
CHALLENGE_MIN_INTERVAL = timedelta(minutes=5)

_CODE_ALPHABET = "0123456789"


class ChallengeError(RuntimeError):
    """Falha externa genérica, sem revelar detalhes internos."""


class ChallengeRateLimited(ChallengeError):
    pass


class ChallengeInvalid(ChallengeError):
    """Código incorreto, expirado, já usado, tentativas esgotadas ou
    `purpose` fora do esperado pelo chamador -- nunca distinguido para
    fora (mesmo texto para todos os casos, evita oráculo)."""


def generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CHALLENGE_CODE_LENGTH))


def code_digest(code: str, *, pepper: str) -> str:
    """HMAC-SHA256(pepper, code) -- nunca SHA-256 puro (ver docstring do
    módulo: o código tem só 1.000.000 de valores possíveis, um digest sem
    segredo seria forçável offline em frações de segundo a partir de um
    vazamento só do banco)."""
    return hmac.new(
        pepper.encode("utf-8"), code.encode("utf-8"), hashlib.sha256
    ).hexdigest()


async def create_challenge_async(
    session: AsyncSession,
    *,
    user_id: UUID,
    purpose: VerificationPurpose,
    channel: VerificationChannel,
    pepper: str,
    now: datetime | None = None,
) -> tuple[VerificationChallenge, str]:
    """Invalida qualquer challenge pendente do mesmo `purpose` (expira
    imediatamente, nunca marca como usado sem confirmação real) e emite
    um novo, já sob o limite de emissão. Devolve o código em claro só
    para o chamador entregar (Telegram/e-mail) -- nunca persistido."""
    current = _aware_now(now)
    await _enforce_issuance_limit_async(
        session, user_id=user_id, purpose=purpose, now=current
    )
    await session.execute(
        update(VerificationChallenge)
        .where(
            VerificationChallenge.user_id == user_id,
            VerificationChallenge.purpose == purpose,
            VerificationChallenge.used_at.is_(None),
            VerificationChallenge.expires_at > current,
        )
        .values(expires_at=current)
    )
    code = generate_code()
    challenge = VerificationChallenge(
        user_id=user_id,
        purpose=purpose,
        channel=channel,
        code_hash=code_digest(code, pepper=pepper),
        expires_at=current + CHALLENGE_TTL,
    )
    session.add(challenge)
    await session.flush()
    return challenge, code


async def confirm_challenge_async(
    session: AsyncSession,
    *,
    challenge_id: UUID,
    code: str,
    purposes: frozenset[VerificationPurpose],
    pepper: str,
    now: datetime | None = None,
) -> VerificationChallenge:
    """`FOR UPDATE` serializa confirmações concorrentes do mesmo
    challenge -- a segunda, depois de esperar a primeira commitar, sempre
    encontra `used_at` já preenchido e falha. Código errado incrementa
    `attempts` (o chamador precisa comitar antes de converter
    `ChallengeInvalid` em resposta HTTP, mesmo padrão já usado por
    `_verify_login_password`/`_fail_token`)."""
    current = _aware_now(now)
    challenge = await session.scalar(
        select(VerificationChallenge)
        .where(VerificationChallenge.id == challenge_id)
        .with_for_update()
    )
    if challenge is None or challenge.purpose not in purposes:
        raise ChallengeInvalid("Código inválido ou expirado.")
    if (
        challenge.used_at is not None
        or challenge.expires_at <= current
        or challenge.attempts >= CHALLENGE_MAX_ATTEMPTS
    ):
        raise ChallengeInvalid("Código inválido ou expirado.")
    if not secrets.compare_digest(challenge.code_hash, code_digest(code, pepper=pepper)):
        challenge.attempts += 1
        raise ChallengeInvalid("Código inválido ou expirado.")
    challenge.used_at = current
    return challenge


async def _enforce_issuance_limit_async(
    session: AsyncSession, *, user_id: UUID, purpose: VerificationPurpose, now: datetime
) -> None:
    recent = await session.scalar(
        select(func.count(VerificationChallenge.id)).where(
            VerificationChallenge.user_id == user_id,
            VerificationChallenge.purpose == purpose,
            VerificationChallenge.created_at > now - CHALLENGE_ISSUANCE_WINDOW,
        )
    )
    if (recent or 0) >= CHALLENGE_ISSUANCE_LIMIT:
        raise ChallengeRateLimited("Tente novamente mais tarde.")
    latest = await session.scalar(
        select(VerificationChallenge.created_at)
        .where(
            VerificationChallenge.user_id == user_id,
            VerificationChallenge.purpose == purpose,
        )
        .order_by(VerificationChallenge.created_at.desc())
        .limit(1)
    )
    if latest is not None and latest > now - CHALLENGE_MIN_INTERVAL:
        raise ChallengeRateLimited("Tente novamente mais tarde.")


def _aware_now(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None:
        raise ValueError("verification challenge timestamps must be timezone-aware")
    return result
