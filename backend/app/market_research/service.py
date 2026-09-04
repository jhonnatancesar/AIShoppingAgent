"""Orquestração da pesquisa de mercado externa (TASK-113, §33.1-§33.20).

Fluxo em três fases distintas, nunca misturadas (correção pós-plano,
ponto 3 -- nenhum lock/transação pode ficar aberto durante HTTP/LLM):

1. `claim_assessment` -- transação CURTA própria, só o UPSERT atômico do
   single-flight (§33.3). Comita e fecha antes de qualquer chamada
   externa.
2. `run_market_research` -- SEM transação: duas buscas WebSearchManager (mercado
   atual + histórico, §33.18), validação determinística de identidade
   (§33.31/correção ponto 5) e interpretação por IA (§33.16/correção
   ponto 6).
3. `mark_assessment_ready`/`mark_assessment_failed` -- transação CURTA
   própria, finaliza o resultado.

Quem decide GATILHO (`should_trigger_market_research`) e TTL
(`resolve_assessment_expiry`) são funções separadas, puras/quase-puras,
para serem testáveis sem rede.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider.contracts import (
    AIMessage,
    AIMessageRole,
    AIProviderManager,
    AIRequest,
)
from app.alerts.internal_history import get_internal_historical_best
from app.alerts.material_improvement import is_material_improvement
from app.alerts.models import MissionProductAlertState
from app.collection.cadence import CadenceConfig, resolve_product_market_mode
from app.core.config import Settings
from app.market_research.models import (
    AssessmentConfidence,
    MarketAssessmentStatus,
    MarketPriceAssessment,
    MarketPriceClassification,
)
from app.products.identity import resolve_product_variant
from app.products.models import Product
from app.search.firecrawl import FirecrawlSearchError, FirecrawlSearchProvider
from app.search.manager import WebSearchManager, build_web_search_manager
from app.search.telemetry import observe_scrape
from app.users.models import UserRole

logger = logging.getLogger(__name__)

_MARKET_RESEARCH_PURPOSE = "market_price_assessment"
_MAX_SEARCH_RESULTS = 6
_MAX_SCRAPE_URLS = 3

_CONFIDENCE_RANK = {
    AssessmentConfidence.LOW: 0,
    AssessmentConfidence.MEDIUM: 1,
    AssessmentConfidence.HIGH: 2,
}

_SYSTEM_PROMPT = (
    "Você avalia se um preço de produto é uma boa oferta, usando somente "
    "as evidências fornecidas (nunca conhecimento próprio, nunca invente "
    "fontes). Responda SOMENTE com um objeto JSON, sem texto ao redor, "
    "com exatamente estas chaves: "
    '{"classification": "excellent_deal"|"good_deal"|"normal_price"|'
    '"insufficient_evidence", "market_low": número ou null, '
    '"market_high": número ou null, "confidence": "low"|"medium"|"high" '
    "ou null, "
    '"historical_low_external": número ou null, '
    '"historical_low_source": string (URL, obrigatório se '
    "historical_low_external não for null, deve ser exatamente uma das "
    'URLs fornecidas em history_evidence) ou null, '
    '"historical_low_observed_at": string YYYY-MM-DD ou null}. '
    "Use market_evidence só para market_low/market_high/classification/"
    "confidence. Use history_evidence só para historical_low_external/"
    "historical_low_source/historical_low_observed_at. Nunca preencha "
    "historical_low_observed_at se a fonte não datar o preço "
    "explicitamente. Nunca declare confidence alto com menos de 3 "
    "evidências de domínios diferentes."
)


# ---------------------------------------------------------------------------
# Gatilho determinístico (§33.13) -- IA nunca decide se Firecrawl é chamada.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriggerSignals:
    current_amount: Decimal
    previous_amount: Decimal | None
    best_notified_amount: Decimal | None
    internal_historical_best: Decimal | None
    target_amount: Decimal | None
    rearmed_at: datetime | None
    last_notified_at: datetime | None
    has_valid_cached_assessment: bool


def should_trigger_market_research(
    signals: TriggerSignals,
    *,
    settings: Settings,
    now: datetime,
    realert_window: timedelta,
) -> bool:
    """Cinco sinais do §33.13 -- qualquer um verdadeiro dispara a pesquisa,
    exceto quando já existe assessment válido em cache (§33.14, checado
    por `has_valid_cached_assessment`, calculado pelo chamador antes)."""
    if signals.has_valid_cached_assessment:
        return False

    drop_percent = settings.market_research_trigger_drop_percent
    if (
        signals.previous_amount is not None
        and signals.previous_amount > 0
        and signals.current_amount
        <= signals.previous_amount * (1 - Decimal(str(drop_percent)))
    ):
        return True

    if (
        signals.internal_historical_best is not None
        and _is_material_improvement(
            reference_amount=signals.internal_historical_best,
            current_amount=signals.current_amount,
            settings=settings,
        )
    ):
        return True

    if signals.best_notified_amount is not None and _is_material_improvement(
        reference_amount=signals.best_notified_amount,
        current_amount=signals.current_amount,
        settings=settings,
    ):
        return True

    if (
        signals.rearmed_at is not None
        and signals.last_notified_at is not None
        and now - signals.last_notified_at >= realert_window
    ):
        return True

    if (
        signals.target_amount is not None
        and signals.current_amount <= signals.target_amount
        and signals.best_notified_amount is None
    ):
        return True

    return False


def _is_material_improvement(
    *, reference_amount: Decimal, current_amount: Decimal, settings: Settings
) -> bool:
    return is_material_improvement(
        reference_amount=reference_amount,
        current_amount=current_amount,
        percent=settings.material_improvement_percent,
        min_amount=settings.material_improvement_min_amount,
        max_amount=settings.material_improvement_max_amount,
    )


# ---------------------------------------------------------------------------
# TTL / modo de mercado (correção pós-plano, ponto 10) -- mesma função
# alimenta o TTL do assessment e a janela de re-alert do evaluator.
# ---------------------------------------------------------------------------


async def resolve_market_mode_hours(
    session: AsyncSession,
    *,
    product_id: UUID,
    now: datetime,
    normal_hours: int,
    promo_hours: int,
    cadence_config: CadenceConfig | None = None,
) -> int:
    """`normal_hours`/`promo_hours` são passados pelo chamador (TTL usa
    `market_assessment_ttl_*`, janela de re-alert usa `realert_*`) --
    mesma resolução de modo (`resolve_product_market_mode`), unidades
    diferentes por chamador (§33.11/§33.15)."""
    decision = await resolve_product_market_mode(
        session,
        product_id=product_id,
        now=now,
        config=cadence_config or CadenceConfig(),
    )
    return normal_hours if decision.mode == "normal" else promo_hours


async def resolve_assessment_expiry(
    session: AsyncSession, *, product_id: UUID, now: datetime, settings: Settings
) -> datetime:
    hours = await resolve_market_mode_hours(
        session,
        product_id=product_id,
        now=now,
        normal_hours=settings.market_assessment_ttl_normal_hours,
        promo_hours=settings.market_assessment_ttl_promo_hours,
    )
    return now + timedelta(hours=hours)


async def resolve_realert_window(
    session: AsyncSession, *, product_id: UUID, now: datetime, settings: Settings
) -> timedelta:
    hours = await resolve_market_mode_hours(
        session,
        product_id=product_id,
        now=now,
        normal_hours=settings.realert_normal_hours,
        promo_hours=settings.realert_promo_hours,
    )
    return timedelta(hours=hours)


# ---------------------------------------------------------------------------
# Snapshot -- valor simples cruzando Fase B -> Fase C (mesma disciplina de
# `_PhaseAOutcome`, TASK-079: nunca um objeto ORM preso a sessão fechada).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssessmentSnapshot:
    product_id: UUID
    status: MarketAssessmentStatus
    classification: MarketPriceClassification | None
    market_low: Decimal | None
    market_high: Decimal | None
    historical_low_external: Decimal | None
    historical_low_source: str | None
    historical_low_observed_at: date | None
    confidence: AssessmentConfidence | None
    reference_price: Decimal
    expires_at: datetime | None

    @property
    def is_good_or_excellent(self) -> bool:
        return self.classification in (
            MarketPriceClassification.GOOD_DEAL,
            MarketPriceClassification.EXCELLENT_DEAL,
        )

    def is_valid(self, *, now: datetime) -> bool:
        return (
            self.status is MarketAssessmentStatus.READY
            and self.expires_at is not None
            and self.expires_at > now
        )


def _snapshot_from_row(row: MarketPriceAssessment) -> AssessmentSnapshot:
    return AssessmentSnapshot(
        product_id=row.product_id,
        status=row.status,
        classification=row.classification,
        market_low=row.market_low,
        market_high=row.market_high,
        historical_low_external=row.historical_low_external,
        historical_low_source=row.historical_low_source,
        historical_low_observed_at=row.historical_low_observed_at,
        confidence=row.confidence,
        reference_price=row.reference_price,
        expires_at=row.expires_at,
    )


async def get_current_assessment_snapshot(
    session: AsyncSession, *, product_id: UUID
) -> AssessmentSnapshot | None:
    row = await session.get(MarketPriceAssessment, product_id)
    return _snapshot_from_row(row) if row is not None else None


def needs_refresh(
    snapshot: AssessmentSnapshot | None, *, current_amount: Decimal, now: datetime, settings: Settings
) -> bool:
    """§33.14: assessment `READY` válido é reaproveitado, salvo refresh
    antecipado por variação grande do preço de referência."""
    if snapshot is None:
        return True
    if not snapshot.is_valid(now=now):
        return True
    if snapshot.reference_price <= 0:
        return False
    variation = abs(current_amount - snapshot.reference_price) / snapshot.reference_price
    return variation >= Decimal(str(settings.market_assessment_price_refresh_percent))


# ---------------------------------------------------------------------------
# Single-flight com lease (§33.3, correção pós-plano ponto 1) -- estados
# PENDING/PROCESSING/READY/FAILED tratados explicitamente no WHERE do
# UPSERT, nunca um SELECT ... FOR UPDATE numa linha presumida existente.
# ---------------------------------------------------------------------------


class ClaimStatus(StrEnum):
    WON = "won"
    CACHE_HIT = "cache_hit"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True, slots=True)
class ClaimResult:
    status: ClaimStatus
    snapshot: AssessmentSnapshot | None


_CLAIM_SQL = text(
    """
    INSERT INTO market_price_assessments
        (product_id, status, reference_price, reference_currency, store_id,
         lease_until, created_at, updated_at)
    VALUES
        (:product_id, 'processing', :reference_price, :reference_currency,
         :store_id, :lease_until, :now, :now)
    ON CONFLICT (product_id) DO UPDATE SET
        status = 'processing',
        reference_price = EXCLUDED.reference_price,
        reference_currency = EXCLUDED.reference_currency,
        store_id = EXCLUDED.store_id,
        lease_until = EXCLUDED.lease_until,
        updated_at = :now
    WHERE
        (market_price_assessments.status = 'processing'
            AND market_price_assessments.lease_until < :now)
        OR (market_price_assessments.status = 'failed'
            AND (market_price_assessments.retry_after IS NULL
                OR market_price_assessments.retry_after <= :now))
        OR (market_price_assessments.status = 'ready'
            AND (
                market_price_assessments.expires_at <= :now
                OR (
                    market_price_assessments.reference_price > 0
                    AND abs(:reference_price - market_price_assessments.reference_price)
                        / market_price_assessments.reference_price >= :refresh_percent
                )
            ))
    RETURNING product_id
    """
)
"""`:now` é sempre o relógio passado pelo CHAMADOR (`claim_assessment`'s
`now`), nunca a função SQL `now()` (relógio real do Postgres) -- mesma
disciplina de "um único relógio por operação" já usada em todo o resto
do projeto (`effective_now`, `app.collection.cadence`). Achado real
durante os testes de integração: a versão anterior usava `now()` do
Postgres nestas comparações, o que tornava `lease_until`/`retry_after`/
`expires_at` fornecidos por teste (ou por um `now` de produção
congelado por qualquer motivo) completamente ignorados -- o claim
sempre comparava contra o relógio real da máquina, nunca contra o
`now` lógico da operação."""


async def claim_assessment(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    product_id: UUID,
    store_id: UUID | None,
    reference_price: Decimal,
    reference_currency: str,
    now: datetime,
    settings: Settings,
) -> ClaimResult:
    """Transação CURTA e própria -- comita e fecha antes de qualquer
    chamada externa (correção pós-plano, ponto 3)."""
    lease_until = now + timedelta(seconds=settings.market_assessment_lease_seconds)
    async with session_factory() as session, session.begin():
        won = (
            await session.execute(
                _CLAIM_SQL,
                {
                    "product_id": product_id,
                    "reference_price": reference_price,
                    "reference_currency": reference_currency,
                    "store_id": store_id,
                    "lease_until": lease_until,
                    "now": now,
                    "refresh_percent": Decimal(
                        str(settings.market_assessment_price_refresh_percent)
                    ),
                },
            )
        ).first()
        if won is not None:
            return ClaimResult(ClaimStatus.WON, None)

        row = await session.get(MarketPriceAssessment, product_id)
        if row is None:
            # Corrida extrema: outra transação venceu o INSERT e ainda
            # não comitou -- nunca deveria ser visível aqui (READ
            # COMMITTED só enxerga linhas já comitadas), mas tratado como
            # "em progresso" por segurança, nunca como erro.
            return ClaimResult(ClaimStatus.IN_PROGRESS, None)
        snapshot = _snapshot_from_row(row)
        if row.status is MarketAssessmentStatus.READY:
            return ClaimResult(ClaimStatus.CACHE_HIT, snapshot)
        return ClaimResult(ClaimStatus.IN_PROGRESS, snapshot)


async def mark_assessment_ready(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    product_id: UUID,
    classification: MarketPriceClassification,
    market_low: Decimal | None,
    market_high: Decimal | None,
    historical_low_external: Decimal | None,
    historical_low_source: str | None,
    historical_low_observed_at: date | None,
    confidence: AssessmentConfidence | None,
    evidence: dict[str, Any],
    expires_at: datetime,
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            update(MarketPriceAssessment)
            .where(MarketPriceAssessment.product_id == product_id)
            .values(
                status=MarketAssessmentStatus.READY,
                lease_until=None,
                retry_after=None,
                last_error=None,
                failure_count=0,
                classification=classification,
                market_low=market_low,
                market_high=market_high,
                historical_low_external=historical_low_external,
                historical_low_source=historical_low_source,
                historical_low_observed_at=historical_low_observed_at,
                confidence=confidence,
                evidence=evidence,
                expires_at=expires_at,
            )
        )


async def mark_assessment_failed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    product_id: UUID,
    error: str,
    now: datetime,
    settings: Settings,
) -> None:
    """Nunca permite loop imediato FAILED -> nova chamada (correção
    pós-plano, ponto 1): `retry_after` cresce por falha CONSECUTIVA
    (`failure_count`), capado."""
    async with session_factory() as session, session.begin():
        row = await session.get(
            MarketPriceAssessment, product_id, with_for_update=True
        )
        failure_count = (row.failure_count if row is not None else 0) + 1
        backoff_minutes = min(
            settings.market_assessment_failure_backoff_minutes
            * (2 ** (failure_count - 1)),
            settings.market_assessment_failure_backoff_max_minutes,
        )
        retry_after = now + timedelta(minutes=backoff_minutes)
        await session.execute(
            update(MarketPriceAssessment)
            .where(MarketPriceAssessment.product_id == product_id)
            .values(
                status=MarketAssessmentStatus.FAILED,
                lease_until=None,
                retry_after=retry_after,
                failure_count=failure_count,
                last_error=error[:2000],
            )
        )


# ---------------------------------------------------------------------------
# Evidência: construção de query + validação determinística de identidade
# (correção pós-plano, ponto 5 -- IA nunca autoriza equivalência de
# produto; só o Product Identity Engine, já usado pela TASK-097, decide).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    url: str
    domain: str
    title: str
    description: str | None


def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return netloc.removeprefix("www.")


def _evidence_matches_identity(product: Product, *, title: str, description: str | None) -> bool:
    """Fail-closed: só entra evidência cujo texto, reparseado pelo MESMO
    motor determinístico que gerou `Product.identity_key` (TASK-097),
    resolve para a identidade EXATA esperada. IA nunca decide isto."""
    if product.identity_key is None:
        return False
    text_to_parse = f"{title} {description or ''}".strip()
    resolved = resolve_product_variant(text_to_parse)
    return resolved is not None and resolved.identity_key == product.identity_key


def product_query_label(product: Product) -> str:
    return product.display_name or product.name


def build_market_query(product: Product) -> str:
    return f"{product_query_label(product)} preço comprar loja"


def build_history_query(product: Product) -> str:
    return f"{product_query_label(product)} menor preço histórico price history"


def _distinct_domain_urls(
    results: tuple[Any, ...], *, limit: int
) -> tuple[str, ...]:
    seen: set[str] = set()
    urls: list[str] = []
    for result in results:
        domain = _domain(result.url)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        urls.append(result.url)
        if len(urls) >= limit:
            break
    return tuple(urls)


async def _search_with_enrichment(
    search_manager: WebSearchManager,
    *,
    enrichment: FirecrawlSearchProvider | None,
    query: str,
    product: Product,
    min_items: int,
) -> tuple[EvidenceItem, ...]:
    """Busca pelo manager; enriquecimento separado em até 3 URLs distintas.

    Snippet insuficiente não provoca outra busca: permite somente scrape das
    URLs encontradas. Falha Search propaga para FAILED/retry_after; falha de
    enriquecimento por URL conserva o tratamento anterior, sem interromper as
    demais fontes. Zero resultados não inventa URLs nem executa Firecrawl.
    """
    response = await search_manager.search(query, limit=_MAX_SEARCH_RESULTS)
    compatible = [
        EvidenceItem(url=r.url, domain=_domain(r.url), title=r.title, description=r.description)
        for r in response.results
        if _evidence_matches_identity(product, title=r.title, description=r.description)
    ]
    if len({item.domain for item in compatible}) >= min_items and len(compatible) >= min_items:
        return tuple(compatible)

    if enrichment is None:
        return tuple(compatible)
    candidate_urls = _distinct_domain_urls(response.results, limit=_MAX_SCRAPE_URLS)
    for url in candidate_urls:
        try:
            page = await enrichment.scrape_basic(url)
        except FirecrawlSearchError:
            observe_scrape(outcome="failed", correlation_id=response.correlation_id)
            # Falha do SERVIÇO Firecrawl para esta URL -- desiste dela,
            # segue para a próxima (nunca contorna, nunca propaga).
            continue
        observe_scrape(
            outcome="success" if page is not None else "empty",
            credits=getattr(page, "credits_used", None),
            correlation_id=response.correlation_id,
        )
        if page is None:
            # Origem específica recusou/indisponível (`success: false`)
            # -- nunca um erro, só "sem evidência desta fonte".
            continue
        title = page.title or url
        if _evidence_matches_identity(product, title=title, description=page.markdown):
            compatible.append(
                EvidenceItem(url=url, domain=_domain(url), title=title, description=page.markdown)
            )
    return tuple(compatible)


# ---------------------------------------------------------------------------
# Interpretação por IA -- mesma convenção de `classify_offer_relevance`
# (prompt fechado + json.loads + qualquer desvio tratado como falha, nunca
# exceção fatal). Quórum/identidade são VALIDADOS AQUI, nunca só confiados
# ao JSON (correção pós-plano, ponto 6).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketResearchOutcome:
    classification: MarketPriceClassification
    market_low: Decimal | None
    market_high: Decimal | None
    confidence: AssessmentConfidence | None
    historical_low_external: Decimal | None
    historical_low_source: str | None
    historical_low_observed_at: date | None
    evidence: dict[str, Any]


def _parse_optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_optional_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_classification(value: object) -> MarketPriceClassification | None:
    if not isinstance(value, str):
        return None
    try:
        return MarketPriceClassification(value)
    except ValueError:
        return None


def _parse_confidence(value: object) -> AssessmentConfidence | None:
    if not isinstance(value, str):
        return None
    try:
        return AssessmentConfidence(value)
    except ValueError:
        return None


async def _interpret_evidence(
    ai_manager: AIProviderManager,
    *,
    profile: UserRole,
    product: Product,
    reference_price: Decimal,
    reference_currency: str,
    market_evidence: tuple[EvidenceItem, ...],
    history_evidence: tuple[EvidenceItem, ...],
    requested_at: datetime,
) -> dict[str, Any] | None:
    content = json.dumps(
        {
            "product": product_query_label(product),
            "reference_price": str(reference_price),
            "currency": reference_currency,
            "market_evidence": [
                {"url": e.url, "title": e.title, "description": e.description}
                for e in market_evidence
            ],
            "history_evidence": [
                {"url": e.url, "title": e.title, "description": e.description}
                for e in history_evidence
            ],
        }
    )
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=_MARKET_RESEARCH_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, content),
        ),
        requested_at=requested_at,
    )
    try:
        response = await ai_manager.generate(request)
        payload = json.loads(response.content)
        if not isinstance(payload, dict):
            raise ValueError("unexpected response shape")
        return payload
    except Exception:
        logger.warning("market_research_interpretation_failed", exc_info=False)
        return None


def _finalize_confidence(
    *, ai_confidence: AssessmentConfidence | None, domain_count: int
) -> AssessmentConfidence:
    """Correção pós-plano, ponto 6: quórum validado pelo CÓDIGO -- 2
    domínios nunca sai de MEDIUM, mesmo se a IA disser HIGH; HIGH exige
    3+ domínios E a IA também ter dito HIGH (nunca promovido sozinho)."""
    cap = AssessmentConfidence.HIGH if domain_count >= 3 else AssessmentConfidence.MEDIUM
    claimed = ai_confidence or AssessmentConfidence.MEDIUM
    return (
        claimed
        if _CONFIDENCE_RANK[claimed] <= _CONFIDENCE_RANK[cap]
        else cap
    )


def finalize_market_research(
    payload: dict[str, Any] | None,
    *,
    market_evidence: tuple[EvidenceItem, ...],
    history_evidence: tuple[EvidenceItem, ...],
    settings: Settings,
) -> MarketResearchOutcome:
    """Validação determinística, independente do que a IA afirma
    (correção pós-plano, ponto 6) -- esta função decide o resultado
    final; o JSON da IA só fornece os números/URLs candidatos."""
    market_domains = {item.domain for item in market_evidence}
    market_urls = {item.url for item in market_evidence}
    history_urls = {item.url for item in history_evidence}
    evidence_record: dict[str, Any] = {
        "market_evidence": [
            {"url": e.url, "domain": e.domain, "title": e.title} for e in market_evidence
        ],
        "history_evidence": [
            {"url": e.url, "domain": e.domain, "title": e.title} for e in history_evidence
        ],
        "ai_payload": payload,
    }

    has_market_quorum = (
        len(market_evidence) >= settings.market_assessment_min_market_sources
        and len(market_domains) >= settings.market_assessment_min_market_sources
    )
    market_low: Decimal | None = None
    market_high: Decimal | None = None
    confidence: AssessmentConfidence | None = None
    classification = MarketPriceClassification.INSUFFICIENT_EVIDENCE
    if has_market_quorum and payload is not None:
        low = _parse_optional_decimal(payload.get("market_low"))
        high = _parse_optional_decimal(payload.get("market_high"))
        if low is not None and high is not None and low <= high:
            market_low, market_high = low, high
            parsed_classification = _parse_classification(payload.get("classification"))
            classification = parsed_classification or MarketPriceClassification.INSUFFICIENT_EVIDENCE
            confidence = _finalize_confidence(
                ai_confidence=_parse_confidence(payload.get("confidence")),
                domain_count=len(market_domains),
            )

    historical_low_external: Decimal | None = None
    historical_low_source: str | None = None
    historical_low_observed_at: date | None = None
    if payload is not None and history_evidence:
        value = _parse_optional_decimal(payload.get("historical_low_external"))
        source = payload.get("historical_low_source")
        if (
            value is not None
            and isinstance(source, str)
            and source.strip()
            and source.strip() in history_urls
        ):
            historical_low_external = value
            historical_low_source = source.strip()
            historical_low_observed_at = _parse_optional_date(
                payload.get("historical_low_observed_at")
            )

    # `market_urls` só existe para eventual auditoria futura -- mantém a
    # variável documentada, sem uso adicional nesta versão.
    del market_urls

    return MarketResearchOutcome(
        classification=classification,
        market_low=market_low,
        market_high=market_high,
        confidence=confidence,
        historical_low_external=historical_low_external,
        historical_low_source=historical_low_source,
        historical_low_observed_at=historical_low_observed_at,
        evidence=evidence_record,
    )


# ---------------------------------------------------------------------------
# Orquestração ponta a ponta (chamada pela Fase B do collection pipeline).
# ---------------------------------------------------------------------------


async def run_market_research(
    session_factory: async_sessionmaker[AsyncSession],
    ai_manager: AIProviderManager,
    firecrawl: FirecrawlSearchProvider | None,
    *,
    product: Product,
    store_id: UUID | None,
    reference_price: Decimal,
    reference_currency: str,
    profile: UserRole,
    now: datetime,
    settings: Settings,
) -> AssessmentSnapshot | None:
    """Vencedor do claim (§33.3): pesquisa de verdade, fora de transação,
    e finaliza via `mark_assessment_ready`/`_failed` (transações curtas
    próprias). Perdedor: `CACHE_HIT` devolve o snapshot pronto; `IN_
    PROGRESS` devolve `None` (segue com dado local -- outro worker
    termina, ou o próximo ciclo encontra `READY`, nunca perdido
    para sempre, correção pós-plano ponto 12)."""
    claim = await claim_assessment(
        session_factory,
        product_id=product.id,
        store_id=store_id,
        reference_price=reference_price,
        reference_currency=reference_currency,
        now=now,
        settings=settings,
    )
    if claim.status is ClaimStatus.CACHE_HIT:
        return claim.snapshot
    if claim.status is ClaimStatus.IN_PROGRESS:
        return None

    try:
        search_manager = build_web_search_manager(settings, firecrawl)
        market_evidence = await _search_with_enrichment(
            search_manager,
            enrichment=firecrawl,
            query=build_market_query(product),
            product=product,
            min_items=settings.market_assessment_min_market_sources,
        )
        history_evidence = await _search_with_enrichment(
            search_manager,
            enrichment=firecrawl,
            query=build_history_query(product),
            product=product,
            min_items=1,
        )
        payload = await _interpret_evidence(
            ai_manager,
            profile=profile,
            product=product,
            reference_price=reference_price,
            reference_currency=reference_currency,
            market_evidence=market_evidence,
            history_evidence=history_evidence,
            requested_at=now,
        )
        outcome = finalize_market_research(
            payload,
            market_evidence=market_evidence,
            history_evidence=history_evidence,
            settings=settings,
        )
    except Exception as error:  # noqa: BLE001 -- nunca derruba a coleta (§33.20)
        logger.warning("market_research_failed", exc_info=True)
        await mark_assessment_failed(
            session_factory,
            product_id=product.id,
            error=repr(error)[:2000],
            now=now,
            settings=settings,
        )
        return None

    expires_at = await _resolve_expiry_with_own_session(
        session_factory, product_id=product.id, now=now, settings=settings
    )
    await mark_assessment_ready(
        session_factory,
        product_id=product.id,
        classification=outcome.classification,
        market_low=outcome.market_low,
        market_high=outcome.market_high,
        historical_low_external=outcome.historical_low_external,
        historical_low_source=outcome.historical_low_source,
        historical_low_observed_at=outcome.historical_low_observed_at,
        confidence=outcome.confidence,
        evidence=outcome.evidence,
        expires_at=expires_at,
    )
    return AssessmentSnapshot(
        product_id=product.id,
        status=MarketAssessmentStatus.READY,
        classification=outcome.classification,
        market_low=outcome.market_low,
        market_high=outcome.market_high,
        historical_low_external=outcome.historical_low_external,
        historical_low_source=outcome.historical_low_source,
        historical_low_observed_at=outcome.historical_low_observed_at,
        confidence=outcome.confidence,
        reference_price=reference_price,
        expires_at=expires_at,
    )


async def evaluate_trigger_and_maybe_research(
    session_factory: async_sessionmaker[AsyncSession],
    ai_manager: AIProviderManager,
    firecrawl: FirecrawlSearchProvider | None,
    *,
    mission_id: UUID,
    product_id: UUID,
    store_id: UUID,
    current_amount: Decimal,
    current_currency: str,
    previous_amount: Decimal | None,
    target_amount: Decimal | None,
    profile: UserRole,
    now: datetime,
    settings: Settings,
) -> AssessmentSnapshot | None:
    """Passo único chamado pela Fase B do pipeline de coleta
    (`app.collection.orchestration._run_phase_b`) -- decoupled de
    `_PendingOffer`/`_PhaseAOutcome` de propósito (aceita só valores
    simples) para nunca criar import circular com `orchestration.py`.

    Fail-closed (§33.1): `Product.identity_key IS NULL` nunca dispara
    pesquisa nem lê/escreve nada além da leitura do próprio `Product`.
    Quando o gatilho (§33.13) não dispara, ainda devolve um snapshot
    `READY`/válido em cache se existir (para o caminho C do evaluator
    poder usar `MarketPriceAssessment` mesmo sem pesquisar de novo);
    devolve `None` quando não há nada utilizável (sem assessment, ou
    assessment `PROCESSING`/expirado/de outro worker)."""
    async with session_factory() as session:
        product = await session.get(Product, product_id)
        if product is None or product.identity_key is None:
            return None
        checkpoint_row = await session.get(
            MissionProductAlertState, (mission_id, product_id)
        )
        internal_best = await get_internal_historical_best(
            session, product_id=product_id, currency=current_currency
        )
        current_snapshot = await get_current_assessment_snapshot(
            session, product_id=product_id
        )
        realert_window = await resolve_realert_window(
            session, product_id=product_id, now=now, settings=settings
        )

    has_valid_cache = current_snapshot is not None and not needs_refresh(
        current_snapshot, current_amount=current_amount, now=now, settings=settings
    )
    signals = TriggerSignals(
        current_amount=current_amount,
        previous_amount=previous_amount,
        best_notified_amount=(
            checkpoint_row.best_notified_amount if checkpoint_row is not None else None
        ),
        internal_historical_best=(
            internal_best.amount if internal_best is not None else None
        ),
        target_amount=target_amount,
        rearmed_at=checkpoint_row.rearmed_at if checkpoint_row is not None else None,
        last_notified_at=(
            checkpoint_row.last_notified_at if checkpoint_row is not None else None
        ),
        has_valid_cached_assessment=has_valid_cache,
    )
    if not should_trigger_market_research(
        signals, settings=settings, now=now, realert_window=realert_window
    ):
        return current_snapshot if has_valid_cache else None

    return await run_market_research(
        session_factory,
        ai_manager,
        firecrawl,
        product=product,
        store_id=store_id,
        reference_price=current_amount,
        reference_currency=current_currency,
        profile=profile,
        now=now,
        settings=settings,
    )


async def _resolve_expiry_with_own_session(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    product_id: UUID,
    now: datetime,
    settings: Settings,
) -> datetime:
    async with session_factory() as session:
        return await resolve_assessment_expiry(
            session, product_id=product_id, now=now, settings=settings
        )
