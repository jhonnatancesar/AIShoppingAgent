"""Extração de identidade de produto assistida por IA -- grounding
determinístico e validação de forma (rodada de 2026-09-12), sem banco.
"""

import json
from datetime import UTC, datetime

import pytest
from app.ai_provider import AIResponse
from app.products.identity import build_resolved_variant_from_fields
from app.products.identity_ai import (
    BATCH_EXTRACT_IDENTITY_PURPOSE,
    AIIdentityExtraction,
    evaluate_ai_identity_extraction,
    extract_product_identities_via_ai_batch,
    extract_product_identity_via_ai,
    normalized_title_hash,
    values_match_ignoring_punctuation,
)
from app.users.models import UserRole

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


class _StaticAIManager:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-model",
            content=self._content,
            finished_at=NOW,
        )


class _FailingAIManager:
    async def generate(self, request):
        raise RuntimeError("provider unreachable")


# ---------------------------------------------------------------------------
# build_resolved_variant_from_fields
# ---------------------------------------------------------------------------


def test_build_resolved_variant_from_fields_produces_stable_identity_key() -> None:
    a = build_resolved_variant_from_fields(
        category="monitor",
        brand="LG",
        family="UltraGear",
        model="27GP850",
        variant=None,
        attributes={"refresh_rate_hz": "165Hz"},
    )
    b = build_resolved_variant_from_fields(
        category="Monitor",
        brand="lg",
        family="ultragear",
        model="27gp850",
        variant=None,
        attributes={"refresh_rate_hz": "165Hz"},
    )
    assert a is not None and b is not None
    assert a.identity_key == b.identity_key
    assert a.family_key == b.family_key


def test_build_resolved_variant_from_fields_different_model_different_key() -> None:
    a = build_resolved_variant_from_fields(
        category="monitor",
        brand="LG",
        family="UltraGear",
        model="27GP850",
        variant=None,
        attributes={},
    )
    b = build_resolved_variant_from_fields(
        category="monitor",
        brand="LG",
        family="UltraGear",
        model="32GP850",
        variant=None,
        attributes={},
    )
    assert a is not None and b is not None
    assert a.identity_key != b.identity_key
    assert a.family_key != b.family_key


def test_build_resolved_variant_from_fields_rejects_blank_required_field() -> None:
    assert (
        build_resolved_variant_from_fields(
            category="monitor",
            brand="",
            family="UltraGear",
            model="27GP850",
            variant=None,
            attributes={},
        )
        is None
    )


# ---------------------------------------------------------------------------
# normalized_title_hash
# ---------------------------------------------------------------------------


def test_normalized_title_hash_is_stable_across_case_and_accent() -> None:
    a = normalized_title_hash("Monitor Gamer LG UltraGear 27GP850")
    b = normalized_title_hash("monitor gamer lg ultragear 27gp850")
    assert a == b


def test_normalized_title_hash_differs_for_different_titles() -> None:
    a = normalized_title_hash("Monitor Gamer LG UltraGear 27GP850")
    b = normalized_title_hash("Monitor Gamer LG UltraGear 32GP850")
    assert a != b


# ---------------------------------------------------------------------------
# evaluate_ai_identity_extraction -- grounding determinístico
# ---------------------------------------------------------------------------


def _extraction(**overrides) -> AIIdentityExtraction:
    defaults = dict(
        category="monitor",
        brand="LG",
        family="UltraGear",
        model="27GP850",
        variant=None,
        store_sku=None,
        manufacturer_part_number=None,
        attributes={"refresh_rate_hz": "165Hz"},
        ai_provider="stub",
        ai_model="stub-model",
    )
    defaults.update(overrides)
    return AIIdentityExtraction(**defaults)


def test_grounded_extraction_is_approved_when_all_fields_appear_in_title() -> None:
    title = "Monitor Gamer LG UltraGear 27GP850 27 polegadas 165Hz"
    evaluated = evaluate_ai_identity_extraction(title, _extraction())
    assert evaluated.grounded is True
    assert evaluated.status == "approved"
    assert evaluated.resolved is not None
    assert evaluated.resolved.category == "monitor"
    assert evaluated.resolved.brand == "lg"


