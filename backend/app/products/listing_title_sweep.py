"""TASK-132 (Parte A): varredura de IA dos títulos novos de anúncios vigiados.

A coleta nunca chama IA. Quando o título de uma Offer já cadastrada muda e
nenhuma regra local o reconhece (`OfferIdentityWatch.needs_ai`), esta varredura,
no início do ciclo do worker e com orçamento (`listing_title_check_budget`),
pede a identidade ao mesmo caminho de sempre (`resolve_or_learn_product_variant`:
extrator -> catálogo -> cache -> reuso -> IA). O que a IA aprende entra no cache
de candidatos e no catálogo; a próxima coleta do anúncio reencontra o título já
resolvido e conta como avistamento. IA só quando o título muda -- título estável
nunca gera chamada.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider import AIProviderManager
from app.database.time import utc_now
from app.offers.listing_change import MAX_AI_ATTEMPTS, apply_pending_identity
from app.offers.models import OfferIdentityWatch
from app.products.identity import ResolvedProductVariant
from app.products.identity_learning import resolve_or_learn_product_variant
from app.users.models import UserRole

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ListingTitleSweepSummary:
    claimed: int = 0
    changed: int = 0
    resolved_same: int = 0
    unresolved: int = 0


@dataclass(frozen=True, slots=True)
class _Claim:
    offer_id: UUID
    title: str


async def _claim(
    session_factory: async_sessionmaker[AsyncSession], budget: int
) -> list[_Claim]:
    async with session_factory() as session, session.begin():
        rows = (
            await session.scalars(
                select(OfferIdentityWatch)
                .where(
                    OfferIdentityWatch.needs_ai.is_(True),
                    OfferIdentityWatch.ai_attempts < MAX_AI_ATTEMPTS,
                )
                .order_by(OfferIdentityWatch.first_seen_at, OfferIdentityWatch.offer_id)
                .limit(budget)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            row.ai_attempts += 1  # contada na reserva: falha não vira laço
        return [_Claim(row.offer_id, row.pending_title) for row in rows]


async def _resolve_one(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _Claim,
    *,
    ai_manager: AIProviderManager,
    profile: UserRole,
    arbiter_ai_manager: AIProviderManager | None,
    now: datetime,
) -> str:
    async with session_factory() as session:
        resolved = await resolve_or_learn_product_variant(
            session,
            raw_title=claim.title,
            ai_manager=ai_manager,
            profile=profile,
            arbiter_ai_manager=arbiter_ai_manager,
            now=now,
        )
        if not isinstance(resolved, ResolvedProductVariant):
            await session.commit()
            return "unresolved"
        watch = await session.get(OfferIdentityWatch, claim.offer_id)
        if watch is None or watch.pending_title != claim.title:
            await session.commit()
            return "unresolved"
        changed = await apply_pending_identity(session, watch, resolved)
        await session.commit()
        return "changed" if changed else "resolved_same"


async def sweep_listing_titles(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ai_manager: AIProviderManager,
    profile: UserRole = UserRole.ADMIN,
    arbiter_ai_manager: AIProviderManager | None = None,
    budget: int = 3,
    now: datetime | None = None,
) -> ListingTitleSweepSummary:
    if budget <= 0:
        return ListingTitleSweepSummary()
    moment = now or utc_now()
    claims = await _claim(session_factory, budget)
    outcomes: Counter[str] = Counter()
    for claim in claims:
        try:
            outcome = await _resolve_one(
                session_factory,
                claim,
                ai_manager=ai_manager,
                profile=profile,
                arbiter_ai_manager=arbiter_ai_manager,
                now=moment,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "listing_title_sweep_failed",
                extra={"offer_id": str(claim.offer_id)},
                exc_info=True,
            )
            outcome = "unresolved"
        outcomes[outcome] += 1
    return ListingTitleSweepSummary(
        claimed=len(claims),
        changed=outcomes["changed"],
        resolved_same=outcomes["resolved_same"],
        unresolved=outcomes["unresolved"],
    )
