"""TASK-137 -- mapeamento das fontes abertas e casamento estrito por nome."""

from app.products.identity import build_resolved_variant_from_fields, catalog_family_key
from app.products.identity_catalog import CatalogEntrySnapshot, match_catalog
from app.products.identity_catalog_import import (
    MappingReport,
    drop_ambiguous_codes,
    map_buildcores_motherboard,
    map_buildcores_ram,
    merge_same_identity,
)


def _board(opendb_id, name, manufacturer="MSI", **extra):
    record = {
        "opendb_id": opendb_id,
        "socket": "AM5",
        "form_factor": "Micro ATX",
        "chipset": "AMD B650",
        "memory": {"ram_type": "DDR5"},
        "metadata": {
            "name": name,
            "manufacturer": manufacturer,
            "part_numbers": extra.pop("part_numbers", []),
            "series": extra.pop("series", ""),
        },
    }
    record.update(extra)
    return record


def _ram(opendb_id, name, ram_type, part_numbers, manufacturer="Kingston", **extra):
    record = {
        "opendb_id": opendb_id,
        "ram_type": ram_type,
        "capacity": 32,
        "speed": 6000,
        "modules": {"quantity": 2, "capacity_gb": 16},
        "cas_latency": 30,
        "metadata": {
            "name": name,
            "manufacturer": manufacturer,
            "part_numbers": part_numbers,
            "series": "FURY Beast",
        },
    }
    record.update(extra)
    return record


def test_board_name_loses_brand_socket_and_form_factor_but_keeps_memory_type() -> None:
    report = MappingReport()
    entry = map_buildcores_motherboard(
        _board(
            "b1",
            "MSI B450M PRO-M2 MAX AM4 DDR4 Micro ATX",
            part_numbers=["B450M PRO-M2 MAX", "7B84-017R"],
            series="B450M",
            chipset="AMD B450",
            socket="AM4",
            memory={"ram_type": "DDR4"},
        ),
        report,
    )
    assert entry is not None
    assert entry.brand == "msi"
    assert entry.model == "b450m-pro-m2-max-ddr4"
    assert entry.attributes["memory_type"] == "ddr4"
    assert entry.attributes["chipset"] == "b450"
    assert entry.part_numbers == ("7B84017R",)
    assert "B450M PRO M2 MAX DDR4" in entry.names
    assert "B450M PRO M2 MAX" in entry.names


def test_chipset_repeated_at_the_start_of_the_source_name_is_removed() -> None:
    entry = map_buildcores_motherboard(
        _board(
            "c1",
            "MSI A520 A520M PRO-VDH AM4 DDR4 Micro ATX",
            part_numbers=["7C96-001R"],
        ),
        MappingReport(),
    )
    assert entry is not None
    assert entry.model == "a520m-pro-vdh-ddr4"
    assert "A520M PRO VDH" in entry.names


def test_same_board_name_in_ddr4_and_ddr5_are_different_products() -> None:
    report = MappingReport()
    ddr4 = map_buildcores_motherboard(
        _board(
            "d4",
            "MSI PRO B660M VC WIFI DDR4 Micro ATX",
            part_numbers=["PRO-B660M-VC-WIFI-DDR4"],
            memory={"ram_type": "DDR4"},
        ),
        report,
    )
    ddr5 = map_buildcores_motherboard(
        _board(
            "d5",
            "MSI PRO B660M VC WIFI DDR5 Micro ATX",
            part_numbers=["PRO-B660M-VC-WIFI-DDR5"],
            memory={"ram_type": "DDR5"},
        ),
        report,
    )
    assert ddr4 is not None and ddr5 is not None
    assert ddr4.model != ddr5.model
    keys = {
        build_resolved_variant_from_fields(
            category=e.category,
            brand=e.brand,
            family=e.family,
            model=e.model,
            variant=e.variant,
            attributes=e.attributes,
        ).identity_key
        for e in (ddr4, ddr5)
    }
    assert len(keys) == 2
    # O nome sem o tipo de memória é igual nas duas: ambíguo, sai dos dois lados.
    kept = drop_ambiguous_codes([ddr4, ddr5], MappingReport())
    assert all("PRO B660M VC WIFI" not in entry.names for entry in kept)
    assert all(
        "PRO B660M VC WIFI DDR4" in e.names or "PRO B660M VC WIFI DDR5" in e.names
        for e in kept
    )