def test_extraction_with_invented_brand_not_in_title_is_never_approved() -> None:
    """A IA "alucinou" uma marca que não está no título -- grounding
    precisa recusar, mesmo com os outros campos corretos."""
    title = "Monitor Gamer UltraGear 27GP850 27 polegadas 165Hz"  # sem "LG"
    evaluated = evaluate_ai_identity_extraction(title, _extraction(brand="LG"))
    assert evaluated.grounded is False
    assert evaluated.status == "pending_review"


def test_extraction_with_invented_model_not_in_title_is_never_approved() -> None:
    title = "Monitor Gamer LG UltraGear 165Hz"  # sem o código do modelo
    evaluated = evaluate_ai_identity_extraction(title, _extraction(model="27GP850"))
    assert evaluated.grounded is False
    assert evaluated.status == "pending_review"


def test_grounding_tolerates_accent_and_case_difference() -> None:
    title = "monitor gamer lg ultragear 27gp850 165hz"
    evaluated = evaluate_ai_identity_extraction(title, _extraction())
    assert evaluated.grounded is True
    assert evaluated.status == "approved"


def test_extraction_with_attribute_not_present_in_title_is_never_approved() -> None:
    """Correção real (2026-09-12, achado do dono do produto): tokens de
    marca/família/modelo sozinhos nunca bastam -- um atributo que a IA
    alega ("refresh_rate_hz": "165Hz") mas que não aparece no título
    nunca é aceito silenciosamente, mesmo com brand/family/model
    corretos e grounded."""
    title = "Monitor Gamer LG UltraGear 27GP850 27 polegadas"  # sem "165Hz"
    evaluated = evaluate_ai_identity_extraction(title, _extraction())
    assert evaluated.grounded is False
    assert evaluated.status == "pending_review"


def test_motherboard_variant_by_model_suffix_never_collapses_with_base_model() -> None:
    """Cenário real de placa-mãe: "B650" e "B650E" compartilham quase
    todos os tokens do título, mas são MODELOS diferentes -- se a IA
    confundir um pelo outro (devolver "B650" para um título que na
    verdade diz "B650E", ou vice-versa), o grounding precisa recusar
    porque o token exato do modelo alegado não aparece no título."""
    title_e_variant = "ASUS ROG STRIX B650E-A GAMING WIFI DDR5 AM5"
    wrong_model = _extraction(
        brand="ASUS", family="ROG STRIX", model="B650-A", attributes={}
    )
    evaluated = evaluate_ai_identity_extraction(title_e_variant, wrong_model)
    assert evaluated.grounded is False, (
        "model='B650-A' não deveria dar grounded contra um título que diz "
        "'B650E-A' -- token 'B650-A' não é o mesmo texto que 'B650E-A'"
    )
    assert evaluated.status == "pending_review"

    correct_model = _extraction(
        brand="ASUS", family="ROG STRIX", model="B650E-A", attributes={}
    )
    evaluated_correct = evaluate_ai_identity_extraction(title_e_variant, correct_model)
    assert evaluated_correct.grounded is True
    assert evaluated_correct.status == "approved"
    # E o identity_key de B650-A (motherboard base) precisa ser DIFERENTE
    # do identity_key de B650E-A (variante distinta) -- nunca o mesmo
    # produto só porque a maioria dos tokens do título coincide.
    base_evaluated = evaluate_ai_identity_extraction(
        "ASUS ROG STRIX B650-A GAMING WIFI DDR5 AM5",
        _extraction(brand="ASUS", family="ROG STRIX", model="B650-A", attributes={}),
    )
    assert (
        base_evaluated.resolved is not None and evaluated_correct.resolved is not None
    )
    assert (
        base_evaluated.resolved.identity_key != evaluated_correct.resolved.identity_key
    )


