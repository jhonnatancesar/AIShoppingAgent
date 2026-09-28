"""TASK-129 -- vocabulário padronizado da extração de identidade por IA.

Achado real (dry-run da PROD, 2026-09-27): a IA escrevia categoria/marca
livremente ("motherboard" x "placa-mae", "kingston" x "fury") e o mesmo
produto virava dois. Estes testes simulam exatamente isso: a IA respondendo
de formas diferentes para o mesmo produto tem que dar a MESMA identidade."""

import asyncio
import json
from datetime import UTC, datetime

from app.ai_provider import AIResponse
from app.products.identity import registered_categories
from app.products.identity_ai import (
    AIIdentityExtraction,
    build_partial_link,
    evaluate_ai_identity_extraction,
    extract_product_identities_via_ai_batch,
    extract_product_identity_via_ai,
)
from app.products.identity_vocabulary import (
    CATEGORY_MEANINGS,
    IdentityVocabulary,
    canonicalize_fields,
    category_slug,
    render_vocabulary_prompt,
)
from app.users.models import UserRole

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
_FURY = IdentityVocabulary(aliases=(("ram", "brand", "fury", "kingston"),))


def _extraction(**fields) -> AIIdentityExtraction:
    base = {
        "category": "motherboard",
        "brand": "ASUS",
        "family": "TUF Gaming",
        "model": "B650-Plus",
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": None,
        "attributes": {},
        "ai_provider": "stub",
        "ai_model": "m",
    }
    base.update(fields)
    return AIIdentityExtraction(**base)


# --- categoria -----------------------------------------------------------


def test_every_registered_category_has_a_meaning_for_the_ai() -> None:
    assert {name for name, _ in registered_categories()} == set(CATEGORY_MEANINGS)


def test_category_synonyms_become_the_official_category() -> None:
    vocab = IdentityVocabulary()
    for written in ("placa-mae", "Placa Mãe", "PLACA MAE", "mainboard", "motherboard"):
        assert vocab.canonical_category(written) == "motherboard", written
    assert vocab.canonical_category("Memória RAM") == "ram"
    assert vocab.canonical_category("fonte") == "psu"
    assert vocab.canonical_category("Placa de Vídeo") == "gpu"


def test_generic_category_is_standardized_not_invented() -> None:
    vocab = IdentityVocabulary()
    assert vocab.canonical_category("Cadeira Gamer") == "cadeira-gamer"
    assert vocab.canonical_category("  ") == ""
    alias = IdentityVocabulary(
        aliases=(("*", "category", "cadeira-para-games", "cadeira-gamer"),)
    )
    assert alias.canonical_category("Cadeira para Games") == "cadeira-gamer"


# --- marca/família -------------------------------------------------------


def test_active_alias_turns_product_line_into_manufacturer() -> None:
    fields = canonicalize_fields(
        _FURY, category="Memória", brand="Fury", family="Beast"
    )
    assert (fields.category, fields.brand, fields.family) == (
        "ram",
        "kingston",
        "Beast",
    )
    # alias é por categoria: "fury" de outra categoria não vira kingston
    other = canonicalize_fields(_FURY, category="mouse", brand="Fury", family=None)
    assert other.brand == "Fury"
    assert canonicalize_fields(None, category="x", brand="", family=None).brand == ""


def test_same_product_written_two_ways_gets_the_same_identity() -> None:
    """O caso real do dry-run: um lote respondeu "motherboard", outro
    "placa-mae" -- agora os dois viram a mesma identidade."""
    title = "Placa-Mãe ASUS TUF Gaming B650-Plus AM5"
    english = evaluate_ai_identity_extraction(
        title, _extraction(category="motherboard")
    )
    portuguese = evaluate_ai_identity_extraction(
        title, _extraction(category="placa-mae")
    )
    assert english.status == portuguese.status == "approved"
    assert english.resolved.identity_key == portuguese.resolved.identity_key
    assert portuguese.resolved.category == "motherboard"


def test_alias_brand_keeps_grounding_on_what_the_title_says() -> None:
    """O grounding confere "Fury" (está no título); a identidade usa a
    grafia oficial "kingston" -- nunca reprova por causa do alias."""
    title = "Memória Fury Beast 8GB DDR4 3200MHz KF432C16BB/8"
    evaluated = evaluate_ai_identity_extraction(
        title,
        _extraction(category="ram", brand="Fury", family="Beast", model="KF432C16BB/8"),
        vocabulary=_FURY,
    )
    assert evaluated.status == "approved"
    assert evaluated.resolved.brand == "kingston"


