"""TASK-136 (passo 2): funil antes da IA -- ranking por popularidade e escolha dos
candidatos que realmente vão para a IA.

Só lógica pura (sem banco, sem rede, sem IA). Recebe as ofertas já normalizadas de UMA
rodada (todas as lojas da missão juntas, depois que todas rodaram) e devolve:

- `pool`: até `pool_size` (10) ofertas mais populares -- quem é "recomendado";
- `chosen`: dentre o pool, as `ai_count` (2) MAIS BARATAS -- as únicas que vão para a IA.

O resto continua registrado com preço e histórico; só não passa pela IA.

Popularidade (decisão do usuário, 2026-10-01): o site NÃO avalia nada -- nota (estrelas),
quantidade de avaliações e vendas são dos usuários nas lojas e aqui só são LIDAS.

    pontos = nota x quantidade de avaliações   (a nota só existe junto com a contagem)

Assim 3 avaliações 5★ (15 pontos) nunca ganham de 160 avaliações 4,9 (784 pontos), e uma
nota 4,0 com 1.000 avaliações (4.000) perde para 4,9 com 900 (4.410). Vendas, quando a
loja informa, **desempatam** (mais vendas primeiro) e colocam quem tem venda acima de quem
não tem sinal nenhum. Oferta sem nenhum sinal fica atrás de todas que têm algum.

A regra de pontos está TODA em `popularity_points`: é o único lugar a mudar se o critério
mudar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

POOL_SIZE = 10
AI_COUNT = 2
DOMINANCE_FACTOR = 3
"""Das 2 mais baratas do pool, se uma tem pelo menos 3x os pontos da outra (ou a outra não
tem avaliação nenhuma), só ela vai para a IA: as avaliações apontam claramente um só."""

_CONDITION_RANK = {"new": 0, "refurbished": 1, "unknown": 2, "used": 3}


@dataclass(frozen=True, slots=True)
class FunnelSelection:
    pool: tuple[Any, ...]
    """Até `pool_size` ofertas mais populares, da mais para a menos popular."""
    chosen: tuple[Any, ...]
    """As `ai_count` mais baratas do pool: as únicas que vão para a IA."""


def popularity_points(item: Any) -> Decimal:
    """Pontos de popularidade da oferta (maior = mais popular); `0` sem sinal."""
    rating = getattr(item, "rating_average", None)
    reviews = getattr(item, "review_count", None)
    if rating is None or not reviews:
        return Decimal(0)
    return Decimal(rating) * Decimal(reviews)


def _sales_count(item: Any) -> int:
    sales = getattr(item, "sales", None)
    if not sales:
        return 0
    return int(sales[0])


def _identity(item: Any) -> str:
    raw = item.raw_offer
    return f"{raw.source_code}:{raw.external_id or raw.url}"


def _condition_rank(item: Any) -> int:
    condition = getattr(item, "condition", None)
    return _CONDITION_RANK.get(getattr(condition, "value", str(condition)), 2)


def _eligible(item: Any) -> bool:
    availability = getattr(getattr(item, "availability", None), "value", None)
    return availability != "unavailable"


def select_for_ai(
    items: tuple[Any, ...] | list[Any],
    *,
    pool_size: int = POOL_SIZE,
    ai_count: int = AI_COUNT,
) -> FunnelSelection:
    """Escolhe o pool (mais populares) e, dentro dele, os mais baratos para a IA.

    Ofertas indisponíveis nunca entram no pool nem são escolhidas (continuam
    registradas, só não competem). A ordem é determinística: empates seguem preço e depois
    a identidade da oferta."""
    if pool_size < 1 or ai_count < 1:
        raise ValueError("pool_size and ai_count must be positive")
    eligible = [item for item in items if _eligible(item)]
    ranked = sorted(
        eligible,
        key=lambda item: (
            -popularity_points(item),
            -_sales_count(item),
            item.total_amount,
            _identity(item),
        ),
    )
    pool = tuple(ranked[:pool_size])
    cheapest = sorted(
        pool,
        key=lambda item: (
            _condition_rank(item),
            item.total_amount,
            -popularity_points(item),
            _identity(item),
        ),
    )
    chosen = tuple(cheapest[:ai_count])
    if len(chosen) == 2:
        better, other = sorted(chosen, key=popularity_points, reverse=True)
        if popularity_points(better) > 0 and popularity_points(better) >= (
            DOMINANCE_FACTOR * popularity_points(other)
        ):
            chosen = (better,)
    return FunnelSelection(pool=pool, chosen=chosen)