def test_memory_type_variant_never_collapses_with_different_memory_type() -> None:
    """Mesma placa-mãe/modelo, DDR4 vs DDR5 -- variantes distintas de
    memória, nunca unificadas só porque brand/family/model batem."""
    ddr5 = evaluate_ai_identity_extraction(
        "ASUS TUF Gaming B650-Plus WiFi DDR5",
        _extraction(
            brand="ASUS",
            family="TUF Gaming",
            model="B650-Plus",
            attributes={"memory_type": "DDR5"},
        ),
    )
    ddr4 = evaluate_ai_identity_extraction(
        "ASUS TUF Gaming B650-Plus WiFi DDR4",
        _extraction(
            brand="ASUS",
            family="TUF Gaming",
            model="B650-Plus",
            attributes={"memory_type": "DDR4"},
        ),
    )
    assert ddr5.status == "approved" and ddr4.status == "approved"
    assert ddr5.resolved is not None and ddr4.resolved is not None
    assert ddr5.resolved.identity_key != ddr4.resolved.identity_key

    # Se a IA errasse e alegasse "DDR5" para o título que na verdade diz
    # "DDR4", o grounding precisa recusar -- nunca aceitar o atributo
    # errado só porque brand/family/model bateram.
    wrong_attribute = evaluate_ai_identity_extraction(
        "ASUS TUF Gaming B650-Plus WiFi DDR4",
        _extraction(
            brand="ASUS",
            family="TUF Gaming",
            model="B650-Plus",
            attributes={"memory_type": "DDR5"},
        ),
    )
    assert wrong_attribute.grounded is False
    assert wrong_attribute.status == "pending_review"


def test_grounding_rejects_hallucinated_manufacturer_part_number() -> None:
    """Mesmo princípio de anti-alucinação aplicado a manufacturer_part_number:
    um código que a IA alega mas que não aparece literalmente no título
    nunca é aceito silenciosamente."""
    evaluated = evaluate_ai_identity_extraction(
        "ASUS TUF Gaming B650-Plus WiFi",
        _extraction(
            brand="ASUS",
            family="TUF Gaming",
            model="B650-Plus",
            manufacturer_part_number="90MB1FV0-M0EAY0",
        ),
    )
    assert evaluated.grounded is False
    assert evaluated.status == "pending_review"


def test_grounding_accepts_manufacturer_part_number_present_in_title() -> None:
    evaluated = evaluate_ai_identity_extraction(
        "ASUS TUF Gaming B650-Plus WiFi 90MB1FV0-M0EAY0",
        _extraction(
            brand="ASUS",
            family="TUF Gaming",
            model="B650-Plus",
            manufacturer_part_number="90MB1FV0-M0EAY0",
            attributes={},
        ),
    )
    assert evaluated.grounded is True
    assert evaluated.status == "approved"


def test_values_match_ignoring_punctuation_recognizes_same_part_number() -> None:
    """Achado real (par Terabyte/KaBuM da mesma memória, 2026-09-13):
    o mesmo `manufacturer_part_number` pode vir com separador diferente
    ("/" vs "-") entre duas lojas -- é o MESMO código, nunca produto
    diferente."""
    assert values_match_ignoring_punctuation("KF432C16BB12A/16", "KF432C16BB12A-16")


def test_values_match_ignoring_punctuation_rejects_different_codes() -> None:
    assert not values_match_ignoring_punctuation("90MB1FV0-M0EAY0", "90MB1FV1-M0EAY0")


def test_values_match_ignoring_punctuation_rejects_absence() -> None:
    """Ausência de um dos lados nunca é tratada como igualdade -- só
    prova algo quando AMBOS os valores estão presentes."""
    assert not values_match_ignoring_punctuation(None, "90MB1FV0-M0EAY0")
    assert not values_match_ignoring_punctuation("", "90MB1FV0-M0EAY0")
    assert not values_match_ignoring_punctuation(None, None)