def test_partial_link_uses_official_category_and_alias() -> None:
    link = build_partial_link(
        "Memória Fury 8GB DDR4",
        category="Memória RAM",
        brand="Fury",
        family=None,
        vocabulary=_FURY,
    )
    assert (link.category, link.brand, link.family) == ("ram", "kingston", None)
    assert (
        build_partial_link(
            "Cadeira Gamer Reclinável", category="Cadeira Gamer", brand="", family=""
        ).category
        == "cadeira-gamer"
    )


# --- o que a IA recebe ---------------------------------------------------


def test_prompt_lists_official_categories_generic_families_and_aliases() -> None:
    text = render_vocabulary_prompt(
        IdentityVocabulary(
            generic_categories=("cadeira-gamer",),
            known_families=(("ram", "kingston", "fury-beast"),),
            aliases=(("ram", "brand", "fury", "kingston"),),
        )
    )
    for name, _ in registered_categories():
        assert f"- {name}: " in text
    assert "- motherboard: placa-mãe. Atributos: socket, chipset, form_factor" in text
    assert "Categorias genéricas já em uso: cadeira-gamer" in text
    assert "- ram | kingston | fury-beast" in text
    assert "- ram brand: fury -> kingston" in text
    bare = render_vocabulary_prompt(None)
    assert "cadeira gamer" in bare and "Marcas e famílias" not in bare


class _Capturing:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="m",
            content=self.content,
            finished_at=NOW,
        )


def test_single_and_batch_extraction_send_the_vocabulary() -> None:
    vocab = IdentityVocabulary(generic_categories=("cadeira-gamer",))
    single = _Capturing("não é json")
    asyncio.run(
        extract_product_identity_via_ai(
            single, raw_title="x", profile=UserRole.ADMIN, vocabulary=vocab
        )
    )
    batch = _Capturing(json.dumps([]))
    asyncio.run(
        extract_product_identities_via_ai_batch(
            batch, raw_titles=["x"], profile=UserRole.ADMIN, vocabulary=vocab
        )
    )
    for manager in (single, batch):
        system = manager.requests[0].messages[0].content
        assert "O QUE PREENCHER EM CADA CAMPO" in system
        assert "VOCABULÁRIO OBRIGATÓRIO" in system
        assert "Categorias genéricas já em uso: cadeira-gamer" in system


def test_category_slug_matches_identity_slug_shape() -> None:
    assert category_slug("Placa-Mãe  B650") == "placa-mae-b650"


# --- cor nunca separa produto (decisão do usuário, 2026-09-27) ------------


def test_color_is_never_part_of_the_identity() -> None:
    """A cor só importa quando o usuário pede na missão (a relevância
    filtra pelo título real); a identidade é a mesma para todas as cores."""
    title = "Headset Gamer HyperX Cloud Stinger 2 Preto Vermelho"
    base = {
        "category": "headset",
        "brand": "HyperX",
        "family": "Cloud",
        "model": "Stinger 2",
    }
    as_variant = evaluate_ai_identity_extraction(
        title, _extraction(**base, variant="Preto")
    )
    as_attribute = evaluate_ai_identity_extraction(
        title, _extraction(**base, attributes={"color": "Vermelho"})
    )
    without = evaluate_ai_identity_extraction(title, _extraction(**base))
    assert as_variant.status == as_attribute.status == without.status == "approved"
    assert (
        as_variant.resolved.identity_key
        == as_attribute.resolved.identity_key
        == without.resolved.identity_key
    )


def test_strip_color_keeps_real_variants_and_other_attributes() -> None:
    from app.products.identity_vocabulary import is_color, strip_color

    assert strip_color("Luz das Estrelas", {"cor": "Rosa", "storage_gb": "128GB"}) == (
        None,
        {"storage_gb": "128GB"},
    )
    assert strip_color("WiFi", {}) == ("WiFi", {})
    assert is_color("Space Gray") and is_color("Preto")
    assert not is_color("Pro") and not is_color("")
