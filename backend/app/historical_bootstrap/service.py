"""Bootstrap histórico externo one-shot da FASE F1."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider.contracts import (
    AIMessage,
    AIMessageRole,
    AIProviderManager,
    AIRequest,
)
from app.collection.models import OfferCondition, PriceObservation
from app.historical_bootstrap.models import (
    ExternalPriceReference,
    HistoricalBootstrap,
    HistoricalBootstrapStatus,
)
from app.offers.models import Offer
from app.products.identity import ResolvedProductVariant, resolve_product_variant
from app.products.models import Product
from app.search.cesar_core_fetch import CesarCoreFetchError, CesarCoreFetchProvider
from app.search.manager import WebSearchManager
from app.search.url_safety import (
    is_safe_to_fetch,
    redact_sensitive_query_values,
    safe_url_for_evidence,
    url_for_fetch_request,
)
from app.users.models import UserRole

logger = logging.getLogger(__name__)

_PRICE = re.compile(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})")
_DATE = re.compile(r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b")
_MAX_RESULTS = 6
_MAX_FETCHES = 3
_MAX_AI_CONTENT = 4_000


@dataclass(frozen=True, slots=True)
class HistoricalCandidate:
    source: str
    amount: Decimal
    historical_date: date | None
    safe_url: str
    store_name: str | None
    identity_text: str
    match_kind: str = "exact"
    """``"exact"`` quando a evidência resolve para o mesmo `identity_key`
    do Product; ``"family"`` quando bate `family_key` (mesmo modelo) e
    tudo que o Product de fato pediu (`attributes`/`variant`), mas a
    evidência carrega um fabricante/variante que o Product não
    especificou -- nunca uma variante DIFERENTE da pedida, só uma que o
    pedido deixou em aberto (`_match`)."""
    evidence_attributes: tuple[tuple[str, str], ...] = ()


async def get_external_price_reference_evidence(
    session: AsyncSession,
    *,
    product_id: UUID,
    currency: str,
) -> ExternalPriceReference | None:
    """FASE F3: menor preço externo já coletado pela FASE F1 para este
    `product_id`, reaproveitado por
    `app.market_research.service.run_market_research` para nunca repetir
    uma busca Firecrawl que a F1 já fez. Um preço histórico é um FATO
    ("este produto foi encontrado por este valor naquele momento") --
    `collected_at` é só metadado de quando a referência foi obtida, nunca
    um prazo de validade; a idade da referência NUNCA é motivo para
    descartá-la aqui. `None` só quando não há nenhuma referência para
    este `product_id`/moeda (nunca coletada) -- o chamador cai no
    caminho de pesquisa existente, nunca inventa um valor. Revalidação/
    refresh do histórico externo (buscar de novo para enriquecer ou
    achar um preço ainda menor) é um conceito FUTURO e DIFERENTE deste
    -- ainda sem política decidida, não implementado aqui."""
    return await session.scalar(
        select(ExternalPriceReference)
        .where(
            ExternalPriceReference.product_id == product_id,
            ExternalPriceReference.currency == currency,
        )
        .order_by(ExternalPriceReference.amount.asc())
        .limit(1)
    )


async def internal_history_is_sufficient(
    session: AsyncSession, *, product_id: UUID, now: datetime
) -> bool:
    """30 dias de cobertura e duas lojas, para BRL/new do mesmo Product."""
    row = (
        await session.execute(
            select(
                func.min(PriceObservation.observed_at),
                func.max(Offer.last_seen_at),
                func.count(func.distinct(Offer.store_id)),
            )
            .select_from(PriceObservation)
            .join(Offer, Offer.id == PriceObservation.offer_id)
            .where(
                Offer.product_id == product_id,
                PriceObservation.currency == "BRL",
                PriceObservation.condition == OfferCondition.NEW,
                PriceObservation.observed_at <= now,
            )
        )
    ).one()
    first, last_seen, stores = row
    return (
        first is not None
        and last_seen is not None
        and last_seen - first >= timedelta(days=30)
        and stores >= 2
    )


def _source(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return "hardware_barato" if host == "hardwarebarato.com" else host[:120]


def _parse_candidate(
    *, url: str, title: str, content: str
) -> HistoricalCandidate | None:
    """Preço é obrigatório; data é opcional (``historical_date=None`` quando
    ausente -- nunca inventada). Um snippet de busca raramente carrega uma
    data explícita, mas ainda assim é evidência histórica válida quando o
    preço e a identidade do produto batem; exigir data aqui forçaria Fetch
    mesmo quando a busca sozinha já bastaria."""
    prices = _PRICE.findall(content)
    if not prices:
        return None
    try:
        amount = Decimal(prices[0].replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None
    historical_date = None
    dates = _DATE.findall(content)
    if dates:
        try:
            historical_date = date(int(dates[0][2]), int(dates[0][1]), int(dates[0][0]))
        except ValueError:
            historical_date = None
    return HistoricalCandidate(
        _source(url),
        amount,
        historical_date,
        safe_url_for_evidence(url),
        None,
        f"{title} {content[:1000]}",
    )


@dataclass(frozen=True, slots=True)
class _Match:
    kind: str
    resolved: ResolvedProductVariant


def _match(product: Product, text: str) -> _Match | None:
    """Reusa a mesma lógica já usada em produção para "missão genérica
    aceita qualquer fabricante/variante, missão específica não"
    (`MissionCriteria.requested_family_key`/`requested_variant`,
    `app/missions/query.py`) -- nunca um conceito novo de "modelo-base vs
    SKU". `Product.family_key`/`attributes`/`variant` já refletem
    exatamente o que a missão pediu, populados pelo mesmo
    `resolve_product_variant` na criação/seleção do Product: uma missão
    genérica ("RTX 5070 Ti") nunca tem `board_brand` em `attributes`;
    uma missão de marca ("RTX 5070 Ti Gigabyte") tem. Por isso comparar
    contra o que o Product de fato tem -- nunca contra `identity_key`
    isolado -- já aceita evidência de QUALQUER fabricante quando a missão
    não pediu um específico, e recusa evidência de um fabricante/variante
    DIFERENTE do pedido quando a missão pediu um.

    Retorna ``None`` sem bater; ``_Match(kind="exact", ...)`` quando a
    evidência resolve para o mesmo `identity_key` do Product (nada a mais
    foi aprendido); ``_Match(kind="family", ...)`` quando bate no nível
    que o Product de fato pediu mas a evidência carrega um
    fabricante/variante que o Product deixou em aberto -- o chamador
    preserva isso em `match_evidence`, nunca descarta."""
    resolved = resolve_product_variant(text)
    if resolved is None or product.family_key is None:
        return None
    if resolved.family_key != product.family_key:
        return None
    required_attributes = dict(product.attributes or {})
    evidence_attributes = dict(resolved.attributes)
    if any(
        evidence_attributes.get(name) != value
        for name, value in required_attributes.items()
    ):
        return None
    if product.variant and resolved.variant != product.variant:
        return None
    kind = "exact" if resolved.identity_key == product.identity_key else "family"
    return _Match(kind=kind, resolved=resolved)


async def _interpret_ambiguous(
    ai: AIProviderManager,
    *,
    product: Product,
    title: str,
    content: str,
    url: str,
    profile: UserRole,
    now: datetime,
) -> HistoricalCandidate | None:
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose="historical_price_bootstrap",
        messages=(
            AIMessage(
                AIMessageRole.SYSTEM,
                "Extraia somente fatos explícitos da evidência. Responda JSON com product_identity_text, price_brl e date YYYY-MM-DD; use null quando ausente. Não infira nem invente.",
            ),
            AIMessage(
                AIMessageRole.USER,
                json.dumps(
                    {
                        "expected_product": product.display_name or product.name,
                        "url": safe_url_for_evidence(url),
                        "title": title[:500],
                        "content": content[:_MAX_AI_CONTENT],
                    }
                ),
            ),
        ),
        requested_at=now,
    )
    try:
        data = json.loads((await ai.generate(request)).content)
        identity = data.get("product_identity_text")
        match = _match(product, identity) if isinstance(identity, str) else None
        if match is None:
            return None
        amount = Decimal(str(data["price_brl"]))
        observed = date.fromisoformat(data["date"])
        if amount < 0:
            return None
        return HistoricalCandidate(
            _source(url),
            amount,
            observed,
            safe_url_for_evidence(url),
            None,
            identity,
            match.kind,
            match.resolved.attributes,
        )
    except Exception, KeyError, TypeError, ValueError, InvalidOperation:
        return None


async def _collect_candidates(
    product: Product,
    *,
    search: WebSearchManager,
    fetch: CesarCoreFetchProvider | None,
    ai: AIProviderManager,
    profile: UserRole,
    now: datetime,
) -> tuple[HistoricalCandidate, ...]:
    """Numa revalidação (ver `run_historical_bootstrap`), esta função roda
    de novo sem nenhum filtro por fonte já conhecida -- uma fonte já
    coletada antes PODE aparecer de novo e ser considerada normalmente
    (ela nunca vira blacklist); o objetivo da revalidação é só permitir
    que evidência adicional (da mesma fonte com um preço novo, ou de uma
    fonte inteiramente nova) seja persistida quando aparecer, nunca
    restringir de onde ela pode vir. Deduplicação de evidência idêntica
    já persistida é responsabilidade do `INSERT ... ON CONFLICT` no
    ponto de persistência (`run_historical_bootstrap`), não desta
    função."""
    label = product.display_name or product.name
    found: list[HistoricalCandidate] = []
    queries = []
    if product.category in {"gpu", "cpu", "motherboard", "psu", "ram"}:
        queries.append(f'site:hardwarebarato.com/produtos "{label}" histórico preço')
    queries.append(f'"{label}" histórico de preço menor preço')
    results = []
    for query in queries:
        response = await search.search(query, limit=_MAX_RESULTS)
        results.extend(response.results)
    seen_urls: set[str] = set()
    fetch_budget = _MAX_FETCHES
    for result in results:
        if result.url in seen_urls:
            continue
        seen_urls.add(result.url)
        if not is_safe_to_fetch(result.url):
            continue

        # Search é sempre o primeiro passo: avalia SOMENTE título+snippet
        # antes de qualquer Fetch. Se já bastar (preço + identidade batem
        # no que o Product de fato pediu), persiste com ZERO Fetch e ZERO
        # IA para esta URL -- fabricante/variante que o Product não
        # restringiu é aceito e preservado (`_match`), nunca exigido.
        candidate = _parse_candidate(
            url=result.url, title=result.title, content=result.snippet
        )
        if candidate is not None:
            match = _match(product, candidate.identity_text)
            if match is not None:
                found.append(
                    replace(
                        candidate,
                        match_kind=match.kind,
                        evidence_attributes=match.resolved.attributes,
                    )
                )
                continue

        # Busca sozinha não bastou. Fetch é enrichment seletivo com
        # orçamento próprio (nunca todas as URLs retornadas pela busca).
        if fetch is None or fetch_budget <= 0:
            continue
        fetch_budget -= 1
        try:
            page = await fetch.scrape_basic(url_for_fetch_request(result.url))
        except CesarCoreFetchError:
            page = None
        if page is None:
            continue
        title = page.title or result.title
        content = page.markdown or result.snippet
        candidate = _parse_candidate(url=result.url, title=title, content=content)
        if candidate is not None:
            match = _match(product, candidate.identity_text)
            if match is not None:
                found.append(
                    replace(
                        candidate,
                        match_kind=match.kind,
                        evidence_attributes=match.resolved.attributes,
                    )
                )
                continue
        if candidate is None:
            continue
        ambiguous = await _interpret_ambiguous(
            ai,
            product=product,
            title=title,
            content=content,
            url=result.url,
            profile=profile,
            now=now,
        )
        if ambiguous is not None:
            found.append(ambiguous)
    return tuple(found)


async def _mark_bootstrap_failed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    bootstrap_id: UUID,
    now: datetime,
    error: BaseException,
    failure_backoff_minutes: float,
    failure_backoff_max_minutes: float,
) -> None:
    """Reaproveita EXATAMENTE o padrão já aprovado de
    `app.market_research.service.mark_assessment_failed` (TASK-113):
    backoff exponencial capado, `failure_count` lido da própria linha
    (nunca resetado pelo claim) e incrementado aqui. `retry_after` é
    exclusivo do caminho de FALHA -- nunca escreve `completed_at`
    (exclusivo do caminho de sucesso/revalidação de 90 dias) e nunca
    apaga nenhuma `ExternalPriceReference`. Só atualiza a linha se ela
    ainda estiver `PROCESSING` (evita sobrescrever um resultado que outro
    worker já finalizou)."""
    async with session_factory() as session, session.begin():
        row = await session.get(HistoricalBootstrap, bootstrap_id, with_for_update=True)
        failure_count = (row.failure_count if row is not None else 0) + 1
        backoff_minutes = min(
            failure_backoff_minutes * (2 ** (failure_count - 1)),
            failure_backoff_max_minutes,
        )
        retry_after = now + timedelta(minutes=backoff_minutes)
        safe_error = redact_sensitive_query_values(repr(error)[:2000]).text
        await session.execute(
            update(HistoricalBootstrap)
            .where(
                HistoricalBootstrap.id == bootstrap_id,
                HistoricalBootstrap.status == HistoricalBootstrapStatus.PROCESSING,
            )
            .values(
                status=HistoricalBootstrapStatus.FAILED,
                lease_until=None,
                retry_after=retry_after,
                failure_count=failure_count,
                last_error=safe_error[:2000],
            )
        )


async def _run_historical_bootstrap(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    product_id: UUID,
    search: Callable[[], WebSearchManager],
    fetch: CesarCoreFetchProvider | None,
    ai: AIProviderManager,
    profile: UserRole,
    now: datetime,
    revalidation_days: int,
    lease_seconds: float,
    failure_backoff_minutes: float,
    failure_backoff_max_minutes: float,
) -> HistoricalBootstrapStatus | None:
    async with session_factory() as session, session.begin():
        product = await session.get(Product, product_id)
        if (
            product is None
            or product.identity_key is None
            or await internal_history_is_sufficient(
                session, product_id=product_id, now=now
            )
        ):
            return None
        stale_before = now - timedelta(days=revalidation_days)
        lease_until = now + timedelta(seconds=lease_seconds)
        bootstrap_id = await session.scalar(
            insert(HistoricalBootstrap)
            .values(
                id=uuid4(),
                product_id=product_id,
                condition="new",
                currency="BRL",
                status=HistoricalBootstrapStatus.PROCESSING,
                started_at=now,
                lease_until=lease_until,
            )
            .on_conflict_do_update(
                constraint="uq_historical_bootstraps_scope",
                set_={
                    "status": HistoricalBootstrapStatus.PROCESSING,
                    "started_at": now,
                    "lease_until": lease_until,
                    # `completed_at`/`retry_after`/`failure_count` NUNCA
                    # são tocados aqui -- só a finalização toca (sucesso
                    # grava um `completed_at` novo e zera o resto; falha
                    # grava `retry_after`/`failure_count` e nunca mexe em
                    # `completed_at`). Mesmo padrão de
                    # `MarketPriceAssessment`: o claim não escreve
                    # `expires_at`, só `mark_assessment_ready` escreve.
                    # Zerar `completed_at` aqui apagaria a prova do
                    # último sucesso se ESTA tentativa falhar.
                },
                # Três caminhos de reclaim, nunca confundidos entre si:
                # (1) PROCESSING com lease vencido -- worker anterior
                #     travou/crashou/teve exceção não tratada;
                # (2) FAILED com retry_after vencido (ou nunca setado) --
                #     backoff de erro, independente da revalidação;
                # (3) COMPLETED_* com completed_at mais velho que
                #     `revalidation_days` -- revalidação periódica de
                #     sucesso, nunca por causa de falha.
                where=or_(
                    and_(
                        HistoricalBootstrap.status
                        == HistoricalBootstrapStatus.PROCESSING,
                        HistoricalBootstrap.lease_until < now,
                    ),
                    and_(
                        HistoricalBootstrap.status == HistoricalBootstrapStatus.FAILED,
                        or_(
                            HistoricalBootstrap.retry_after.is_(None),
                            HistoricalBootstrap.retry_after <= now,
                        ),
                    ),
                    and_(
                        HistoricalBootstrap.status.in_(
                            (
                                HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES,
                                HistoricalBootstrapStatus.COMPLETED_WITHOUT_REFERENCES,
                            )
                        ),
                        HistoricalBootstrap.completed_at < stale_before,
                    ),
                ),
            )
            .returning(HistoricalBootstrap.id)
        )
        if bootstrap_id is None:
            existing = await session.scalar(
                select(HistoricalBootstrap.status).where(
                    HistoricalBootstrap.product_id == product_id,
                    HistoricalBootstrap.condition == "new",
                    HistoricalBootstrap.currency == "BRL",
                )
            )
            return existing
        # Só para decidir o status final (abaixo) -- NUNCA usado para
        # filtrar `_collect_candidates` (uma fonte já conhecida pode
        # reaparecer e ser considerada normalmente; não é blacklist).
        has_existing_references = (
            await session.scalar(
                select(ExternalPriceReference.id)
                .where(ExternalPriceReference.product_id == product_id)
                .limit(1)
            )
        ) is not None
        detached = Product(
            id=product.id,
            name=product.name,
            display_name=product.display_name,
            category=product.category,
            variant=product.variant,
            attributes=dict(product.attributes or {}),
            family_key=product.family_key,
            identity_key=product.identity_key,
        )
    try:
        candidates = await _collect_candidates(
            detached, search=search(), fetch=fetch, ai=ai, profile=profile, now=now
        )
    except Exception as error:  # noqa: BLE001 -- nunca derruba a coleta
        await _mark_bootstrap_failed(
            session_factory,
            bootstrap_id=bootstrap_id,
            now=now,
            error=error,
            failure_backoff_minutes=failure_backoff_minutes,
            failure_backoff_max_minutes=failure_backoff_max_minutes,
        )
        return None
    # `candidates` é só o que esta rodada achou -- o status precisa
    # refletir o total cumulativo (referências já existentes +
    # candidatos desta rodada), nunca regredir para "sem referências"
    # quando uma revalidação não traz nada além do que já tinha.
    status = (
        HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
        if candidates or has_existing_references
        else HistoricalBootstrapStatus.COMPLETED_WITHOUT_REFERENCES
    )
    try:
        async with session_factory() as session, session.begin():
            for item in candidates:
                is_exact = item.match_kind == "exact"
                match_evidence: dict[str, Any] = {
                    "method": "identity_key_exact" if is_exact else "family_key_market"
                }
                if not is_exact:
                    # Missão não restringiu este atributo (ex.: fabricante
                    # da placa) -- a evidência veio de um mais específico;
                    # nunca descartar essa informação (correção explícita:
                    # não misturar referência de modelo/mercado com SKU
                    # exato sem metadata).
                    match_evidence["evidence_attributes"] = dict(
                        item.evidence_attributes
                    )
                # Numa revalidação, a MESMA fonte pode reaparecer com
                # evidência IDÊNTICA à já persistida (nunca vira duplicata
                # -- a constraint `uq_external_price_references_evidence`
                # já existe no modelo desde a FASE F1) ou com evidência
                # NOVA (preço diferente, data diferente, URL diferente da
                # mesma fonte, ou fonte inteiramente nova) -- nesse caso,
                # uma nova linha é persistida normalmente, nunca
                # substituindo a anterior.
                await session.execute(
                    insert(ExternalPriceReference)
                    .values(
                        id=uuid4(),
                        bootstrap_id=bootstrap_id,
                        product_id=product_id,
                        source=item.source,
                        reference_type="historical_price",
                        amount=item.amount,
                        currency="BRL",
                        historical_date=item.historical_date,
                        store_name=item.store_name,
                        safe_url=item.safe_url,
                        condition="new",
                        matched_identity_key=detached.identity_key,
                        match_evidence=match_evidence,
                        quality="verified" if is_exact else "family_match",
                        collected_at=now,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_external_price_references_evidence"
                    )
                )
            row = await session.get(
                HistoricalBootstrap, bootstrap_id, with_for_update=True
            )
            if row is not None:
                row.status = status
                row.completed_at = now
                row.lease_until = None
                row.retry_after = None
                row.failure_count = 0
                row.last_error = None
    except Exception as error:  # noqa: BLE001 -- nunca derruba a coleta
        # Evidência já coletada (`candidates`) é perdida nesta tentativa
        # (rollback da transação acima) -- mas NUNCA fica presa em
        # PROCESSING: registra FAILED com backoff, mesmo tratamento do
        # ponto de falha anterior. `ExternalPriceReference` já persistida
        # de rodadas ANTERIORES não é afetada (transação isolada).
        await _mark_bootstrap_failed(
            session_factory,
            bootstrap_id=bootstrap_id,
            now=now,
            error=error,
            failure_backoff_minutes=failure_backoff_minutes,
            failure_backoff_max_minutes=failure_backoff_max_minutes,
        )
        return None
    return status


async def run_historical_bootstrap(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    product_id: UUID,
    search: Callable[[], WebSearchManager],
    fetch: CesarCoreFetchProvider | None,
    ai: AIProviderManager,
    profile: UserRole,
    now: datetime,
    revalidation_days: int,
    lease_seconds: float,
    failure_backoff_minutes: float,
    failure_backoff_max_minutes: float,
) -> HistoricalBootstrapStatus | None:
    """Roda uma vez; depois disso, só roda de novo para REVALIDAR (sucesso
    antigo, `completed_at` + `revalidation_days`) ou para TENTAR DE NOVO
    depois de uma falha (`retry_after`/`failure_count`, backoff
    exponencial reaproveitado de `app.market_research.service.
    mark_assessment_failed`/TASK-113) -- os dois conceitos nunca se
    confundem: falha NUNCA atualiza `completed_at` como se fosse sucesso,
    e sucesso/revalidação NUNCA passam por `retry_after`. Nenhum dos dois
    jamais apaga `ExternalPriceReference` já persistida -- um preço
    histórico é um FATO ("este produto foi encontrado por este valor
    naquele momento"), que continua verdadeiro para sempre (decisão
    explícita do usuário, 2026-09-06). `lease_seconds` recupera uma linha
    presa em `PROCESSING` por crash/kill/exceção verdadeiramente não
    tratada (mesmo papel de `MarketPriceAssessment.lease_until`).

    `search` é uma FÁBRICA (`Callable[[], WebSearchManager]`), nunca um
    `WebSearchManager` já construído -- só é chamada depois de todas as
    checagens que podem encerrar o bootstrap sem precisar de Search
    (produto ausente, `identity_key` ausente, histórico interno já
    suficiente, nem revalidação nem retry ainda vencidos). Isso evita que
    a ausência de credencial de Search vire uma falha ANTECIPADA de toda
    a coleta quando o bootstrap nem chegaria a precisar dela.

    O bootstrap histórico é uma etapa AUXILIAR do pipeline de coleta:
    esta função NUNCA propaga exceção para o chamador, mesmo quando a
    própria tentativa de registrar `FAILED` falha (ex.: banco
    indisponível) -- nesse caso extremo, a linha permanece exatamente
    como estava (nunca um estado falso de sucesso) e a recuperação
    acontece pelo `lease_until` já gravado na próxima tentativa elegível.
    """
    try:
        return await _run_historical_bootstrap(
            session_factory,
            product_id=product_id,
            search=search,
            fetch=fetch,
            ai=ai,
            profile=profile,
            now=now,
            revalidation_days=revalidation_days,
            lease_seconds=lease_seconds,
            failure_backoff_minutes=failure_backoff_minutes,
            failure_backoff_max_minutes=failure_backoff_max_minutes,
        )
    except Exception:  # noqa: BLE001 -- rede de segurança final, ver docstring
        logger.warning("historical_bootstrap_unexpected_failure", exc_info=True)
        return None
