"""Melhoria material de preço (TASK-113, §33.10) -- puro, sem I/O.

`required_improvement = clamp(reference_amount * percent, min_amount,
max_amount)`. Compartilhado por `app.alerts.evaluator` (decisão de
alerta, caminho B do §33.9) e `app.market_research.service` (gatilho de
pesquisa externa, §33.13) -- um único ponto de fórmula, nunca dois
lugares calculando a mesma coisa de formas diferentes.
"""

from decimal import Decimal


def required_material_improvement(
    reference_amount: Decimal,
    *,
    percent: float,
    min_amount: float,
    max_amount: float,
) -> Decimal:
    raw = reference_amount * Decimal(str(percent))
    floor = Decimal(str(min_amount))
    ceiling = Decimal(str(max_amount))
    return min(max(raw, floor), ceiling)


def is_material_improvement(
    *,
    reference_amount: Decimal,
    current_amount: Decimal,
    percent: float,
    min_amount: float,
    max_amount: float,
) -> bool:
    if reference_amount <= 0:
        return False
    required = required_material_improvement(
        reference_amount, percent=percent, min_amount=min_amount, max_amount=max_amount
    )
    return (reference_amount - current_amount) >= required