def test_pending_review_still_carries_resolved_for_the_review_queue() -> None:
    """Mesmo sem grounding, `resolved` continua preenchido -- é o que
    fica gravado no candidato `pending_review` para um humano revisar
    o que a IA propôs (nunca usado para resolver identidade sozinho)."""
    title = "Monitor Gamer UltraGear 27GP850"  # sem "LG"
    evaluated = evaluate_ai_identity_extraction(title, _extraction(brand="LG"))
    assert evaluated.status == "pending_review"
    assert evaluated.resolved is not None


# ---------------------------------------------------------------------------
# extract_product_identity_via_ai -- fronteira de IA controlada
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extract_product_identity_parses_valid_response() -> None:
    manager = _StaticAIManager(
        '{"category": "monitor", "brand": "LG", "family": "UltraGear", '
        '"model": "27GP850", "variant": null, "store_sku": null, '
        '"manufacturer_part_number": null, '
        '"attributes": {"refresh_rate_hz": "165Hz"}}'
    )
    result = await extract_product_identity_via_ai(
        manager,
        raw_title="Monitor Gamer LG UltraGear 27GP850 165Hz",
        profile=UserRole.ADMIN,
    )
    assert result is not None
    assert result.brand == "LG"
    assert result.attributes == {"refresh_rate_hz": "165Hz"}
    assert result.store_sku is None
    assert result.manufacturer_part_number is None


@pytest.mark.anyio
async def test_extract_product_identity_parses_sku_and_part_number() -> None:
    manager = _StaticAIManager(
        '{"category": "placa-mae", "brand": "Asus", "family": "TUF Gaming", '
        '"model": "B650M-E", "variant": "WIFI", '
        '"store_sku": "TUF-GAMING-B650M-E-WIFI", '
        '"manufacturer_part_number": "90MB1FV0-M0EAY0", "attributes": {}}'
    )
    result = await extract_product_identity_via_ai(
        manager,
        raw_title=(
            "Placa Mae Asus TUF Gaming B650M-E WIFI "
            "TUF-GAMING-B650M-E-WIFI 90MB1FV0-M0EAY0"
        ),
        profile=UserRole.ADMIN,
    )
    assert result is not None
    assert result.store_sku == "TUF-GAMING-B650M-E-WIFI"
    assert result.manufacturer_part_number == "90MB1FV0-M0EAY0"


@pytest.mark.anyio
async def test_extract_product_identity_none_on_malformed_json() -> None:
    manager = _StaticAIManager("not json at all")
    result = await extract_product_identity_via_ai(
        manager, raw_title="Monitor Gamer LG UltraGear 27GP850", profile=UserRole.ADMIN
    )
    assert result is None


@pytest.mark.anyio
async def test_extract_product_identity_none_on_unexpected_shape() -> None:
    manager = _StaticAIManager('{"only_one_field": "x"}')
    result = await extract_product_identity_via_ai(
        manager, raw_title="Monitor Gamer LG UltraGear 27GP850", profile=UserRole.ADMIN
    )
    assert result is None


@pytest.mark.anyio
async def test_extract_product_identity_none_when_ai_signals_uncertainty() -> None:
    """Campo vazio ("") é a própria IA dizendo "não sei" -- nunca tratado
    como um valor real que por acaso falha grounding depois."""
    manager = _StaticAIManager(
        '{"category": "monitor", "brand": "", "family": "UltraGear", '
        '"model": "27GP850", "variant": null, "store_sku": null, '
        '"manufacturer_part_number": null, "attributes": {}}'
    )
    result = await extract_product_identity_via_ai(
        manager, raw_title="Monitor Gamer LG UltraGear 27GP850", profile=UserRole.ADMIN
    )
    assert result is None


@pytest.mark.anyio
async def test_extract_product_identity_none_when_provider_fails() -> None:
    result = await extract_product_identity_via_ai(
        _FailingAIManager(),
        raw_title="Monitor Gamer LG UltraGear 27GP850",
        profile=UserRole.ADMIN,
    )
    assert result is None


