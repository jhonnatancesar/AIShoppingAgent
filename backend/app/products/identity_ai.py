"""Extração de identidade de produto assistida por IA (rodada de
2026-09-12) -- generaliza o Product Identity Engine (`app.products.
identity`) para além dos 5 extratores regex hardcoded (smartphone/CPU/
GPU) sem exigir código novo por família/categoria.

Mesma disciplina de `app.collection.relevance` (única outra fronteira
de IA "estruturante" deste projeto): usa SOMENTE `AIProviderManager`
(nunca um provider direto), pede um JSON estrito, e qualquer resposta
fora do contrato ou sem grounding devolve `None` -- nunca um fallback
silencioso que inventa identidade.

**A IA nunca decide a fórmula de identidade.** Ela só preenche campos
estruturados (categoria/marca/família/modelo/variante/atributos); a
chave determinística (`family_key`/`identity_key`) continua sendo
código puro (`app.products.identity.build_resolved_variant_from_fields`,
as MESMAS funções que os extratores regex usam). E toda extração passa
por **grounding determinístico**: cada token de marca/família/modelo
que a IA devolveu precisa aparecer literalmente no título original
(normalizado) -- a IA não pode "inventar" uma marca ou família que não
está no texto. Grounding é condição NECESSÁRIA para `status=
"approved"`, nunca suficiente sozinho (ver `evaluate_ai_identity_extraction`).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from app.ai_provider import AIMessage, AIMessageRole, AIProviderManager, AIRequest
from app.products.identity import (
    ResolvedProductVariant,
    build_resolved_variant_from_fields,
    normalize_for_grounding,
)
from app.users.models import UserRole

logger = logging.getLogger("app.products.identity_ai")

EXTRACT_IDENTITY_PURPOSE = "extract_product_identity"
BATCH_EXTRACT_IDENTITY_PURPOSE = "extract_product_identity_batch"

_FIELD_INSTRUCTIONS = (
    "category: categoria geral do produto em uma palavra (ex.: "
    '"monitor", "teclado", "gpu", "smartphone").\n'
    'brand: fabricante (ex.: "LG", "Samsung", "Logitech").\n'
    'family: linha/série do produto dentro da marca (ex.: "UltraGear", '
    '"Galaxy Tab", "MX Keys").\n'
    'model: código ou número do modelo específico (ex.: "27GP850", '
    '"S9", "K380").\n'
    "variant: variação explícita do MESMO modelo (cor, tamanho, edição) "
    "quando existir no título, ou null quando não houver nenhuma "
    "variação mencionada.\n"
    "store_sku: código/SKU que a PRÓPRIA loja usa para identificar este "
    'anúncio (ex.: um campo chamado "SKU", "Código" ou "Referência" '
    "na página), quando aparecer explicitamente no título -- null quando "
    "não houver. Isto é específico da loja, NUNCA um identificador do "
    "fabricante.\n"
    "manufacturer_part_number: código/part number atribuído pelo "
    'FABRICANTE do produto (ex.: um campo chamado "Part Number", '
    '"Número de peça", "MPN" ou "P/N" na página), quando aparecer '
    "explicitamente no título -- null quando não houver. Nunca confunda "
    "com store_sku: o mesmo manufacturer_part_number pode aparecer em "
    "lojas diferentes vendendo o mesmo produto; o store_sku, não.\n"
    "attributes: pares chave-valor de especificações técnicas "
    "EXPLICITAMENTE mencionadas no título (ex.: capacidade de "
    "armazenamento, taxa de atualização, tipo de memória, tamanho de "
    "tela) -- nunca inventadas nem inferidas de conhecimento externo "
    "sobre o produto, e nunca incluindo store_sku/manufacturer_part_"
    "number aqui (eles têm campos próprios). Cada VALOR deve ser "
    "copiado como aparece no título, incluindo a unidade/sufixo colado "
    'ao número (ex.: "165Hz", não "165"; "512GB", não "512"; "DDR5", '
    'não "5") -- nunca separe o número da unidade.\n\n'
    "Nunca invente marca, família, modelo, variante, SKU, part number "
    "ou atributo que não esteja claramente presente no título. Cada um "
    "dos campos brand/family/model/variant/store_sku/manufacturer_"
    "part_number e cada valor de attributes deve ser uma palavra ou "
    "frase que aparece, ainda que com grafia/acentuação diferente, no "
    "próprio título recebido -- nunca conhecimento externo sobre o "
    "produto. Preste atenção a SUFIXOS que mudam o modelo (ex.: "
    '"B650" e "B650E" são placas-mãe DIFERENTES -- nunca omita a letra '
    "final se ela estiver no título) e a variantes que mudam o produto "
    'mesmo com o mesmo modelo (ex.: "DDR4" vs "DDR5" são versões de '
    "memória diferentes da mesma placa-mãe -- sempre capture isso como "
    "atributo quando aparecer). Se o título não permitir identificar "
    "marca, família E modelo com segurança, devolva strings vazias "
    '("") nesses campos em vez de adivinhar.'
)

_SYSTEM_PROMPT = (
    "Você recebe o título bruto de um anúncio de e-commerce e precisa "
    "estruturar a identidade do produto para permitir comparação de preço "
    "entre lojas diferentes que vendem o MESMO item -- de QUALQUER "
    "categoria (eletrônicos, eletrodomésticos, móveis, vestuário etc.), "
    "nunca só hardware de PC.\n\n"
    "Responda somente com um objeto JSON válido, sem texto adicional, "
    "comentários ou blocos de código, exatamente neste formato: "
    '{"category": string, "brand": string, "family": string, '
    '"model": string, "variant": string | null, '
    '"store_sku": string | null, "manufacturer_part_number": string | null, '
    '"attributes": {string: string}}\n\n'
) + _FIELD_INSTRUCTIONS

_BATCH_SYSTEM_PROMPT = (
    "Você recebe uma LISTA de títulos brutos de anúncios de e-commerce de "
    'lojas diferentes, cada um identificado por um "id" numérico, e '
    "precisa estruturar a identidade de CADA produto da lista "
    "independentemente -- de QUALQUER categoria (eletrônicos, "
    "eletrodomésticos, móveis, vestuário etc.), nunca só hardware de PC. "
    'IMPORTANTE: cada item deve usar SOMENTE o título do MESMO "id" -- '
    "nunca misture informação entre títulos de ids diferentes da lista, "
    "mesmo que pareçam descrever o mesmo produto.\n\n"
    'Entrada: um array JSON de objetos {"id": number, "title": string}.\n\n'
    "Responda somente com um array JSON válido, com exatamente um objeto "
    'por "id" recebido (mesma quantidade de itens da entrada), sem texto '
    "adicional, comentários ou blocos de código, exatamente neste formato "
    'por item: {"id": number, "category": string, "brand": string, '
    '"family": string, "model": string, "variant": string | null, '
    '"store_sku": string | null, "manufacturer_part_number": string | null, '
    '"attributes": {string: string}}\n\n'
) + _FIELD_INSTRUCTIONS


def _strip_markdown_code_fence(content: str) -> str:
    """Remove um bloco de código Markdown (```` ```json ... ``` ````)
    ao redor da resposta -- achado real (2026-09-13, título de placa-mãe
    Terabyte): o prompt já pede JSON puro sem bloco de código, mas o
    modelo gratuito atual (`mimo-v2.5-free`) às vezes ignora essa
    instrução e envolve o JSON válido em uma cerca de código. Sem isto,
    `json.loads` falhava e uma extração correta era descartada como
    "falha de IA" (nunca finge sucesso quando a cerca não é a única
    coisa ao redor do JSON -- só remove a cerca nas bordas, o parse
    subsequente ainda reprova qualquer conteúdo realmente inválido)."""
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[: -len("```")]
        stripped = stripped.strip()
    return stripped


@dataclass(frozen=True, slots=True)
class AIIdentityExtraction:
    """Resultado bruto da IA, ANTES de qualquer validação de grounding
    -- nunca usado diretamente para resolver identidade; sempre passa
    por `evaluate_ai_identity_extraction` primeiro."""

    category: str
    brand: str
    family: str
    model: str
    variant: str | None
    store_sku: str | None
    manufacturer_part_number: str | None
    attributes: dict[str, str]
    ai_provider: str
    ai_model: str


@dataclass(frozen=True, slots=True)
class EvaluatedIdentityExtraction:
    """Extração já avaliada deterministicamente -- pronta para
    persistir como `ProductIdentityCandidate`.

    `resolved` é preenchido sempre que os campos permitem calcular uma
    identidade (mesmo sem grounding) -- é o que fica registrado na
    fila de revisão para um humano ver o que a IA propôs. **Só use
    `resolved` para resolver identidade em produção quando `status ==
    "approved"`** -- com `"pending_review"`, `resolved` existe apenas
    para preencher o candidato persistido, nunca para uso automático."""

    resolved: ResolvedProductVariant | None
    grounded: bool
    status: str
    """`"approved"` | `"pending_review"` -- nunca `"rejected"` aqui
    (rejeição é sempre uma decisão humana posterior, nunca automática
    a partir da própria extração)."""
    raw: AIIdentityExtraction


def normalized_title_hash(raw_title: str) -> str:
    """Chave de reuso determinística -- mesmo título (de qualquer loja)
    sempre produz o mesmo hash, permitindo consultar um candidato já
    decidido sem nova chamada de IA. `sha256` do título já normalizado
    (mesma normalização dos extratores regex, acento/caixa/espaço)."""
    normalized = normalize_for_grounding(raw_title)
    return sha256(normalized.encode("utf-8")).hexdigest()


_TOKEN_EDGE_PUNCTUATION = ",.;:!?()[]{}\"'"


def _strip_token_edges(token: str) -> str:
    """Remove pontuação de LISTA (vírgula, ponto, parênteses etc.) presa
    nas bordas de um token -- nunca do meio (preserva "/" em modelos como
    "KF432C16BB12A/16"). Achado real (2026-09-13, título de memória
    Terabyte real): títulos de loja separam especificações por vírgula
    ("... RGB, 16GB, 3200Mhz, Preto, KF432C16BB12A/16"); `_normalized`
    só colapsa espaços, então o split por espaço produzia tokens como
    "16GB," -- nunca igual ao valor limpo "16GB" que a IA devolve,
    reprovando grounding correto por um detalhe de pontuação, não por
    divergência real de conteúdo."""
    return token.strip(_TOKEN_EDGE_PUNCTUATION)


_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def _compact_alnum(text: str) -> str:
    """Forma compacta (só `[A-Z0-9]`, sem separador nenhum) de um valor
    -- usada só como fallback de comparação (ver `tokens_present`) para
    reconhecer o MESMO código/abreviação escrito com pontuação diferente
    entre duas lojas (achado real 2026-09-13: mesma placa-mãe com
    "mATX" numa loja e "M-ATX" noutra; mesmo kit de memória com part
    number "KF432C16BB12A/16" numa loja e "KF432C16BB12A-16" noutra)."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_ALNUM.sub("", plain.upper())


