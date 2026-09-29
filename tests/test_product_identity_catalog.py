"""TASK-132 (Parte B) -- catálogo de nomenclaturas: casamento por part number e
por nome, guarda de palavras de edição e sanidade da pré-lista (unitário; o
comportamento contra Postgres real está em
`tests/integration/test_identity_catalog.py`)."""

from types import SimpleNamespace

from app.products.identity_catalog import (
    CatalogEntrySnapshot,
    compact_code,
    is_usable_part_number,
    match_catalog,
)
from app.products.identity_catalog_seed import SEED_ENTRIES, SeedEntry
from app.products.identity_edition import has_unknown_edition_word


def _entry(
    *,
    family: str = "redmi-note",
    model: str = "13",
    variant: str = "base",
    names: tuple[str, ...] = (),
    part_numbers: tuple[str, ...] = (),
    required: tuple[str, ...] = ("storage_gb",),
    category: str = "smartphone",
    brand: str = "xiaomi",
) -> CatalogEntrySnapshot:
    return CatalogEntrySnapshot(
        category=category,
        brand=brand,
        family=family,
        model=model,
        variant=variant,
        attributes={},
        required_attributes=required,
        family_key="fk",
        identity_key=None,
        part_numbers=part_numbers,
        names=names,
    )


def _redmi_catalog() -> tuple[CatalogEntrySnapshot, ...]:
    return (
        _entry(names=("REDMI NOTE 13",)),
        _entry(variant="pro", names=("REDMI NOTE 13 PRO",)),
        _entry(
            variant="pro-plus", names=("REDMI NOTE 13 PRO+", "REDMI NOTE 13 PRO PLUS")
        ),
    )


class TestNameMatching:
    def test_edition_entries_never_cross_match(self) -> None:
        catalog = _redmi_catalog()
        base = match_catalog(catalog, "Smartphone Xiaomi Redmi Note 13 5G 256GB")
        pro = match_catalog(catalog, "Xiaomi Redmi Note 13 Pro 5G 256GB")
        plus = match_catalog(catalog, "Xiaomi Redmi Note 13 Pro+ 5G 512GB")
        plus_spelled = match_catalog(catalog, "Xiaomi Redmi Note 13 Pro Plus 512GB")
        assert (base.variant, pro.variant, plus.variant, plus_spelled.variant) == (
            "base",
            "pro",
            "pro-plus",
            "pro-plus",
        )

    def test_capacity_comes_from_the_title_like_the_extractors(self) -> None:
        resolved = match_catalog(_redmi_catalog(), "Redmi Note 13 (1 TB)")
        assert dict(resolved.attributes) == {"storage_gb": "1024"}

    def test_phone_without_capacity_does_not_resolve(self) -> None:
        assert match_catalog(_redmi_catalog(), "Xiaomi Redmi Note 13 5G") is None

    def test_unknown_title_does_not_resolve(self) -> None:
        assert (
            match_catalog(_redmi_catalog(), "Notebook Dell Inspiron 15 512GB") is None
        )

    def test_two_entries_with_the_same_name_are_ambiguous(self) -> None:
        catalog = (
            _entry(names=("REDMI NOTE 13",)),
            _entry(model="14", names=("REDMI NOTE 13",)),
        )
        assert match_catalog(catalog, "Redmi Note 13 256GB") is None

    def test_complete_identity_entry_resolves_without_attributes(self) -> None:
        catalog = (
            _entry(
                category="gpu",
                brand="amd",
                family="radeon-rx",
                model="9070-xt",
                names=("RX 9070 XT",),
                required=(),
            ),
            _entry(
                category="gpu",
                brand="amd",
                family="radeon-rx",
                model="9070",
                names=("RX 9070",),
                required=(),
            ),
        )
        xt = match_catalog(catalog, "Placa de Vídeo Radeon RX 9070 XT 16GB")
        plain = match_catalog(catalog, "Placa de Vídeo Radeon RX 9070 16GB")
        assert (xt.model, plain.model) == ("9070-xt", "9070")


