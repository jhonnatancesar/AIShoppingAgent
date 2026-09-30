"""TASK-133 (etapa 1): falha de IA de identidade sempre deixa um candidato.

Contexto: quando `extract_product_identity_via_ai` falhava (cota esgotada,
provedor fora, tempo esgotado, resposta fora do contrato) o GG não gravava nada
e chamava a IA de novo pelo MESMO título a cada coleta -- gastava cota à toa e
não deixava rastro. Regra nova: nenhum título passa pela IA sem uma linha em
`product_identity_candidates`.

- `record_ai_failure`: cria/atualiza o candidato `ai_failed` (motivo,
  tentativas, `next_retry_at` com espera crescente).
- `retry_delay`: falha de INFRAESTRUTURA (cota, provedor, tempo) espera cada vez
  mais (até 6 h) e NUNCA conta para o teto -- cota esgotada por um dia não pode
  transformar título bom em "não entendido". Falha de CONTEÚDO
  (`invalid_response`/`request_rejected`) conta: no teto o título segue para a
  leitura de página (`awaiting_page`, se há produto de origem) ou termina
  `unrecognized`, visível para revisão.
- `AIBreaker`: disjuntor por processo. Depois de uma falha de infraestrutura a
  IA de identidade fica pausada para TODOS os títulos por alguns minutos (ou até
  o reset da cota informado pelo provedor, no máximo 1 h), em vez de cada título
  falhar um a um. Os títulos pulados também ganham candidato (`circuit_open`,
  sem gastar tentativa).
- `clear_ai_failed`: a decisão real (a IA respondeu) substitui a linha `ai_failed`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.time import utc_now
from app.products.identity_ai import INFRA_FAILURE_KINDS, AIExtractionFailure
from app.products.identity_candidates import ProductIdentityCandidate

logger = logging.getLogger("app.products.identity_ai_failure")

MAX_CONTENT_ATTEMPTS = 5
"""Tentativas com resposta inválida antes de mandar o título para a página."""

_CONTENT_DELAYS = (
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),
    timedelta(hours=24),
)
_INFRA_BASE = timedelta(minutes=15)
_INFRA_MAX = timedelta(hours=6)

BREAKER_KINDS = frozenset(
    {"quota", "provider_unavailable", "timeout", "provider_error"}
)
BREAKER_DEFAULT = timedelta(minutes=5)
BREAKER_MAX = timedelta(hours=1)


def retry_delay(kind: str, attempts: int) -> timedelta:
    """Espera antes da próxima chamada de IA do título, dado o total de
    tentativas já feitas (>= 1)."""
    step = max(attempts, 1) - 1
    if kind in INFRA_FAILURE_KINDS:
        return min(_INFRA_BASE * (2 ** min(step, 10)), _INFRA_MAX)
    return _CONTENT_DELAYS[min(step, len(_CONTENT_DELAYS) - 1)]


class AIBreaker:
    """Disjuntor simples, em memória, por processo (worker e API são processos
    diferentes e cada um aprende sozinho)."""

    def __init__(self) -> None:
        self._open_until: datetime | None = None

    def open_until(self, now: datetime) -> datetime | None:
        if self._open_until is not None and now < self._open_until:
            return self._open_until
        return None

    def trip(self, failure: AIExtractionFailure, now: datetime) -> None:
        if failure.kind not in BREAKER_KINDS:
            return
        wait = BREAKER_DEFAULT
        if failure.quota_reset_at is not None and failure.quota_reset_at > now:
            wait = min(failure.quota_reset_at - now, BREAKER_MAX)
        until = now + wait
        if self._open_until is None or until > self._open_until:
            self._open_until = until

    def reset(self) -> None:
        self._open_until = None


AI_BREAKER = AIBreaker()


def is_retry_due(candidate: ProductIdentityCandidate, now: datetime) -> bool:
    return candidate.next_retry_at is None or candidate.next_retry_at <= now


async def clear_ai_failed(session: AsyncSession, title_hash: str) -> None:
    """A IA respondeu: a decisão que vai ser gravada substitui a linha de falha."""
    await session.execute(
        delete(ProductIdentityCandidate).where(
            ProductIdentityCandidate.normalized_title_hash == title_hash,
            ProductIdentityCandidate.status == "ai_failed",
        )
    )


async def record_ai_failure(
    session: AsyncSession,
    *,
    raw_title: str,
    title_hash: str,
    failure: AIExtractionFailure,
    now: datetime | None = None,
    source_product_id: UUID | None = None,
    count_attempt: bool = True,
    retry_at: datetime | None = None,
) -> None:
    """Grava (ou atualiza) o candidato `ai_failed` do título. Nunca derruba o
    chamador: corrida com outra decisão gravada para o mesmo título é ignorada
    (a decisão real já venceu). O commit é de quem chama."""
    moment = now or utc_now()
    try:
        async with session.begin_nested():
            existing = await session.scalar(
                select(ProductIdentityCandidate).where(
                    ProductIdentityCandidate.normalized_title_hash == title_hash
                )
            )
            if existing is not None and existing.status != "ai_failed":
                return  # já existe decisão de verdade para este título
            attempts = (existing.ai_attempts if existing else 0) + (
                1 if count_attempt else 0
            )
            capped = (
                failure.kind not in INFRA_FAILURE_KINDS
                and attempts >= MAX_CONTENT_ATTEMPTS
            )
            if capped:
                next_status = "awaiting_page" if source_product_id else "unrecognized"
                _apply_terminal(
                    existing,
                    session,
                    raw_title=raw_title,
                    title_hash=title_hash,
                    status=next_status,
                    attempts=attempts,
                    source_product_id=source_product_id,
                    now=moment,
                )
                await session.flush()
                logger.warning(
                    "product_identity_ai_failed_cap_reached",
                    extra={"status": next_status, "kind": failure.kind},
                )
                return
            due = retry_at or moment + retry_delay(failure.kind, max(attempts, 1))
            if existing is None:
                session.add(
                    ProductIdentityCandidate(
                        id=uuid4(),
                        raw_title=raw_title[:2000],
                        normalized_title_hash=title_hash,
                        status="ai_failed",
                        grounded=False,
                        ai_attempts=attempts,
                        ai_error_kind=failure.kind,
                        next_retry_at=due,
                        source_product_id=source_product_id,
                        created_at=moment,
                    )
                )
            else:
                existing.ai_attempts = attempts
                existing.ai_error_kind = failure.kind
                existing.next_retry_at = due
                if source_product_id is not None:
                    existing.source_product_id = source_product_id
            await session.flush()
    except IntegrityError:
        # Outra coleta gravou uma linha para o mesmo título entre a consulta e
        # o INSERT -- nada a perder, a decisão dela vale.
        logger.info("product_identity_ai_failure_record_raced")


def _apply_terminal(
    existing: ProductIdentityCandidate | None,
    session: AsyncSession,
    *,
    raw_title: str,
    title_hash: str,
    status: str,
    attempts: int,
    source_product_id: UUID | None,
    now: datetime,
) -> None:
    if existing is None:
        existing = ProductIdentityCandidate(
            id=uuid4(),
            raw_title=raw_title[:2000],
            normalized_title_hash=title_hash,
            grounded=False,
            created_at=now,
        )
        session.add(existing)
    existing.status = status
    existing.ai_attempts = attempts
    existing.ai_error_kind = None
    existing.next_retry_at = None
    existing.page_attempts = 0
    if source_product_id is not None:
        existing.source_product_id = source_product_id