def test_ram_types_ddr3_to_ddr5_never_share_identity() -> None:
    entries = [
        map_buildcores_ram(
            _ram(f"r{n}", "Kit", f"DDR{n}", [f"KIT{n}ABC123"]), MappingReport()
        )
        for n in (3, 4, 5)
    ]
    assert all(entry is not None for entry in entries)
    keys = {
        build_resolved_variant_from_fields(
            category=e.category,
            brand=e.brand,
            family=e.family,
            model=e.model,
            variant=e.variant,
            attributes=e.attributes,
        ).identity_key
        for e in entries
    }
    assert len(keys) == 3
    assert [e.attributes["type"] for e in entries] == ["ddr3", "ddr4", "ddr5"]


def test_marketing_title_as_name_falls_back_to_short_human_name_or_is_skipped() -> None:
    long_name = (
        "ASUS Prime H570-PLUS LGA1200 Intel 11th 10th Gen ATX Motherboard PCIe 4.0 "
        "8 power stages HDMI DVI DisplayPort Dual M.2 Intel 1Gb LAN USB 3.2"
    )
    report = MappingReport()
    with_fallback = map_buildcores_motherboard(
        _board(
            "l1", long_name, "ASUS", part_numbers=["PRIME H570-PLUS", "90MB1670-M0EAY0"]
        ),
        report,
    )
    assert with_fallback is not None
    assert with_fallback.model == "prime-h570-plus"
    without = map_buildcores_motherboard(_board("l2", long_name, "ASUS"), report)
    assert without is None
    assert report.skipped["nome_longo_demais"] == 1


def test_record_without_manufacturer_is_skipped() -> None:
    report = MappingReport()
    record = _board("x", "B650M PRO")
    record["metadata"]["manufacturer"] = None
    assert map_buildcores_motherboard(record, report) is None
    assert report.skipped["sem_fabricante_nome_ou_id"] == 1


def test_ram_without_usable_part_number_is_skipped() -> None:
    report = MappingReport()
    assert map_buildcores_ram(_ram("r", "Kit", "DDR5", ["8", ""]), report) is None
    assert report.skipped["sem_part_number_utilizavel"] == 1


def test_a_part_number_shared_by_two_products_never_merges_them() -> None:
    """A fonte lista o part number de um modelo na lista de outro (12400 no 12400F):
    juntar os registros misturaria produtos diferentes. O código dividido sai dos DOIS
    e cada produto fica com o que é só dele."""
    plain = map_buildcores_motherboard(
        _board("a", "MSI B650M PRO AM5 DDR5 Micro ATX", part_numbers=["7D75-001R"]),
        MappingReport(),
    )
    variant = map_buildcores_motherboard(
        _board(
            "b",
            "MSI B650M PRO A AM5 DDR5 Micro ATX",
            part_numbers=["7D75-001R", "7D75-002R"],
        ),
        MappingReport(),
    )
    kept = drop_ambiguous_codes(merge_same_identity([plain, variant]), MappingReport())
    by_model = {entry.model: entry for entry in kept}
    assert set(by_model) == {"b650m-pro-ddr5", "b650m-pro-a-ddr5"}
    assert by_model["b650m-pro-ddr5"].part_numbers == ()
    assert by_model["b650m-pro-a-ddr5"].part_numbers == ("7D75002R",)


def test_other_brand_sharing_a_part_number_is_dropped_from_both() -> None:
    a = map_buildcores_motherboard(
        _board("a", "MSI MAG B650 TOMAHAWK WIFI", part_numbers=["SHARED123"]),
        MappingReport(),
    )
    b = map_buildcores_motherboard(
        _board("b", "ASUS PRIME B650M-A WIFI", "ASUS", part_numbers=["SHARED123"]),
        MappingReport(),
    )
    kept = drop_ambiguous_codes([a, b], MappingReport())
    assert all("SHARED123" not in entry.part_numbers for entry in kept)


# --- casamento estrito por nome (fontes abertas) ---------------------------


def _snapshot(model, names, part_numbers=(), source="buildcores"):
    built = build_resolved_variant_from_fields(
        category="motherboard",
        brand="msi",
        family="b650m",
        model=model,
        variant="base",
        attributes={"chipset": "b650"},
    )
    return CatalogEntrySnapshot(
        category="motherboard",
        brand="msi",
        family="b650m",
        model=model,
        variant="base",
        attributes={"chipset": "b650"},
        required_attributes=(),
        family_key=catalog_family_key(
            category="motherboard", brand="msi", family="b650m", model=model
        ),
        identity_key=built.identity_key,
        part_numbers=tuple(part_numbers),
        names=tuple(names),
        source=source,
    )