class TestPartNumberMatching:
    def _catalog(self) -> tuple[CatalogEntrySnapshot, ...]:
        return (
            _entry(
                category="ram",
                brand="kingston",
                family="fury-beast",
                model="kf560c36bbe-8",
                part_numbers=("KF560C36BBE8",),
                required=(),
            ),
            _entry(
                category="ram",
                brand="kingston",
                family="fury-beast",
                model="kf560c36bbea-8",
                part_numbers=("KF560C36BBEA8",),
                required=(),
            ),
        )

    def test_part_number_matches_with_any_punctuation(self) -> None:
        for title in (
            "Memória Kingston Fury Beast 8GB DDR5 6000MHz KF560C36BBE-8",
            "KF560C36BBE8 Kingston Fury 8GB",
            "Memoria Fury Beast KF560C36BBE 8 preta",
        ):
            assert match_catalog(self._catalog(), title).model == "kf560c36bbe-8"

    def test_rgb_part_number_is_a_different_product(self) -> None:
        resolved = match_catalog(
            self._catalog(), "Kingston Fury Beast RGB 8GB 6000MT/s KF560C36BBEA-8"
        )
        assert resolved.model == "kf560c36bbea-8"

    def test_part_number_beats_a_name_that_would_match_too(self) -> None:
        catalog = (
            *self._catalog(),
            _entry(
                category="ram",
                brand="kingston",
                family="fury-beast",
                model="generic",
                names=("FURY BEAST",),
                required=(),
            ),
        )
        resolved = match_catalog(catalog, "Kingston FURY Beast 8GB KF560C36BBE-8")
        assert resolved.model == "kf560c36bbe-8"


class TestPartNumberRules:
    def test_compact_code_ignores_punctuation_and_case(self) -> None:
        assert compact_code("kf560c36bbe-8") == compact_code("KF560C36BBE 8")
        assert compact_code("PVV5-32G600C36K") == "PVV532G600C36K"

    def test_only_specific_part_numbers_are_usable(self) -> None:
        assert is_usable_part_number("KF560C36BBE8")
        assert not is_usable_part_number("6000")  # só número
        assert not is_usable_part_number("DDR5")  # curto
        assert not is_usable_part_number("ABCDEFGH")  # sem dígito


class TestEditionGuard:
    def test_shared_guard_uses_only_letters_and_digits(self) -> None:
        pro = SimpleNamespace(
            brand="xiaomi",
            family="redmi-note",
            model="13",
            variant="base",
            attributes={},
        )
        assert has_unknown_edition_word("REDMI NOTE 13 PRO+ 5G", pro) is True
        assert has_unknown_edition_word("REDMI NOTE 13 5G", pro) is False


class TestSeed:
    def test_seed_has_no_duplicate_identity_or_name(self) -> None:
        identities = [
            (s.category, s.brand, s.family, s.model, s.variant) for s in SEED_ENTRIES
        ]
        assert len(identities) == len(set(identities))
        names = [name for seed in SEED_ENTRIES for name in seed.names]
        assert len(names) == len(set(names))

    def test_seed_covers_more_than_phones(self) -> None:
        assert {s.category for s in SEED_ENTRIES} >= {"smartphone", "cpu", "gpu"}

    def test_seed_values_are_slugs_and_names_are_uppercase(self) -> None:
        for seed in SEED_ENTRIES:
            assert isinstance(seed, SeedEntry)
            for value in (
                seed.category,
                seed.brand,
                seed.family,
                seed.model,
                seed.variant,
            ):
                assert value == value.lower() and " " not in value, value
            assert all(name == name.upper() for name in seed.names)

    def test_phone_seeds_require_capacity_and_others_do_not(self) -> None:
        for seed in SEED_ENTRIES:
            assert bool(seed.required_attributes) == (seed.category == "smartphone")

    def test_every_seed_resolves_from_its_own_name(self) -> None:
        for seed in SEED_ENTRIES:
            entry = CatalogEntrySnapshot(
                category=seed.category,
                brand=seed.brand,
                family=seed.family,
                model=seed.model,
                variant=seed.variant,
                attributes={},
                required_attributes=seed.required_attributes,
                family_key="fk",
                identity_key=None,
                part_numbers=(),
                names=seed.names,
            )
            title = f"{seed.names[0].title()} 256GB"
            resolved = match_catalog((entry,), title)
            assert resolved is not None, seed
            assert (resolved.brand, resolved.family, resolved.model) == (
                seed.brand,
                seed.family,
                seed.model,
            )