def _compact_run_matches(title_tokens: tuple[str, ...], compact_value: str) -> bool:
    """`True` só quando a forma compacta de `compact_value` bate EXATAMENTE
    com a forma compacta de uma sequência CONTÍGUA de tokens do título --
    nunca substring livre dentro do título inteiro (reabriria o
    falso-positivo de prefixo já corrigido em 2026-09-12, ex. "B650" vs
    "B650E": a busca aborta assim que o acumulado ultrapassa o
    comprimento do valor, então "B650" nunca bate contra um acumulado
    que já virou "B650E")."""
    if not compact_value:
        return False
    for start in range(len(title_tokens)):
        accumulated = ""
        for token in title_tokens[start:]:
            accumulated += _compact_alnum(token)
            if len(accumulated) > len(compact_value):
                break
            if accumulated == compact_value:
                return True
    return False


def values_match_ignoring_punctuation(a: str | None, b: str | None) -> bool:
    """`True` só quando os dois valores são o MESMO código/identificador
    escrito com pontuação/separador diferente (mesmo fallback de
    `tokens_present`, exposto aqui para comparar dois valores
    ESTRUTURADOS diretamente -- ex.: `manufacturer_part_number` de duas
    lojas -- sem depender de nenhum título bruto). Vazio/`None` de
    qualquer lado nunca é considerado igual (ausência não é sinal de
    igualdade nem de contradição, ver `app.products.identity_learning`)."""
    if not a or not b:
        return False
    return _compact_alnum(a) == _compact_alnum(b)