def test_open_source_name_matches_when_the_run_is_complete() -> None:
    catalog = (_snapshot("b650m-pro", ["B650M PRO"]),)
    assert match_catalog(catalog, "Placa-mae MSI B650M PRO AM5 DDR5 mATX") is not None
    assert match_catalog(catalog, "Placa Mae MSI B650M PRO") is not None
    assert match_catalog(catalog, "Placa Mae MSI B650M PRO, Socket AM5") is not None


def test_open_source_name_never_matches_a_longer_model_name() -> None:
    catalog = (_snapshot("b650m-pro", ["B650M PRO"]),)
    assert match_catalog(catalog, "Placa Mae MSI B650M PRO-A AM5") is None
    assert match_catalog(catalog, "Placa Mae MSI B650M PRO WIFI AM5") is None
    assert match_catalog(catalog, "Placa Mae MSI B650M PRO PLUS") is None


def test_longest_open_source_name_wins_over_the_shorter_one() -> None:
    catalog = (
        _snapshot("b650m-pro", ["B650M PRO"]),
        _snapshot("b650m-pro-wifi", ["B650M PRO WIFI"]),
    )
    resolved = match_catalog(catalog, "Placa Mae MSI B650M PRO WIFI AM5 DDR5")
    assert resolved is not None and resolved.model == "b650m-pro-wifi"


def test_non_open_source_entries_keep_the_old_loose_name_matching() -> None:
    catalog = (_snapshot("b650m-pro", ["B650M PRO"], source="learned"),)
    assert match_catalog(catalog, "Placa Mae MSI B650M PRO-A AM5") is not None


def test_part_number_match_is_exact_for_open_sources() -> None:
    catalog = (_snapshot("b650m-pro", ["B650M PRO"], part_numbers=["7D75001R"]),)
    resolved = match_catalog(catalog, "Placa Mae MSI 7D75-001R extra palavra qualquer")
    assert resolved is not None and resolved.model == "b650m-pro"


def _cpu_snapshot(model, names):
    built = build_resolved_variant_from_fields(
        category="cpu",
        brand="intel",
        family="core",
        model=model,
        variant="base",
        attributes={},
    )
    return CatalogEntrySnapshot(
        category="cpu",
        brand="intel",
        family="core",
        model=model,
        variant="base",
        attributes={},
        required_attributes=(),
        family_key=catalog_family_key(
            category="cpu", brand="intel", family="core", model=model
        ),
        identity_key=built.identity_key,
        part_numbers=(),
        names=tuple(names),
        source="buildcores",
    )


def test_spec_terms_after_the_model_are_accepted_but_variant_suffixes_are_not() -> None:
    plain = _cpu_snapshot("core-i5-12400", ["CORE I5 12400"])
    with_f = _cpu_snapshot("core-i5-12400f", ["CORE I5 12400F"])
    catalog = (plain, with_f)
    ok = match_catalog(
        catalog, "Processador Intel Core i5-12400 2.5GHz LGA 1700 Cache 18MB"
    )
    assert ok is not None and ok.model == "core-i5-12400"
    variant = match_catalog(catalog, "Processador Intel Core i5-12400F 2.5GHz LGA 1700")
    assert variant is not None and variant.model == "core-i5-12400f"
    # Sufixo de variante (K, KF, Pro, Plus, XT, WiFi) continua barrando o nome curto.
    assert match_catalog((plain,), "Processador Intel Core i5 12400 Plus") is None
    assert match_catalog((plain,), "Intel Core i5 12400 K") is None
    # Memória de vídeo e "sem fio" são especificação, não variante.
    gpu = _cpu_snapshot("arc-a310-elf-4gb", ["ARC A310 ELF 4GB"])
    assert (
        match_catalog((gpu,), "Placa de Video Sparkle Arc A310 ELF 4GB GDDR6")
        is not None
    )
    mouse = _cpu_snapshot("g304", ["G304 LIGHTSPEED"])
    assert (
        match_catalog((mouse,), "Mouse Logitech G304 Lightspeed Sem Fio Preto")
        is not None
    )
