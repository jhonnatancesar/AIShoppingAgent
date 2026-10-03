"""TASK-137 -- pedido por especificacao ("quero uma b550") e filtro de termos."""

from types import SimpleNamespace

import pytest
from app.collection.contracts import OfferCondition
from app.collection.normalization import Availability
from app.collection.orchestration import _select_final_candidates
from app.collection.spec_terms import (
    title_satisfies,
    title_satisfies_all,
    total_memory_gb,
)
from app.products.identity import MonitoringScope, canonical_collection_criteria
from app.products.identity_catalog import CatalogEntrySnapshot
from app.products.identity_spec_request import (
    build_vocabulary,
    monitoring_identity_from_spec,
    parse_spec_request,
)


def _board(chipset, socket, brand="msi"):
    return CatalogEntrySnapshot(
        category="motherboard",
        brand=brand,
        family="x",
        model=f"{chipset}-board",
        variant="base",
        attributes={"chipset": chipset, "socket": socket},
        required_attributes=(),
        family_key="f",
        identity_key="i",
        part_numbers=(),
        names=(f"{chipset.upper()} BOARD",),
        source="buildcores",
    )


CATALOG = (
    _board("b550", "am4"),
    _board("b650", "am5"),
    _board("b650e", "am5", brand="asus"),
    _board("x870e", "am5"),
    _board("z790", "lga-1700", brand="gigabyte"),
)
VOCAB = build_vocabulary(CATALOG)


def _parse(text):
    return parse_spec_request(VOCAB, [text])


@pytest.mark.parametrize(
    ("text", "category", "attributes"),
    [
        ("quero uma placa mae b550", "motherboard", {"chipset": "b550"}),
        ("quero uma b550", "motherboard", {"chipset": "b550"}),
        ("B550", "motherboard", {"chipset": "b550"}),
        ("placa mae x870e", "motherboard", {"chipset": "x870e"}),
        (
            "quero uma b650m",
            "motherboard",
            {"chipset": "b650", "form_factor": "micro-atx"},
        ),
        ("placa mae am5", "motherboard", {"socket": "am5"}),
        (
            "placa mae lga1700 ddr5",
            "motherboard",
            {"socket": "lga-1700", "memory_type": "ddr5"},
        ),
        ("quero uma memoria ddr5 de 8 Gb", "ram", {"type": "ddr5", "capacity_gb": "8"}),
        (
            "memoria ram ddr4 16gb 3200mhz",
            "ram",
            {"type": "ddr4", "capacity_gb": "16", "speed_mhz": "3200"},
        ),
        ("ddr5 8gb", "ram", {"type": "ddr5", "capacity_gb": "8"}),
    ],
)
def test_user_requests_become_specifications(text, category, attributes) -> None:
    spec = _parse(text)
    assert spec is not None, text
    assert spec.category == category
    assert spec.attributes == attributes


@pytest.mark.parametrize(
    "text",
    [
        "placa mae",  # nenhuma especificação
        "quero uma memoria ram",
        "MSI B650M GAMING",  # sobrou palavra: mais específico que uma especificação
        "quero uma rtx 5070",  # outra categoria (regra fixa de GPU)
        "cadeira gamer",
        "memoria b550",  # categorias misturadas
        "placa mae 8gb",
        "ddr5",  # tipo sozinho, sem categoria
        "placa mae matx",  # só o formato é amplo demais
        "",
    ],
)
def test_requests_that_are_not_specifications_are_left_alone(text) -> None:
    assert _parse(text) is None


def test_form_factor_words_are_understood_and_enforced() -> None:
    spec = _parse("quero uma placa mae b550 matx")
    assert spec is not None
    assert spec.attributes == {"chipset": "b550", "form_factor": "micro-atx"}
    assert "form_factor:micro-atx" in spec.terms
    assert _parse("placa mae b550 atx") is not None  # ATX sozinho não filtra


def test_same_specification_in_different_words_shares_one_family_item() -> None:
    a = monitoring_identity_from_spec(CATALOG, ["quero uma b550"])
    b = monitoring_identity_from_spec(CATALOG, ["Placa-mãe B550", "B550"])
    c = monitoring_identity_from_spec(CATALOG, ["quero uma b650"])
    assert a is not None and b is not None and c is not None
    assert a.monitoring_key == b.monitoring_key != c.monitoring_key
    assert a.scope is MonitoringScope.FAMILY
    assert a.collection_search == ("placa mae b550", None)
    assert a.collection_terms == ("chipset:b550",)