def tokens_present(raw_title_normalized: str, value: str) -> bool:
    """Todo TOKEN de `value` (normalizado) precisa aparecer como PALAVRA
    COMPLETA em `raw_title_normalized` -- usado tanto para grounding da
    extração da IA (`_is_grounded`) quanto para reconhecer, mais tarde,
    um título DIFERENTE que descreve a MESMA família/modelo/variante já
    aprendida (`app.products.identity_learning.
    _find_reusable_candidate_by_tokens`). Vazio nunca é considerado
    presente (campo que deveria distinguir algo, mas está em branco,
    não prova nada).

    Correção real (2026-09-12, achado ao escrever o teste de placa-mãe
    B650/B650E): comparar por SUBSTRING livre (`token in texto`) dava
    falso-positivo quando um token era PREFIXO de outro token do título
    -- "B650" é substring de "B650E", então o modelo errado "B650"
    passava grounding contra um título que na verdade dizia "B650E".
    Comparação agora é contra o CONJUNTO DE TOKENS do título (split por
    espaço), nunca substring arbitrária -- "B650" só bate contra o
    token exato "B650" no título, nunca contra "B650E".

    Fallback real (2026-09-13, achado ao validar aprendizado entre
    lojas com dados reais Terabyte/Pichau): duas lojas descrevem o
    MESMO valor com pontuação/separador diferente ao redor do mesmo
    código -- "mATX" vs "M-ATX", "KF432C16BB12A/16" vs
    "KF432C16BB12A-16". A normalização já usada aqui trata "-"/"_"
    como espaço mas nunca colapsa "/" nem junta de volta um valor que
    o OUTRO lado escreveu sem separador nenhum; sem este fallback,
    formatação -- não conteúdo -- decidia "produto diferente". Só
    aceita quando a forma compacta do valor bate EXATAMENTE com uma
    sequência contígua de tokens do título (`_compact_run_matches`),
    nunca substring livre -- preserva a mesma proteção contra
    B650/B650E."""
    if not value.strip():
        return False
    title_tokens = tuple(
        cleaned
        for token in raw_title_normalized.split()
        if (cleaned := _strip_token_edges(token))
    )
    title_token_set = frozenset(title_tokens)
    if all(
        _strip_token_edges(token) in title_token_set
        for token in normalize_for_grounding(value).split()
    ):
        return True
    return _compact_run_matches(title_tokens, _compact_alnum(value))


