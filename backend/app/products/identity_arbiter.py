"""Árbitro de IA para a zona cinzenta de identidade de produto
(checkpoint 3, 2026-09-13) -- camada GLOBAL, sem conhecimento de loja
ou categoria: qualquer par de listagens (qualquer categoria, qualquer
loja atual ou futura) que o motor determinístico (`app.products.
identity`) e o reaproveitamento por tokens (`app.products.
identity_learning._find_reusable_candidate_by_tokens`) não conseguirem
decidir com segurança passa por aqui.

A pergunta feita à IA é sempre a mesma, deliberadamente genérica:
"esses dois listings representam o MESMO produto físico/variante
comercial canônica?" -- nunca uma pergunta específica de categoria.
Resposta estritamente estruturada em três estados (nunca um quarto
inventado): `SAME_PRODUCT`, `DIFFERENT_PRODUCT`, `INCONCLUSIVE`.
`INCONCLUSIVE` -- e qualquer falha de rede/parsing -- nunca funde nem
separa (fail-closed, mesma disciplina do resto do módulo).

Sempre `profile=UserRole.USER` na chamada à IA, INDEPENDENTE do
profile de quem chamou esta função -- é o único jeito de garantir
`cost_policy="free_only"` em `app.ai_provider.cesar_core` (`ai_profile
== "user"`), nunca permitindo fallback pago para o árbitro (seção 11
do checkpoint 3)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from app.ai_provider import AIMessage, AIMessageRole, AIProviderManager, AIRequest
from app.users.models import UserRole

logger = logging.getLogger("app.products.identity_arbiter")

ARBITRATE_IDENTITY_PURPOSE = "arbitrate_product_identity"


class ArbiterVerdict(StrEnum):
    SAME_PRODUCT = "SAME_PRODUCT"
    DIFFERENT_PRODUCT = "DIFFERENT_PRODUCT"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class ListingEvidence:
    """Evidências tipadas de UMA listagem/anúncio -- mesmo contrato
    genérico independente de loja/categoria (checkpoint 3, seção 3).
    Campos ausentes são `None`/vazio; ausência nunca é enviada à IA
    como se fosse uma contradição, só como "sem informação"."""

    manufacturer: str
    family: str
    model_name: str
    variant: str | None
    store_sku: str | None
    manufacturer_part_number: str | None
    attributes: dict[str, str]

    def as_prompt_dict(self) -> dict[str, str | None]:
        payload: dict[str, str | None] = {
            "manufacturer": self.manufacturer,
            "family": self.family,
            "model_name": self.model_name,
            "variant": self.variant,
            "store_sku": self.store_sku,
            "manufacturer_part_number": self.manufacturer_part_number,
        }
        payload.update(self.attributes)
        return payload


_SYSTEM_PROMPT = (
    "Você é um árbitro de identidade de produto para um comparador de "
    "preços entre lojas diferentes. Você recebe as evidências "
    "ESTRUTURADAS de DUAS listagens/anúncios (de lojas possivelmente "
    "diferentes, de QUALQUER categoria de produto -- eletrônicos, "
    "eletrodomésticos, móveis, o que for) e decide se representam o "
    "MESMO produto físico / mesma variante comercial canônica.\n\n"
    "Responda somente com um objeto JSON válido, sem texto adicional, "
    "comentários ou blocos de código, exatamente neste formato: "
    '{"verdict": "SAME_PRODUCT" | "DIFFERENT_PRODUCT" | "INCONCLUSIVE"}'
    "\n\n"
    "Regras de julgamento:\n"
    "- `store_sku` é o código que cada LOJA usa para o próprio "
    "anúncio -- nunca compare `store_sku` entre as duas listagens "
    "como se fosse um identificador do produto; lojas diferentes "
    "quase sempre têm `store_sku` diferentes para o MESMO produto.\n"
    "- `manufacturer_part_number` é o código atribuído pelo "
    "FABRICANTE -- se as duas listagens tiverem o MESMO "
    "`manufacturer_part_number`, isso é evidência muito forte de "
    "SAME_PRODUCT, mesmo que outros campos estejam escritos "
    "diferente.\n"
    "- Um campo ausente (null) em uma listagem NUNCA é contradição -- "
    "é apenas falta de informação daquela loja. Só considere "
    "DIFFERENT_PRODUCT quando houver um valor EXPLÍCITO e "
    "inequivocamente incompatível entre as duas listagens (ex.: "
    "modelos diferentes, capacidades/memória/tamanho diferentes).\n"
    "- Diferenças de grafia, pontuação, idioma ou ordem das palavras "
    'para o MESMO valor não são contradição (ex.: "mATX" e '
    '"M-ATX" são o mesmo formato; "16GB" e "16 GB" são a mesma '
    "capacidade).\n"
    "- Quando a evidência disponível não permitir decidir com "
    "segurança, responda INCONCLUSIVE -- nunca invente uma decisão."
)


def _build_user_message(listing_a: ListingEvidence, listing_b: ListingEvidence) -> str:
    payload = {
        "listing_a": listing_a.as_prompt_dict(),
        "listing_b": listing_b.as_prompt_dict(),
    }
    return json.dumps(payload, ensure_ascii=False)


async def arbitrate_same_product(
    manager: AIProviderManager,
    *,
    listing_a: ListingEvidence,
    listing_b: ListingEvidence,
    requested_at: datetime | None = None,
) -> ArbiterVerdict:
    """Chama o árbitro de IA (via `AIProviderManager` -> César Core,
    SEMPRE `profile=UserRole.USER` -> `cost_policy=free_only`) para
    decidir a zona cinzenta entre duas listagens. Qualquer falha de
    rede/provedor ou resposta fora do contrato -> `INCONCLUSIVE`
    (fail-closed, nunca funde nem separa por adivinhação). Roda fora
    de qualquer transação de banco aberta (é I/O de rede) -- mesma
    disciplina de `extract_product_identity_via_ai`; quem chama deve
    ter feito `await session.rollback()` antes, se houver uma leitura
    de banco em aberto."""
    moment = requested_at or datetime.now(UTC)
    request = AIRequest(
        request_id=uuid4(),
        profile=UserRole.USER,
        purpose=ARBITRATE_IDENTITY_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, _build_user_message(listing_a, listing_b)),
        ),
        requested_at=moment,
    )
    try:
        response = await manager.generate(request)
        payload = json.loads(_strip_markdown_code_fence(response.content))
        if not isinstance(payload, dict) or set(payload) != {"verdict"}:
            raise ValueError("unexpected response shape")
        verdict = payload["verdict"]
        if verdict not in {member.value for member in ArbiterVerdict}:
            raise ValueError("unexpected verdict value")
        return ArbiterVerdict(verdict)
    except Exception:
        logger.warning("product_identity_arbiter_failed", exc_info=False)
        return ArbiterVerdict.INCONCLUSIVE


def _strip_markdown_code_fence(content: str) -> str:
    """Mesma tolerância de `app.products.identity_ai` -- o modelo
    gratuito às vezes envolve o JSON válido numa cerca de código
    Markdown apesar da instrução em contrário."""
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[: -len("```")]
        stripped = stripped.strip()
    return stripped
