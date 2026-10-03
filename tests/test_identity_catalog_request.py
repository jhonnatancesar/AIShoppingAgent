"""TASK-137 -- pedido do usuario -> identidade de monitoramento pelo catalogo."""

from app.products.identity import (
    MonitoringScope,
    build_resolved_variant_from_fields,
    canonical_collection_criteria,
    catalog_family_key,
)
from app.products.identity_catalog import CatalogEntrySnapshot
from app.products.identity_catalog_request import (
    collection_hint,
    monitoring_identity_from_catalog,
)


def _entry(
    *,
    category="motherboard",
    brand="msi",
    family="b650m",
    model="b650m-pro-ddr5",
    names=("B650M PRO DDR5", "B650M PRO"),
    part_numbers=(),
    attributes=None,
):
    attributes = attributes or {
        "socket": "am5",
        "chipset": "b650",
        "form_factor": "micro-atx",
        "memory_type": "ddr5",
    }
    built = build_resolved_variant_from_fields(
        category=category,
        brand=brand,
        family=family,
        model=model,
        variant="base",
        attributes=attributes,
    )
    return CatalogEntrySnapshot(
        category=category,
        brand=brand,
        family=family,
        model=model,
        variant="base",
        attributes=attributes,
        required_attributes=(),
        family_key=catalog_family_key(
            category=category, brand=brand, family=family, model=model
        ),
        identity_key=built.identity_key,
        part_numbers=tuple(part_numbers),
        names=tuple(names),
        source="buildcores",
    )


def test_request_matching_one_catalog_product_gets_a_specific_identity() -> None:
    identity = monitoring_identity_from_catalog((_entry(),), ["MSI B650M PRO"])
    assert identity is not None
    assert identity.scope is MonitoringScope.SPECIFIC
    assert (identity.category, identity.brand) == ("motherboard", "msi")
    assert identity.collection_search == ("msi b650m pro", "B650M PRO")


def test_different_wordings_of_the_same_product_share_the_monitoring_key() -> None:
    a = monitoring_identity_from_catalog((_entry(),), ["MSI B650M PRO"])
    b = monitoring_identity_from_catalog(
        (_entry(),), ["placa mae B650M PRO", "B650M PRO"]
    )
    assert a is not None and b is not None
    assert a.monitoring_key == b.monitoring_key


def test_collection_criteria_uses_the_catalog_name_not_the_identity_fields() -> None:
    identity = monitoring_identity_from_catalog((_entry(),), ["MSI B650M PRO"])
    payload = {
        "scope": identity.scope.value,
        "category": identity.category,
        "brand": identity.brand,
        "family": identity.family,
        "model": identity.model,
        "variant": identity.variant,
        "attributes": dict(identity.attributes),
        "collection": {
            "search_query": identity.collection_search[0],
            "model": identity.collection_search[1],
        },
    }
    criteria = canonical_collection_criteria(payload)
    assert criteria.search_query == "msi b650m pro"
    assert criteria.model == "B650M PRO"
    # Sem o bloco `collection` (itens vindos das regras de texto) nada muda.
    payload.pop("collection")
    assert "ddr5" in canonical_collection_criteria(payload).search_query


def test_longer_or_unknown_model_does_not_get_an_identity() -> None:
    catalog = (_entry(),)
    assert monitoring_identity_from_catalog(catalog, ["MSI B650M PRO-A"]) is None
    assert monitoring_identity_from_catalog(catalog, ["B650M PRO WIFI"]) is None
    assert monitoring_identity_from_catalog(catalog, ["placa mae gamer"]) is None
    assert monitoring_identity_from_catalog(catalog, [""]) is None


def test_two_different_products_in_the_request_texts_do_not_decide() -> None:
    other = _entry(
        model="b650m-gaming-ddr5",
        names=("B650M GAMING DDR5", "B650M GAMING"),
    )
    assert (
        monitoring_identity_from_catalog(
            (_entry(), other), ["MSI B650M PRO", "MSI B650M GAMING"]
        )
        is None
    )


def test_ram_hint_uses_brand_family_and_specs_without_a_title_filter() -> None:
    entry = _entry(
        category="ram",
        brand="kingston",
        family="fury-beast",
        model="kf560c30bbk232",
        names=(),
        part_numbers=("KF560C30BBK232",),
        attributes={"type": "ddr5", "capacity_gb": "32", "speed_mhz": "6000"},
    )
    query, model = collection_hint(
        entry, {"type": "ddr5", "capacity_gb": "32", "speed_mhz": "6000"}
    )
    assert query == "kingston fury beast 32gb ddr5 6000"
    assert model is None
