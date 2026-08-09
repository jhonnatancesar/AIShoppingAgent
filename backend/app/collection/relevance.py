"""Normalização de título de exibição e classificação de relevância via IA.

As duas funções usam somente o `AIProviderManager` já existente (nunca
chamam provedores diretamente) e nunca alteram preço, URL, loja,
disponibilidade ou moeda — esses dados vêm exclusivamente da coleta.
Qualquer falha de rede/provedor ou resposta fora do contrato esperado
devolve `None`; o chamador trata isso como "sem resultado válido ainda" e
usa o comportamento conservador (título bruto, sem alerta), sem quebrar a
coleta e sem persistir um resultado inválido.
"""

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from app.ai_provider import AIMessage, AIMessageRole, AIProviderManager, AIRequest
from app.users.models import UserRole

logger = logging.getLogger("app.collection.relevance")

TITLE_NORMALIZATION_PURPOSE = "normalize_offer_title"
RELEVANCE_CLASSIFICATION_PURPOSE = "classify_offer_relevance"


class OfferRelevance(StrEnum):
    """Correspondência entre uma oferta coletada e o pedido de uma missão."""

    MATCH = "match"
    POSSIBLE_MATCH = "possible_match"
    NO_MATCH = "no_match"


_TITLE_SYSTEM_PROMPT = (
    "Você recebe o título bruto de um anúncio de e-commerce, geralmente "
    "longo e cheio de palavras-chave de SEO. Devolva um título curto e "
    "legível para exibição a uma pessoa: marca, modelo e variante "
    "relevante (cor, capacidade, tamanho), sem repetir palavras-chave nem "
    "inventar informação que não esteja no título original.\n\n"
    "Responda somente com um objeto JSON válido, sem texto adicional, "
    "comentários ou blocos de código, exatamente neste formato: "
    '{"display_title": string}\n\n'
    "Nunca invente marca, modelo, cor ou qualquer atributo que não esteja "
    "claramente presente no título bruto. Nunca inclua preço, loja, "
    "disponibilidade ou qualquer dado que não seja o nome do produto."
)

_RELEVANCE_SYSTEM_PROMPT = (
    "Você recebe o que uma pessoa pediu para encontrar (busca de uma "
    "missão de compra) e o título bruto de um anúncio real encontrado. "
    "Classifique se o anúncio corresponde ao que foi pedido.\n\n"
    "Responda somente com um objeto JSON válido, sem texto adicional, "
    "comentários ou blocos de código, exatamente neste formato: "
    '{"relevance": "match" | "possible_match" | "no_match"}\n\n'
    'Use "match" somente quando o anúncio for claramente o mesmo produto '
    "pedido (mesma categoria, mesma marca e modelo quando mencionados). "
    'Use "no_match" quando o anúncio for um produto diferente, um '
    "acessório, uma peça avulsa, uma categoria diferente ou um modelo "
    'claramente diferente do pedido. Use "possible_match" somente quando '
    "houver ambiguidade genuína que impeça afirmar com segurança qualquer "
    "um dos dois extremos. Nunca classifique com base em preço — julgue "
    "somente a correspondência entre o pedido e o anúncio."
)


async def normalize_offer_title(
    manager: AIProviderManager,
    *,
    raw_title: str,
    profile: UserRole,
    requested_at: datetime | None = None,
) -> str | None:
    """Devolve um título curto para exibição, ou `None` se a IA falhar/for inválida.

    O chamador deve usar o título bruto como alternativa quando `None` for
    devolvido.
    """
    moment = requested_at or datetime.now(UTC)
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=TITLE_NORMALIZATION_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _TITLE_SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, raw_title),
        ),
        requested_at=moment,
    )
    try:
        response = await manager.generate(request)
        payload = json.loads(response.content)
        if not isinstance(payload, dict) or set(payload) != {"display_title"}:
            raise ValueError("unexpected response shape")
        title = payload["display_title"]
        if not isinstance(title, str) or not title.strip():
            raise ValueError("display_title must be a non-blank string")
        return title.strip()[:300]
    except Exception:
        logger.warning("offer_title_normalization_failed", exc_info=False)
        return None


async def classify_offer_relevance(
    manager: AIProviderManager,
    *,
    mission_search_query: str,
    raw_title: str,
    profile: UserRole,
    requested_at: datetime | None = None,
) -> OfferRelevance | None:
    """Classifica a correspondência produto-missão, ou `None` se a IA falhar.

    `None` significa "sem classificação válida ainda" — o chamador deve
    tratar a observação atual de forma conservadora (sem alertar) e tentar
    de novo na próxima coleta, sem persistir um resultado inválido.
    """
    moment = requested_at or datetime.now(UTC)
    content = json.dumps(
        {"search_query": mission_search_query, "listing_title": raw_title}
    )
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=RELEVANCE_CLASSIFICATION_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _RELEVANCE_SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, content),
        ),
        requested_at=moment,
    )
    try:
        response = await manager.generate(request)
        payload = json.loads(response.content)
        if not isinstance(payload, dict) or set(payload) != {"relevance"}:
            raise ValueError("unexpected response shape")
        return OfferRelevance(payload["relevance"])
    except Exception:
        logger.warning("offer_relevance_classification_failed", exc_info=False)
        return None
