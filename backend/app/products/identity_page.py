"""TASK-128 etapa 2 -- "não entendi" vira leitura da página do produto.

Decisão do usuário (2026-09-26): quando a IA não entende nem a categoria
de um título de loja, "retornar ao GG que não entendeu e abrir o worker e
ler a página e passar mais detalhes para a IA". O "não entendi" já fica
registrado no cache (`ProductIdentityCandidate.status = "awaiting_page"`,
com o Product de origem); esta varredura roda no INÍCIO de cada ciclo do
worker (`CollectionOrchestrator.run_batch`), com orçamento pequeno e em
sequência (nunca abas simultâneas na mesma loja):

1. reserva até `budget` títulos em transação curta (tentativa + horário);
2. abre a página de uma Offer do Product de origem pelo provider da loja
   (`CollectionAdapter.read_product_page`, mesma aba de detalhe Edge/CDP);
3. chama a IA com título + dados da página -- fora de qualquer transação;
4. substitui o `awaiting_page` pela decisão nova (exata, revisão, parcial
   ou `unrecognized`) e vincula o Product de origem.

Salvaguarda (nunca um laço de IA/navegação): loja que não abre página de
produto vira `unrecognized` na hora; falha passageira (bloqueio, página
vazia, IA fora do ar) tenta de novo depois de `PAGE_RETRY_AFTER`, no
máximo `MAX_PAGE_ATTEMPTS` vezes, e então vira `unrecognized`. Também
funciona para os "não entendi" gravados pelo backfill (que não tem
navegador): a varredura do worker os resolve depois."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider import AIProviderManager
from app.collection.contracts import ProductPageRead, ProductPageReadStatus
from app.database.time import utc_now
from app.offers.models import Offer
from app.products.identity import normalize_for_grounding
from app.products.identity_ai import PartialProductLink, extract_product_identity_via_ai
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import (
    _find_same_model_candidates,
    _PreparedResolution,
    _resolve_from_extraction,
    apply_learned_identity,
    apply_partial_link,
)
from app.products.models import Product
from app.stores.models import Store
from app.users.models import UserRole

logger = logging.getLogger("app.products.identity_page")

ProductPageReader = Callable[[str, str], Awaitable[ProductPageRead]]
"""`(store_code, url) -> ProductPageRead` -- em produção,
`CollectionAdapter.read_product_page`."""

MAX_PAGE_ATTEMPTS = 3
PAGE_RETRY_AFTER = timedelta(minutes=30)

_LINKED = "linked"
_UNRECOGNIZED = "unrecognized"
_RETRY = "retry"


@dataclass(frozen=True, slots=True)
class AwaitingPageSweepSummary:
    claimed: int = 0
    linked: int = 0
    """Títulos que saíram com vínculo (exato ou parcial)."""
    unrecognized: int = 0
    """Títulos que ficaram TERMINAIS (nem a página ajudou)."""
    retry_later: int = 0


@dataclass(frozen=True, slots=True)
class _PageClaim:
    candidate_id: UUID
    raw_title: str
    title_hash: str
    source_product_id: UUID | None
    attempt: int
    store_code: str | None
    url: str | None


async def _claim_awaiting_page_titles(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    budget: int,
    now: datetime,
    max_attempts: int,
    retry_after: timedelta,
) -> list[_PageClaim]:
    """Transação curta: encerra os títulos que já esgotaram as tentativas
    (caso uma tentativa tenha caído no meio) e reserva os próximos --
    `page_read_at = now` impede outro ciclo de abrir a mesma página antes
    de `retry_after`. Só leitura local + UPDATE; nenhum I/O externo."""
    released_before = now - retry_after
    async with session_factory() as session, session.begin():
        await session.execute(
            update(ProductIdentityCandidate)
            .where(
                ProductIdentityCandidate.status == "awaiting_page",
                ProductIdentityCandidate.page_attempts >= max_attempts,
                ProductIdentityCandidate.page_read_at <= released_before,
            )
            .values(status="unrecognized")
        )
        rows = (
            await session.execute(
                select(
                    ProductIdentityCandidate.id,
                    ProductIdentityCandidate.raw_title,
                    ProductIdentityCandidate.normalized_title_hash,
                    ProductIdentityCandidate.source_product_id,
                    ProductIdentityCandidate.page_attempts,
                )
                .where(
                    ProductIdentityCandidate.status == "awaiting_page",
                    ProductIdentityCandidate.page_attempts < max_attempts,
                    or_(
                        ProductIdentityCandidate.page_read_at.is_(None),
                        ProductIdentityCandidate.page_read_at <= released_before,
                    ),
                )
                .order_by(
                    ProductIdentityCandidate.page_attempts,
                    ProductIdentityCandidate.created_at,
                )
                .limit(budget)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claims: list[_PageClaim] = []
        for row in rows:
            attempt = row.page_attempts + 1
            await session.execute(
                update(ProductIdentityCandidate)
                .where(ProductIdentityCandidate.id == row.id)
                .values(page_attempts=attempt, page_read_at=now)
            )
            store_code = url = None
            if row.source_product_id is not None:
                offer = (
                    await session.execute(
                        select(Store.code, Offer.url)
                        .join(Store, Store.id == Offer.store_id)
                        .where(
                            Offer.product_id == row.source_product_id,
                            Offer.superseded_by_id.is_(None),
                        )
                        .order_by(Offer.last_seen_at.desc())
                        .limit(1)
                    )
                ).first()
                if offer is not None:
                    store_code, url = offer
            claims.append(
                _PageClaim(
                    candidate_id=row.id,
                    raw_title=row.raw_title,
                    title_hash=row.normalized_title_hash,
                    source_product_id=row.source_product_id,
                    attempt=attempt,
                    store_code=store_code,
                    url=url,
                )
            )
    return claims


async def _mark_unrecognized(
    session_factory: async_sessionmaker[AsyncSession], claim: _PageClaim
) -> str:
    async with session_factory() as session, session.begin():
        await session.execute(
            update(ProductIdentityCandidate)
            .where(
                ProductIdentityCandidate.id == claim.candidate_id,
                ProductIdentityCandidate.status == "awaiting_page",
            )
            .values(status="unrecognized")
        )
    return _UNRECOGNIZED


async def _retry_later_or_give_up(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _PageClaim,
    *,
    max_attempts: int,
) -> str:
    if claim.attempt >= max_attempts:
        return await _mark_unrecognized(session_factory, claim)
    return _RETRY


async def _resolve_with_page(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _PageClaim,
    *,
    page_reader: ProductPageReader,
    ai_manager: AIProviderManager,
    profile: UserRole,
    arbiter_ai_manager: AIProviderManager | None,
    now: datetime,
    max_attempts: int,
) -> str:
    if claim.store_code is None or claim.url is None:
        # Product de origem sumiu (fundido) ou nunca teve Offer ativa --
        # não há página para abrir agora.
        return await _retry_later_or_give_up(
            session_factory, claim, max_attempts=max_attempts
        )
    page = await page_reader(claim.store_code, claim.url)
    if page.status is ProductPageReadStatus.UNSUPPORTED:
        return await _mark_unrecognized(session_factory, claim)
    if page.status is not ProductPageReadStatus.READ:
        return await _retry_later_or_give_up(
            session_factory, claim, max_attempts=max_attempts
        )
    extraction = await extract_product_identity_via_ai(
        ai_manager,
        raw_title=claim.raw_title,
        profile=profile,
        page_context=page.context,
    )
    if extraction is None:
        # IA fora do ar/resposta fora do contrato -- passageiro.
        return await _retry_later_or_give_up(
            session_factory, claim, max_attempts=max_attempts
        )
    async with session_factory() as session:
        arbitration = await _find_same_model_candidates(
            session, normalize_for_grounding(claim.raw_title)
        )
        # Só leituras até aqui; o árbitro de IA (zona cinzenta) pode rodar
        # dentro de `_resolve_from_extraction` -- nunca com transação aberta.
        await session.rollback()
        resolved = await _resolve_from_extraction(
            session,
            raw_title=claim.raw_title,
            prepared=_PreparedResolution(
                done=False,
                resolved=None,
                title_hash=claim.title_hash,
                arbitration_candidates=tuple(arbitration),
            ),
            extraction=extraction,
            ai_manager=ai_manager,
            arbiter_ai_manager=arbiter_ai_manager,
            now=now,
            page_context=page.context,
            replace_awaiting_page=True,
        )
        if resolved is not None and claim.source_product_id is not None:
            product = await session.get(Product, claim.source_product_id)
            if product is not None and product.identity_key is None:
                if isinstance(resolved, PartialProductLink):
                    apply_partial_link(product, resolved)
                else:
                    await apply_learned_identity(
                        session, product=product, resolved=resolved
                    )
        await session.commit()
    return _LINKED if resolved is not None else _UNRECOGNIZED


async def resolve_awaiting_page_titles(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    page_reader: ProductPageReader,
    ai_manager: AIProviderManager,
    profile: UserRole = UserRole.ADMIN,
    arbiter_ai_manager: AIProviderManager | None = None,
    budget: int = 3,
    now: datetime | None = None,
    max_attempts: int = MAX_PAGE_ATTEMPTS,
    retry_after: timedelta = PAGE_RETRY_AFTER,
) -> AwaitingPageSweepSummary:
    """Varredura do worker (ver docstring do módulo). Um título que falha
    por erro inesperado nunca derruba os demais nem o ciclo -- a tentativa
    já foi contada na reserva, então ele volta depois (ou encerra)."""
    if budget <= 0:
        return AwaitingPageSweepSummary()
    moment = now or utc_now()
    claims = await _claim_awaiting_page_titles(
        session_factory,
        budget=budget,
        now=moment,
        max_attempts=max_attempts,
        retry_after=retry_after,
    )
    outcomes: Counter[str] = Counter()
    for claim in claims:
        try:
            outcome = await _resolve_with_page(
                session_factory,
                claim,
                page_reader=page_reader,
                ai_manager=ai_manager,
                profile=profile,
                arbiter_ai_manager=arbiter_ai_manager,
                now=moment,
                max_attempts=max_attempts,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "identity_page_read_title_failed",
                extra={"candidate_id": str(claim.candidate_id)},
                exc_info=True,
            )
            outcome = _RETRY
        outcomes[outcome] += 1
    return AwaitingPageSweepSummary(
        claimed=len(claims),
        linked=outcomes[_LINKED],
        unrecognized=outcomes[_UNRECOGNIZED],
        retry_later=outcomes[_RETRY],
    )