def _is_grounded(
    raw_title_normalized: str,
    *,
    brand: str,
    family: str,
    model: str,
    variant: str | None,
    store_sku: str | None,
    manufacturer_part_number: str | None,
    attributes: dict[str, str],
) -> bool:
    """Marca/família/modelo devolvidos pela IA precisam aparecer
    literalmente no título original (mesma normalização) -- token a
    token, para que uma frase como "linha UltraGear" ainda seja
    reconhecida mesmo que a IA devolva só "UltraGear". Um campo vazio
    (a IA já sinalizou "não sei") nunca é considerado grounded.

    Correção real (2026-09-12, achado do dono do produto): tokens de
    marca/família/modelo sozinhos NUNCA bastam para provar equivalência
    entre produtos -- duas placas-mãe "B650" e "B650E" da mesma marca/
    família compartilham quase todos os tokens do título, mas são
    MODELOS diferentes; "DDR4" vs "DDR5" no mesmo modelo são VARIANTES
    diferentes. Por isso o grounding agora também exige que `variant`
    (quando a IA devolveu um) e CADA valor em `attributes` apareçam
    literalmente no título -- um atributo que a IA alega mas que não
    está no texto nunca é aceito silenciosamente; a proposta cai em
    `pending_review` (nunca `rejected` automático, nunca aceita)."""
    for field in (brand, family, model):
        if not tokens_present(raw_title_normalized, field):
            return False
    if variant is not None and not tokens_present(raw_title_normalized, variant):
        return False
    if store_sku is not None and not tokens_present(raw_title_normalized, store_sku):
        return False
    if manufacturer_part_number is not None and not tokens_present(
        raw_title_normalized, manufacturer_part_number
    ):
        return False
    for value in attributes.values():
        if not tokens_present(raw_title_normalized, value):
            return False
    return True


def evaluate_ai_identity_extraction(
    raw_title: str, extraction: AIIdentityExtraction
) -> EvaluatedIdentityExtraction:
    """Núcleo determinístico: decide se uma extração da IA é confiável
    o bastante para `status="approved"` -- nunca a própria IA decide
    isso. Critério: grounding (ver `_is_grounded`) E os três campos
    obrigatórios (`brand`/`family`/`model`) não vazios E a fórmula de
    chave determinística (`build_resolved_variant_from_fields`)
    conseguir produzir uma identidade válida a partir dos campos
    (normalização para slug não pode esvaziar nenhum deles).

    Qualquer uma dessas condições falhando -> `status="pending_review"`
    e `resolved=None` -- a extração fica registrada para revisão humana,
    mas NUNCA é usada para resolver identidade automaticamente."""
    raw_title_normalized = normalize_for_grounding(raw_title)
    grounded = _is_grounded(
        raw_title_normalized,
        brand=extraction.brand,
        family=extraction.family,
        model=extraction.model,
        variant=extraction.variant,
        store_sku=extraction.store_sku,
        manufacturer_part_number=extraction.manufacturer_part_number,
        attributes=extraction.attributes,
    )
    resolved = build_resolved_variant_from_fields(
        category=extraction.category,
        brand=extraction.brand,
        family=extraction.family,
        model=extraction.model,
        variant=extraction.variant,
        attributes=extraction.attributes,
    )
    status = "approved" if (grounded and resolved is not None) else "pending_review"
    return EvaluatedIdentityExtraction(
        resolved=resolved,
        grounded=grounded,
        status=status,
        raw=extraction,
    )