# ---------------------------------------------------------------------------
# extract_product_identities_via_ai_batch -- lote da TASK-123
# ---------------------------------------------------------------------------


class _CapturingAIManager(_StaticAIManager):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return await super().generate(request)


def _batch_item(item_id, **overrides):
    item = {
        "id": item_id,
        "category": "monitor",
        "brand": "LG",
        "family": "UltraGear",
        "model": "27GP850",
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": None,
        "attributes": {},
    }
    item.update(overrides)
    return item


@pytest.mark.anyio
async def test_batch_matches_items_by_id_never_by_position() -> None:
    """O modelo gratuito pode reordenar/omitir itens e envolver o JSON em
    cerca de código -- cada posição só recebe o item do PRÓPRIO id."""
    content = json.dumps(
        [
            _batch_item(2, model="32GS95UE"),
            {"id": 1, "brand": "sem as outras chaves"},
            _batch_item(True, model="bool nunca é id"),
            _batch_item(7, model="fora do lote"),
            _batch_item(0, variant=" Branco ", attributes={"hz": "165Hz", "x": " "}),
        ]
    )
    manager = _CapturingAIManager(f"```json\n{content}\n```")

    results = await extract_product_identities_via_ai_batch(
        manager,
        raw_titles=["LG UltraGear 27GP850 Branco", "título sem item", "LG 32GS95UE"],
        profile=UserRole.ADMIN,
        requested_at=NOW,
    )

    assert [None if r is None else r.model for r in results] == [
        "27GP850",
        None,
        "32GS95UE",
    ]
    assert results[0].variant == "Branco"
    assert results[0].attributes == {"hz": "165Hz"}
    request = manager.requests[0]
    assert request.max_tokens == 4096
    assert request.purpose == BATCH_EXTRACT_IDENTITY_PURPOSE
    assert request.messages[1].content.startswith("Processe os 3 títulos")


@pytest.mark.anyio
async def test_batch_rejects_items_with_wrong_types_or_blank_required_fields() -> None:
    content = json.dumps(
        [
            _batch_item(0, brand=123),
            _batch_item(1, variant=5),
            _batch_item(2, store_sku=5),
            _batch_item(3, manufacturer_part_number=5),
            _batch_item(4, attributes=["não é objeto"]),
            _batch_item(5, attributes={"hz": 165}),
            _batch_item(6, model="  "),
            _batch_item(7, store_sku=" SKU-1 ", manufacturer_part_number=" 27GP850-B "),
        ]
    )

    results = await extract_product_identities_via_ai_batch(
        _StaticAIManager(content),
        raw_titles=[f"título {n}" for n in range(8)],
        profile=UserRole.ADMIN,
    )

    assert results[:7] == [None] * 7
    assert results[7].store_sku == "SKU-1"
    assert results[7].manufacturer_part_number == "27GP850-B"


@pytest.mark.anyio
@pytest.mark.parametrize("content", ['{"id": 0}', "não é JSON"])
async def test_batch_all_none_when_response_is_not_a_json_array(content) -> None:
    results = await extract_product_identities_via_ai_batch(
        _StaticAIManager(content), raw_titles=["a", "b"], profile=UserRole.ADMIN
    )

    assert results == [None, None]


@pytest.mark.anyio
async def test_batch_all_none_when_provider_fails() -> None:
    results = await extract_product_identities_via_ai_batch(
        _FailingAIManager(), raw_titles=["a", "b", "c"], profile=UserRole.ADMIN
    )

    assert results == [None, None, None]


@pytest.mark.anyio
async def test_batch_with_no_titles_never_calls_ai() -> None:
    manager = _StaticAIManager("[]")

    assert (
        await extract_product_identities_via_ai_batch(
            manager, raw_titles=[], profile=UserRole.ADMIN
        )
        == []
    )
    assert manager.calls == 0
