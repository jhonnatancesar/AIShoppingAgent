"""TASK-137 (passo C) -- candidatos do banco, conferência e resposta da IA."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from app.ai_provider import AIProviderQuotaExceeded, AIResponse
from app.products.identity import build_resolved_variant_from_fields, catalog_family_key
from app.products.identity_ai import AIExtractionFailure
from app.products.identity_catalog import CatalogEntrySnapshot
from app.products.identity_catalog_request import (
    choice_is_coherent,
    find_candidates,
    request_key,
)
from app.products.identity_catalog_resolution_worker import (
    AIChoice,
    choose_candidate_via_ai,
    parse_choice,
)
from app.users.models import UserRole


def _entry(model, names, *, brand="msi"):
    attributes = {"socket": "am5", "chipset": "b650"}
    built = build_resolved_variant_from_fields(
        category="motherboard",
        brand=brand,
        family="b650m",
        model=model,
        variant="base",
        attributes=attributes,
    )
    return CatalogEntrySnapshot(
        category="motherboard",
        brand=brand,
        family="b650m",
        model=model,
        variant="base",
        attributes=attributes,
        required_attributes=(),
        family_key=catalog_family_key(
            category="motherboard", brand=brand, family="b650m", model=model
        ),
        identity_key=built.identity_key,
        part_numbers=(),
        names=tuple(names),
        source="buildcores",
    )


CATALOG = (
    _entry("b650m-gaming-plus-wifi", ["B650M GAMING PLUS WIFI"]),
    _entry("b650m-gaming-plus", ["B650M GAMING PLUS"]),
    _entry("b650m-pro-a", ["B650M PRO A"]),
    _entry("b650m-gaming-wifi-asus", ["B650M GAMING WIFI"], brand="asus"),
)


def test_request_key_ignores_case_accents_and_punctuation() -> None:
    assert request_key("MSI B650M Pró-A") == request_key("msi b650m pro a")
    assert request_key("MSI B650M PRO") != request_key("MSI B650M PRO A")


def test_candidates_are_the_similar_products_of_the_requested_brand() -> None:
    candidates = find_candidates(CATALOG, ["MSI B650M GAMING"])
    models = [c.entry.model for c in candidates]
    assert models == ["b650m-gaming-plus", "b650m-gaming-plus-wifi"]
    # A marca citada no pedido restringe: a ASUS não entra.
    assert "b650m-gaming-wifi-asus" not in models


def test_no_candidates_without_a_model_code_or_without_similarity() -> None:
    assert find_candidates(CATALOG, ["cadeira gamer"]) == []
    assert find_candidates(CATALOG, ["placa mae msi"]) == []
    assert find_candidates(CATALOG, ["MSI X999Z ULTRA SUPER"]) == []


def test_at_most_five_candidates() -> None:
    many = tuple(
        _entry(f"b650m-pro-{n}", [f"B650M PRO {n}"])
        for n in ("a", "b", "c", "d", "e", "f", "g")
    )
    assert len(find_candidates(many, ["MSI B650M PRO"])) == 5


def test_coherence_requires_the_model_code_and_the_brand_of_the_request() -> None:
    entry = CATALOG[1]
    assert choice_is_coherent(["MSI B650M GAMING"], entry, "B650M GAMING PLUS")
    assert not choice_is_coherent(["MSI B650M GAMING"], entry, "X870E GAMING PLUS")
    assert not choice_is_coherent(["ASUS B650M GAMING"], entry, "B650M GAMING PLUS")
    assert not choice_is_coherent(["cadeira gamer"], entry, "B650M GAMING PLUS")


def test_parse_choice_accepts_letter_or_null_and_rejects_everything_else() -> None:
    labels = {"A", "B"}
    assert parse_choice('{"choice": "b"}', labels) == "B"
    assert parse_choice('```json\n{"choice": null}\n```', labels) is None
    for bad in (
        '{"choice": "C"}',
        '{"choice": 1}',
        '{"x": "A"}',
        "texto",
        '{"choice": "A", "y": 1}',
    ):
        with pytest.raises(ValueError):
            parse_choice(bad, labels)


class _Manager:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.requests = content, error, []

    async def generate(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-model",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


CANDIDATES = [
    {"label": "A", "entry_id": "x", "name": "B650M GAMING PLUS", "brand": "msi"},
    {"label": "B", "entry_id": "y", "name": "B650M GAMING PLUS WIFI", "brand": "msi"},
]


def _choose(manager):
    return asyncio.run(
        choose_candidate_via_ai(
            manager,
            request_text="MSI B650M GAMING",
            understood={"search_query": "MSI B650M GAMING", "model": None},
            candidates=CANDIDATES,
            profile=UserRole.ADMIN,
        )
    )


def test_ai_receives_only_the_request_and_the_candidates_and_answers_a_letter() -> None:
    manager = _Manager(json.dumps({"choice": "A"}))
    result = _choose(manager)
    assert result == AIChoice("A", "stub", "stub-model")
    request = manager.requests[0]
    assert request.purpose == "choose_catalog_candidate"
    user = json.loads(request.messages[-1].content)
    assert user["pedido_do_usuario"] == "MSI B650M GAMING"
    assert [p["letra"] for p in user["produtos"]] == ["A", "B"]
    assert "entry_id" not in request.messages[-1].content


def test_ai_failures_are_classified_not_raised() -> None:
    assert _choose(_Manager("not json")) == AIExtractionFailure("invalid_response")
    quota = _choose(_Manager(error=AIProviderQuotaExceeded()))
    assert isinstance(quota, AIExtractionFailure) and quota.kind == "quota"
    assert _choose(_Manager(error=TimeoutError())).kind == "timeout"
