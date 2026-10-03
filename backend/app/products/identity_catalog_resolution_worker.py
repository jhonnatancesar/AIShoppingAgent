"""TASK-137 (passo C) -- a IA desempata pedidos em dúvida entre candidatos do catálogo.

Roda no worker, FORA de qualquer transação. Para cada pedido `pending`/`ai_failed`
devido (`catalog_request_resolutions`), manda à IA o pedido, o que ela já entendeu
dele (`search_query`/`model`) e até 5 candidatos DO BANCO; a resposta é fechada
(`{"choice": "A".."E" | null}`): a IA nunca inventa produto, só escolhe da lista.

A escolha ainda passa por conferência determinística (`choice_is_coherent`: o código
de modelo e a marca do pedido precisam estar no candidato). Escolhida e coerente ->
`resolved` e as missões sem item com o mesmo pedido são religadas (passam a usar a
coleta compartilhada e o funil). "Nenhum" ou incoerente -> `none`. Falha de IA ->
`ai_failed` com espera crescente e o MESMO disjuntor compartilhado da identidade
(TASK-133); falha de conteúdo repetida vira `none`. O mesmo pedido nunca gasta IA
duas vezes depois de decidido.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider import AIMessage, AIMessageRole, AIProviderManager, AIRequest
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionMonitoringItem,
    MissionStatus,
)
from app.missions.monitoring import (
    _criteria_identity_text,
    reconcile_mission_monitoring_item_async,
)
from app.products.identity_ai import (
    INFRA_FAILURE_KINDS,
    AIExtractionFailure,
    _strip_markdown_code_fence,
    classify_ai_failure,
)
from app.products.identity_ai_failure import (
    ai_gate,
    breaker_open_until,
    breaker_trip,
    retry_delay,
)
from app.products.identity_catalog import CatalogEntrySnapshot, load_catalog
from app.products.identity_catalog_models import CatalogRequestResolution
from app.products.identity_catalog_request import choice_is_coherent, request_key
from app.users.models import UserRole

logger = logging.getLogger("app.products.identity_catalog_resolution_worker")

CHOOSE_CATALOG_CANDIDATE_PURPOSE = "choose_catalog_candidate"
MAX_CONTENT_ATTEMPTS = 5
_LABELS = "ABCDE"

_SYSTEM_PROMPT = (
    "Você escolhe qual produto de uma LISTA é exatamente o que o usuário pediu. "
    'Responda SOMENTE um JSON no formato {"choice": "A"} com a letra do produto, '
    'ou {"choice": null} se nenhum for claramente o MESMO produto. '
    "Variações no nome (por exemplo 'A', 'PRO-A', 'WIFI', 'MAX', 'PLUS', 'AC', 'II', "
    "outra memória DDR) são produtos DIFERENTES: não escolha um deles por aproximação. "
    "Na dúvida, responda null. Nunca invente um produto fora da lista nem escreva "
    "texto fora do JSON."
)


@dataclass(frozen=True, slots=True)
class AIChoice:
    label: str | None
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class ResolutionSummary:
    examined: int = 0
    resolved: int = 0
    none: int = 0
    failed: int = 0
    relinked_missions: int = 0
    breaker_open: bool = False


def _build_user_message(
    request_text: str, understood: dict, candidates: list[dict]
) -> str:
    return json.dumps(
        {
            "pedido_do_usuario": request_text,
            "o_que_a_ia_entendeu": {
                key: value for key, value in understood.items() if value
            },
            "produtos": [
                {
                    "letra": item["label"],
                    "marca": item["brand"],
                    "nome": item["name"],
                    **(
                        {"atributos": item["attributes"]}
                        if item.get("attributes")
                        else {}
                    ),
                }
                for item in candidates
            ],
        },
        ensure_ascii=False,
    )


def parse_choice(content: str, labels: set[str]) -> str | None:
    """Letra escolhida, ou `None` (a IA disse "nenhum"). Qualquer outra forma de
    resposta levanta `ValueError` (vira falha `invalid_response`)."""
    payload = json.loads(_strip_markdown_code_fence(content))
    if not isinstance(payload, dict) or set(payload) != {"choice"}:
        raise ValueError("unexpected response shape")
    choice = payload["choice"]
    if choice is None:
        return None
    if not isinstance(choice, str) or choice.strip().upper() not in labels:
        raise ValueError("choice outside the candidate list")
    return choice.strip().upper()


async def choose_candidate_via_ai(
    manager: AIProviderManager,
    *,
    request_text: str,
    understood: dict,
    candidates: list[dict],
    profile: UserRole,
    requested_at: datetime | None = None,
) -> AIChoice | AIExtractionFailure:
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=CHOOSE_CATALOG_CANDIDATE_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _SYSTEM_PROMPT),
            AIMessage(
                AIMessageRole.USER,
                _build_user_message(request_text, understood, candidates),
            ),
        ),
        requested_at=requested_at or datetime.now(UTC),
    )
    try:
        response = await manager.generate(request)
    except Exception as exc:
        logger.warning(
            "catalog_request_ai_failed",
            extra={"stage": "generate", "error": type(exc).__name__},
        )
        return classify_ai_failure(exc)
    try:
        label = parse_choice(response.content, {item["label"] for item in candidates})
    except Exception:
        logger.warning("catalog_request_ai_failed", extra={"stage": "parse"})
        return AIExtractionFailure("invalid_response")
    return AIChoice(label, response.provider, response.model)


def _prompt_candidates(
    stored: list[dict], by_id: dict[UUID, CatalogEntrySnapshot]
) -> list[dict]:
    items = []
    for item in stored:
        entry = by_id.get(UUID(item["entry_id"]))
        if entry is None:
            continue
        items.append(
            {
                **item,
                "attributes": {
                    key: entry.attributes[key]
                    for key in ("socket", "chipset", "form_factor", "memory_type")
                    if key in entry.attributes
                },
            }
        )
    return items


async def _apply(
    session_factory: async_sessionmaker[AsyncSession],
    row_id: UUID,
    *,
    status: str,
    now: datetime,
    chosen: UUID | None = None,
    provider: str | None = None,
    error_kind: str | None = None,
    next_retry_at: datetime | None = None,
    count_attempt: bool = False,
) -> None:
    async with session_factory() as session, session.begin():
        row = await session.get(CatalogRequestResolution, row_id, with_for_update=True)
        if row is None:
            return
        row.status = status
        row.chosen_entry_id = chosen
        row.ai_error_kind = error_kind
        row.next_retry_at = next_retry_at
        if provider is not None:
            row.ai_provider = provider
        if count_attempt:
            row.attempts += 1
        if status in {"resolved", "none"}:
            row.decided_at = now
        row.updated_at = now


async def _relink_missions(
    session_factory: async_sessionmaker[AsyncSession], key: str, now: datetime
) -> int:
    """Religa as missões ainda SEM item cujo pedido tem esta chave: a identidade
    escolhida vem do cache gravado (`lookup_or_register` devolve na hora)."""
    relinked = 0
    async with session_factory() as session, session.begin():
        rows = (
            await session.execute(
                select(Mission, MissionCriteria)
                .join(MissionCriteria, MissionCriteria.mission_id == Mission.id)
                .outerjoin(
                    MissionMonitoringItem,
                    MissionMonitoringItem.mission_id == Mission.id,
                )
                .where(
                    MissionMonitoringItem.mission_id.is_(None),
                    Mission.status.in_((MissionStatus.ACTIVE, MissionStatus.PAUSED)),
                )
            )
        ).all()
        for mission, criteria in rows:
            if request_key(_criteria_identity_text(criteria)) != key:
                continue
            item = await reconcile_mission_monitoring_item_async(
                session,
                mission_id=mission.id,
                criteria=criteria,
                mission_is_active=mission.status is MissionStatus.ACTIVE,
                now=now,
            )
            relinked += 1 if item is not None else 0
    return relinked


async def resolve_pending_requests(
    session_factory: async_sessionmaker[AsyncSession],
    manager: AIProviderManager,
    profile: UserRole,
    *,
    now: datetime | None = None,
    limit: int = 5,
) -> ResolutionSummary:
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        if await breaker_open_until(session, moment) is not None:
            return ResolutionSummary(breaker_open=True)
        rows = (
            await session.scalars(
                select(CatalogRequestResolution)
                .where(
                    CatalogRequestResolution.status.in_(("pending", "ai_failed")),
                    or_(
                        CatalogRequestResolution.next_retry_at.is_(None),
                        CatalogRequestResolution.next_retry_at <= moment,
                    ),
                )
                .order_by(CatalogRequestResolution.created_at)
                .limit(limit)
            )
        ).all()
        due = [
            (
                row.id,
                row.request_key,
                row.request_text,
                row.understood,
                row.candidates,
                row.attempts,
            )
            for row in rows
        ]
        catalog = await load_catalog(session) if due else ()
    by_id = {entry.entry_id: entry for entry in catalog if entry.entry_id is not None}

    resolved = none = failed = relinked = 0
    for row_id, key, request_text, understood, stored, attempts in due:
        candidates = _prompt_candidates(stored, by_id)
        if not candidates:
            await _apply(session_factory, row_id, status="none", now=moment)
            none += 1
            continue
        async with ai_gate():
            result = await choose_candidate_via_ai(
                manager,
                request_text=request_text,
                understood=understood,
                candidates=candidates,
                profile=profile,
                requested_at=moment,
            )
        if isinstance(result, AIExtractionFailure):
            failed += 1
            total = attempts + 1
            terminal = (
                result.kind not in INFRA_FAILURE_KINDS and total >= MAX_CONTENT_ATTEMPTS
            )
            await _apply(
                session_factory,
                row_id,
                status="none" if terminal else "ai_failed",
                now=moment,
                error_kind=result.kind,
                next_retry_at=None
                if terminal
                else moment + retry_delay(result.kind, total),
                count_attempt=True,
            )
            async with session_factory() as session:
                await breaker_trip(session, result, moment)
            if result.kind in INFRA_FAILURE_KINDS:
                break  # a IA está fora: não insiste nos demais pedidos
            continue
        if result.label is None:
            await _apply(
                session_factory,
                row_id,
                status="none",
                now=moment,
                provider=result.provider,
            )
            none += 1
            continue
        chosen = next(item for item in candidates if item["label"] == result.label)
        entry = by_id[UUID(chosen["entry_id"])]
        texts = [request_text, understood.get("search_query"), understood.get("model")]
        if not choice_is_coherent([t for t in texts if t], entry, chosen["name"]):
            await _apply(
                session_factory,
                row_id,
                status="none",
                now=moment,
                provider=result.provider,
                error_kind="incoherent_choice",
            )
            none += 1
            continue
        await _apply(
            session_factory,
            row_id,
            status="resolved",
            now=moment,
            chosen=entry.entry_id,
            provider=result.provider,
        )
        resolved += 1
        relinked += await _relink_missions(session_factory, key, moment)
    return ResolutionSummary(
        examined=len(due),
        resolved=resolved,
        none=none,
        failed=failed,
        relinked_missions=relinked,
    )


__all__ = [
    "CHOOSE_CATALOG_CANDIDATE_PURPOSE",
    "AIChoice",
    "ResolutionSummary",
    "choose_candidate_via_ai",
    "parse_choice",
    "resolve_pending_requests",
]