def test_collection_criteria_carries_the_required_terms() -> None:
    identity = monitoring_identity_from_spec(CATALOG, ["memoria ddr5 8gb"])
    payload = {
        "brand": identity.brand,
        "family": identity.family,
        "model": identity.model,
        "collection": {
            "search_query": identity.collection_search[0],
            "model": None,
            "required_terms": list(identity.collection_terms),
        },
    }
    criteria = canonical_collection_criteria(payload)
    assert criteria.search_query == "memoria ram ddr5 8gb"
    assert criteria.required_terms == ("type:ddr5", "capacity_gb:8")


# --- filtro de termos no título ---------------------------------------------


def test_chipset_term_accepts_form_factor_suffix_but_not_another_chipset() -> None:
    assert title_satisfies("chipset:b550", "Placa Mae Gigabyte B550M DS3H")
    assert title_satisfies("chipset:b550", "ASUS TUF GAMING B550-PLUS WIFI II")
    assert not title_satisfies("chipset:b650", "Placa Mae ASUS B650E-F GAMING")
    assert not title_satisfies("chipset:b550", "Placa Mae B5500 qualquer")
    assert title_satisfies("chipset_exact:b650m", "Placa Mae MSI B650M-A PRO")
    assert not title_satisfies("chipset_exact:b650m", "Placa Mae MSI B650 TOMAHAWK ATX")


def test_memory_capacity_is_the_kit_total_not_a_module() -> None:
    assert title_satisfies("capacity_gb:8", "Memoria Kingston 8GB DDR5 4800")
    assert title_satisfies("capacity_gb:8", "Memoria 8 GB DDR4 3200MHz")
    assert not title_satisfies("capacity_gb:8", "Memoria Corsair 16GB (2x8GB) DDR5")
    assert title_satisfies("capacity_gb:16", "Memoria Corsair Kit 2x8GB DDR5")
    assert not title_satisfies("capacity_gb:8", "Memoria Kingston Fury 32GB DDR5")
    assert total_memory_gb("NOTEBOOK 8GB 512GB SSD") == 8
    assert total_memory_gb("MEMORIA SEM CAPACIDADE") is None


def test_memory_type_never_confuses_lpddr_and_separates_ddr_generations() -> None:
    assert title_satisfies("type:ddr5", "Memoria Kingston DDR5 4800")
    assert not title_satisfies("type:ddr5", "Memoria LPDDR5 soldada")
    assert not title_satisfies("type:ddr5", "Memoria Kingston DDR4 3200")
    assert title_satisfies("memory_type:ddr4", "Placa Mae B550M DDR4 AM4")


def test_socket_brand_speed_and_form_factor_terms() -> None:
    assert title_satisfies("socket:am5", "Placa Mae B650 Socket AM5")
    assert not title_satisfies("socket:am5", "Placa Mae B550 AM4")
    assert title_satisfies("socket:lga-1700", "Placa Mae Z790 LGA 1700")
    assert title_satisfies("brand:msi", "Placa Mae MSI B550M")
    assert not title_satisfies("brand:msi", "Placa Mae GIGABYTE B550M")
    assert title_satisfies("speed_mhz:6000", "Memoria DDR5-6000 CL30 16GB")
    assert not title_satisfies("speed_mhz:6000", "Memoria DDR5 16000")
    assert title_satisfies("form_factor:micro-atx", "Placa Mae B550 Micro-ATX")
    assert title_satisfies("form_factor:micro-atx", "Placa Mae B550M mATX")
    assert not title_satisfies("form_factor:micro-atx", "Placa Mae B550 ATX")
    assert title_satisfies_all((), "qualquer")
    assert not title_satisfies_all(("chipset:b550", "type:ddr5"), "Placa B550 DDR4")


def test_collection_keeps_only_titles_that_meet_every_term() -> None:
    def offer(title):
        return SimpleNamespace(
            raw_offer=SimpleNamespace(title=title, external_id=title, url=title),
            condition=OfferCondition.NEW,
            seller_kind=None,
            availability=Availability.AVAILABLE,
            amount=1,
            total_amount=1,
        )

    offers = tuple(
        offer(title)
        for title in (
            "Placa Mae Gigabyte B550M DS3H DDR4",
            "Placa Mae ASUS B650E-F",
            "Placa Mae MSI MAG B550 Tomahawk",
            "Fonte 650W",
        )
    )
    kept = _select_final_candidates(
        search_query="placa mae b550",
        model=None,
        source_code="kabum",
        offers=offers,
        required_terms=("chipset:b550",),
    )
    assert sorted(item.raw_offer.title for item in kept) == [
        "Placa Mae Gigabyte B550M DS3H DDR4",
        "Placa Mae MSI MAG B550 Tomahawk",
    ]
