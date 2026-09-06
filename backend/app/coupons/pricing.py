"""Aplicabilidade e cálculo de preço final com cupom.

Reaproveita o MESMO fluxo de oportunidade já existente (F2/F3, TASK-113)
-- este módulo só decide "qual é o melhor preço final considerando
cupons", nunca decide sozinho se isso é uma oportunidade (isso continua
sendo `should_trigger_market_research`/`evaluate_price_alerts`, nunca
duplicado aqui).

Regras de aplicabilidade e normalização de URL são decisão explícita do
usuário (2026-09-06) -- ver `docs/internal/project-context.md`, bloco
"Consumo real de cupons". Nunca inventa heurística silenciosa: quando o
dado não permite decidir com segurança, o cupom é tratado como não
aplicável/não calculável, nunca uma suposição.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from app.coupons.models import Coupon
from app.offers.models import Offer

_ACTIVE_STATUS = "active"
_KNOWN_DISCOUNT_KINDS = frozenset({"fixed_amount", "percentage"})

# Parâmetros de tracking JÁ CONHECIDOS e universalmente documentados
# (UTM da Google Analytics + click-ids de ads mais comuns) -- decisão
# explícita do usuário: remover SÓ estes, nunca a query inteira (pode
# identificar produto/variante de verdade), nunca uma lista inventada.
_TRACKING_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "igshid",
        "mc_cid",
        "mc_eid",
    }
)


def normalize_offer_url(url: str) -> str:
    """Normalização CONSERVADORA de URL para comparar `Coupon.scope_
    reference` com `Offer.url` (decisão explícita do usuário,
    2026-09-06): normaliza protocolo, host (minúsculo, sem `www.`),
    remove fragmento e barra final, remove SÓ parâmetros de tracking já
    conhecidos (`_TRACKING_QUERY_KEYS`) -- preserva qualquer outro
    parâmetro (pode identificar produto/variante de verdade). Nunca
    reordena os parâmetros restantes, nunca infere equivalência por
    nome/path parecido -- só igualdade exata depois desta normalização
    conta como match."""
    if not url:
        return ""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(kept_query)
    return urlunsplit(("https", host, path, query, ""))


def is_coupon_applicable(coupon: Coupon, offer: Offer) -> bool:
    """Só os dois casos que os dados atuais sustentam com segurança:

    - `scope_kind == "store_wide"` -- aplica a qualquer Offer da mesma
      Store (o chamador já garante isso ao consultar por `store_id`).
    - `scope_kind == "product"` -- só quando `scope_reference`
      normalizada é EXATAMENTE igual à `Offer.url` normalizada.

    `scope_kind is None` (DEC-093: "possivelmente aplicável", nunca uma
    afirmação) e `"category"` (nunca produzido de fato pelo worker hoje)
    NUNCA são aplicados automaticamente -- ficam no banco, mas fora da
    avaliação determinística."""
    if coupon.status != _ACTIVE_STATUS:
        return False
    if coupon.scope_kind == "store_wide":
        return True
    if coupon.scope_kind == "product":
        if not coupon.scope_reference or not offer.url:
            return False
        return normalize_offer_url(coupon.scope_reference) == normalize_offer_url(
            offer.url
        )
    return False


def calculate_final_price(
    reference_amount: Decimal, coupon: Coupon
) -> tuple[Decimal, Decimal] | None:
    """`(discount_amount, final_amount)` ou `None` quando o desconto não
    pode ser calculado com segurança -- `discount_kind` desconhecido/
    ausente, `discount_value` ausente, compra mínima não atingida, ou
    desconto calculado <= 0. Nunca inventa valor; só usa o que o worker
    já extraiu com confiança (`fixed_amount`/`percentage`,
    `minimum_purchase_amount`/`maximum_discount_amount` quando
    presentes)."""
    if coupon.discount_kind not in _KNOWN_DISCOUNT_KINDS or coupon.discount_value is None:
        return None
    if (
        coupon.minimum_purchase_amount is not None
        and reference_amount < coupon.minimum_purchase_amount
    ):
        return None
    if coupon.discount_kind == "fixed_amount":
        discount = coupon.discount_value
    else:  # "percentage"
        discount = reference_amount * (coupon.discount_value / Decimal(100))
    if coupon.maximum_discount_amount is not None:
        discount = min(discount, coupon.maximum_discount_amount)
    discount = min(discount, reference_amount)  # nunca preço final negativo
    if discount <= 0:
        return None
    return discount, reference_amount - discount


@dataclass(frozen=True, slots=True)
class AppliedCoupon:
    """Valor simples (mesma disciplina de `_AIOutcome`/TASK-079) -- nunca
    um `Coupon` ORM atravessando fronteira de fase/sessão. Carrega tudo
    que uma decisão (F2/F3/alerta) precisa preservar como snapshot
    (correção 2026-09-06, `AppliedCouponPayload` em `app.events.catalog`
    espelha estes mesmos campos)."""

    coupon_id: UUID
    code: str
    discount_kind: str
    original_amount: Decimal
    discount_amount: Decimal
    final_amount: Decimal
    currency: str
    raw_rule_text: str | None = None
    """`Coupon.raw_rule_text` -- texto/regra necessário para apresentar
    ao usuário, preservado aqui só para poder virar snapshot do evento
    do alerta; cada canal decide como (ou se) exibir."""


def _logical_key(coupon: Coupon) -> tuple[UUID, str]:
    """Mesma `(store_id, code)` não vazio == mesmo cupom percebido pelo
    usuário, mesmo vindo de evidências/fontes diferentes (achado da
    auditoria: o worker pode gerar mais de uma linha pro mesmo cupom
    real). Usado só para AGRUPAR candidatos já precificados -- nunca
    decide sozinho qual evidência sobrevive (ver
    `_dedupe_priced_candidates`)."""
    return (coupon.store_id, coupon.code)


def _price_candidate(
    coupon: Coupon, reference_amount: Decimal, currency: str
) -> AppliedCoupon | None:
    result = calculate_final_price(reference_amount, coupon)
    if result is None:
        return None
    discount_amount, final_amount = result
    return AppliedCoupon(
        coupon_id=coupon.id,
        code=coupon.code,
        discount_kind=coupon.discount_kind,
        original_amount=reference_amount,
        discount_amount=discount_amount,
        final_amount=final_amount,
        currency=currency,
        raw_rule_text=coupon.raw_rule_text,
    )


def _dedupe_priced_candidates(
    priced: Iterable[tuple[Coupon, AppliedCoupon]],
) -> tuple[AppliedCoupon, ...]:
    """Deduplicação para APRESENTAÇÃO -- roda só DEPOIS de já ter o preço
    final de TODAS as evidências aplicáveis (correção 2026-09-06: a
    versão anterior deduplicava por `(store_id, code)` usando `last_
    seen_at` ANTES de calcular preço, podendo descartar em silêncio uma
    evidência aplicável e mais vantajosa só por ser mais antiga).

    Mesma `(store_id, code)` não vazio: mantém a evidência com o MENOR
    `final_amount` já calculado; `last_seen_at` só desempata quando os
    dois resultados são economicamente idênticos (mesmo `final_amount`)
    -- recência nunca vence sozinha sobre um desconto melhor. `code=""`
    (ex.: clip automático sem código próprio): nenhum identificador
    estável para agrupar -- cada evidência permanece distinta, nunca
    fundida."""
    best_by_key: dict[tuple[UUID, str], tuple[Coupon, AppliedCoupon]] = {}
    standalone: list[AppliedCoupon] = []
    for coupon, applied in priced:
        if not coupon.code:
            standalone.append(applied)
            continue
        key = _logical_key(coupon)
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = (coupon, applied)
            continue
        current_coupon, current_applied = current
        is_better = applied.final_amount < current_applied.final_amount
        is_tiebreak = (
            applied.final_amount == current_applied.final_amount
            and coupon.last_seen_at > current_coupon.last_seen_at
        )
        if is_better or is_tiebreak:
            best_by_key[key] = (coupon, applied)
    return tuple(applied for _coupon, applied in best_by_key.values()) + tuple(standalone)


def best_applicable_coupon(
    offer: Offer,
    coupons: Iterable[Coupon],
    reference_amount: Decimal,
    currency: str,
) -> AppliedCoupon | None:
    """Fluxo correto (correção 2026-09-06): cupons ativos -> aplicabili-
    dade POR EVIDÊNCIA -> preço final de TODAS as evidências aplicáveis
    -> deduplicação para apresentação (pelo MELHOR resultado, nunca pela
    linha mais recente) -> melhor opção individual entre os grupos
    restantes. Nunca soma cupons entre si (decisão explícita: sem regra
    de acumulação definida, não inventar)."""
    priced: list[tuple[Coupon, AppliedCoupon]] = []
    for coupon in coupons:
        if not is_coupon_applicable(coupon, offer):
            continue
        applied = _price_candidate(coupon, reference_amount, currency)
        if applied is not None:
            priced.append((coupon, applied))
    deduped = _dedupe_priced_candidates(priced)
    if not deduped:
        return None
    return min(deduped, key=lambda applied: applied.final_amount)