async def extract_product_identity_via_ai(
    manager: AIProviderManager,
    *,
    raw_title: str,
    profile: UserRole,
    requested_at: datetime | None = None,
) -> AIIdentityExtraction | None:
    """Chama a IA (via `AIProviderManager` -> César Core) para
    estruturar um título bruto -- `None` em qualquer falha de rede/
    provedor ou resposta fora do contrato esperado (mesmo padrão de
    `app.collection.relevance.normalize_offer_title`: o chamador nunca
    recebe um resultado parcialmente inválido). Roda sempre FORA de
    qualquer transação de banco aberta (é I/O de rede) -- mesma
    disciplina de Fase B do `CollectionOrchestrator` (TASK-079); quem
    integra esta função ao pipeline de coleta deve chamá-la de uma fase
    sem transação ativa, nunca de dentro de `_resolve_offer`/`_persist_
    phase_a`."""
    moment = requested_at or datetime.now(UTC)
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=EXTRACT_IDENTITY_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, raw_title),
        ),
        requested_at=moment,
    )
    try:
        response = await manager.generate(request)
        payload = json.loads(_strip_markdown_code_fence(response.content))
        expected_keys = {
            "category",
            "brand",
            "family",
            "model",
            "variant",
            "store_sku",
            "manufacturer_part_number",
            "attributes",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("unexpected response shape")
        category, brand, family, model = (
            payload["category"],
            payload["brand"],
            payload["family"],
            payload["model"],
        )
        variant = payload["variant"]
        store_sku = payload["store_sku"]
        manufacturer_part_number = payload["manufacturer_part_number"]
        attributes = payload["attributes"]
        if not all(
            isinstance(value, str) for value in (category, brand, family, model)
        ):
            raise ValueError("category/brand/family/model must be strings")
        if variant is not None and not isinstance(variant, str):
            raise ValueError("variant must be a string or null")
        if store_sku is not None and not isinstance(store_sku, str):
            raise ValueError("store_sku must be a string or null")
        if manufacturer_part_number is not None and not isinstance(
            manufacturer_part_number, str
        ):
            raise ValueError("manufacturer_part_number must be a string or null")
        if not isinstance(attributes, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in attributes.items()
        ):
            raise ValueError("attributes must be a string-to-string mapping")
        if not (
            category.strip() and brand.strip() and family.strip() and model.strip()
        ):
            # A própria IA sinalizou incerteza (campo vazio) -- nunca
            # tratamos isso como grounding "acidentalmente" verdadeiro.
            return None
        return AIIdentityExtraction(
            category=category.strip(),
            brand=brand.strip(),
            family=family.strip(),
            model=model.strip(),
            variant=variant.strip() if variant else None,
            store_sku=store_sku.strip() if store_sku else None,
            manufacturer_part_number=(
                manufacturer_part_number.strip() if manufacturer_part_number else None
            ),
            attributes={k: v.strip() for k, v in attributes.items() if v.strip()},
            ai_provider=response.provider,
            ai_model=response.model,
        )
    except Exception:
        logger.warning("product_identity_ai_extraction_failed", exc_info=False)
        return None


async def extract_product_identities_via_ai_batch(
    manager: AIProviderManager,
    *,
    raw_titles: list[str],
    profile: UserRole,
    requested_at: datetime | None = None,
) -> list[AIIdentityExtraction | None]:
    """Mesmo contrato de `extract_product_identity_via_ai`, mas manda um
    LOTE de títulos numa única chamada de IA (`request.purpose=
    BATCH_EXTRACT_IDENTITY_PURPOSE`) -- criada para TASK-123 (achado
    real 2026-09-20, pergunta direta do usuário: resolver o backlog de
    `Product.identity_key IS NULL` gastava 1 chamada de IA por produto,
    o que ameaça a quota diária gratuita quando o backlog cresce).

    Devolve uma lista NA MESMA ORDEM/TAMANHO de `raw_titles`. Cada
    posição é casada pelo "id" que a própria IA devolve (nunca por
    ordem posicional da resposta -- o modelo gratuito pode reordenar
    ou omitir item), então um item malformado ou fora do contrato vira
    `None` SÓ naquela posição, sem contaminar os demais itens do
    mesmo lote. Quando a chamada inteira falha (rede/provedor, ou a
    resposta nem é um array JSON) -- TODAS as posições vêm `None`,
    mesmo fail-closed do modo de item único, só que no grão do lote
    inteiro em vez de 1 título; o chamador trata isso como "continua
    sem identidade nesta rodada", nunca como erro fatal.

    Log em duas etapas separadas (achado real em PROD, 2026-09-20,
    `v1.3.20`: 9 de ~10 lotes falharam num `--dry-run` real e o log
    antigo, `exc_info=False` sem mais nada, não permitia saber se era
    rede/provedor -- ex.: OmniRoute/César Core rejeitando ou expirando
    -- ou o modelo gratuito devolvendo algo fora do contrato para um
    lote maior): `stage="generate"` é falha da chamada em si (rede,
    César Core, provedor); `stage="parse"` é resposta recebida mas fora
    do contrato -- inclui um preview truncado do conteúdo bruto (nunca
    o payload inteiro, mesma disciplina de truncamento de `raw_title`
    usada no resto deste módulo) para dar pista real do que o modelo
    devolveu, sem também gerar exceção não tratada nem vazar payload
    grande no log."""
    results: list[AIIdentityExtraction | None] = [None] * len(raw_titles)
    if not raw_titles:
        return results
    moment = requested_at or datetime.now(UTC)
    payload_in = [
        {"id": index, "title": title} for index, title in enumerate(raw_titles)
    ]
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=BATCH_EXTRACT_IDENTITY_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _BATCH_SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, json.dumps(payload_in, ensure_ascii=False)),
        ),
        requested_at=moment,
    )
    try:
        response = await manager.generate(request)
    except Exception as exc:
        logger.warning(
            "product_identity_ai_batch_extraction_failed",
            extra={
                "stage": "generate",
                "batch_size": len(raw_titles),
                "error": f"{type(exc).__name__}: {exc}"[:300],
            },
            exc_info=False,
        )
        return results

    try:
        payload = json.loads(_strip_markdown_code_fence(response.content))
        if not isinstance(payload, list):
            raise ValueError(f"expected a JSON array, got {type(payload).__name__}")
    except Exception as exc:
        logger.warning(
            "product_identity_ai_batch_extraction_failed",
            extra={
                "stage": "parse",
                "batch_size": len(raw_titles),
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "raw_content_preview": response.content[:300],
            },
            exc_info=False,
        )
        return results

    expected_keys = {
        "id",
        "category",
        "brand",
        "family",
        "model",
        "variant",
        "store_sku",
        "manufacturer_part_number",
        "attributes",
    }
    for item in payload:
        if not isinstance(item, dict) or set(item) != expected_keys:
            continue
        item_id = item["id"]
        if (
            not isinstance(item_id, int)
            or isinstance(item_id, bool)
            or not (0 <= item_id < len(raw_titles))
        ):
            continue
        category, brand, family, model = (
            item["category"],
            item["brand"],
            item["family"],
            item["model"],
        )
        variant = item["variant"]
        store_sku = item["store_sku"]
        manufacturer_part_number = item["manufacturer_part_number"]
        attributes = item["attributes"]
        if not all(
            isinstance(value, str) for value in (category, brand, family, model)
        ):
            continue
        if variant is not None and not isinstance(variant, str):
            continue
        if store_sku is not None and not isinstance(store_sku, str):
            continue
        if manufacturer_part_number is not None and not isinstance(
            manufacturer_part_number, str
        ):
            continue
        if not isinstance(attributes, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in attributes.items()
        ):
            continue
        if not (
            category.strip() and brand.strip() and family.strip() and model.strip()
        ):
            continue
        results[item_id] = AIIdentityExtraction(
            category=category.strip(),
            brand=brand.strip(),
            family=family.strip(),
            model=model.strip(),
            variant=variant.strip() if variant else None,
            store_sku=store_sku.strip() if store_sku else None,
            manufacturer_part_number=(
                manufacturer_part_number.strip() if manufacturer_part_number else None
            ),
            attributes={k: v.strip() for k, v in attributes.items() if v.strip()},
            ai_provider=response.provider,
            ai_model=response.model,
        )
    return results
