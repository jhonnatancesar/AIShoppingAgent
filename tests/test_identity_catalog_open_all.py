"""TASK-137 -- mapeamento geral do BuildCores (todas as categorias)."""

from app.products.identity_catalog_import import (
    MappingReport,
    drop_ambiguous_codes,
    merge_same_identity,
)
from app.products.identity_catalog_open_all import FOLDER_SPECS, map_buildcores_record


def _record(name, manufacturer, *, pns=(), series="", variant="", **fields):
    return {
        "opendb_id": f"id-{name}",
        "metadata": {
            "name": name,
            "manufacturer": manufacturer,
            "part_numbers": list(pns),
            "series": series,
            "variant": variant,
        },
        **fields,
    }


def _map(folder, record):
    return map_buildcores_record(folder, record, MappingReport())


def test_every_buildcores_folder_except_os_is_covered() -> None:
    expected = {
        "Accessory", "CPU", "CPUCooler", "CaptureCard", "CaseFan", "Chair", "Desk", "GPU",
        "Headphones", "Keyboard", "Laptop", "Lighting", "Microphone", "Monitor", "Mouse",
        "Mousepad", "NetworkCard", "PCCase", "PSU", "PrebuiltDesktop", "SoundCard",
        "Speaker", "Stand", "Storage", "ThermalCompound", "VRHeadset", "Webcam",
    }  # fmt: skip
    assert (
        set(FOLDER_SPECS) == expected
    )  # Motherboard/RAM têm mapeador próprio; OS fora


def test_amd_and_intel_gpus_carry_vendor_chipset_vram_and_board_brand() -> None:
    amd = _map(
        "GPU",
        _record(
            "XFX Speedster QICK 319 Core Radeon RX 6700 XT 12GB GDDR6 Black",
            "XFX",
            pns=["RX-67XTYLUDP"],
            series="Radeon RX 6700 XT",
            chipset_manufacturer="AMD",
            chipset="Radeon RX 6700 XT",
            memory=12,
            memory_type="GDDR6",
        ),
    )
    intel = _map(
        "GPU",
        _record(
            "Sparkle Arc A310 ELF 4GB GDDR6 Blue / Black",
            "Sparkle",
            pns=["SA310C-4G"],
            series="Arc A310",
            chipset_manufacturer="Intel",
            chipset="Arc A310",
            memory=4,
            memory_type="GDDR6",
        ),
    )
    assert amd is not None and intel is not None
    assert amd.category == intel.category == "gpu"
    assert amd.attributes == {
        "gpu_vendor": "amd",
        "chipset": "radeon-rx-6700-xt",
        "vram": "12",
        "memory_type": "gddr6",
        "board_brand": "xfx",
    }
    assert intel.attributes["gpu_vendor"] == "intel"
    assert intel.attributes["chipset"] == "arc-a310"
    # Cor e memória no fim do nome não entram no nome curto.
    assert "SPEEDSTER QICK 319 CORE RADEON RX 6700 XT 12GB" in amd.names


def test_cpu_core_ultra_and_ryzen_keep_socket_cores_and_series() -> None:
    ultra = _map(
        "CPU",
        _record(
            "Intel Core Ultra 7 265K",
            "Intel",
            pns=["BX80768265K"],
            series="Core Ultra 7 200",
            socket="LGA 1851",
            cores={"total": 20, "threads": 20},
        ),
    )
    ryzen = _map(
        "CPU",
        _record(
            "AMD Ryzen 7 7800X3D",
            "AMD",
            pns=["100-100000910WOF"],
            series="Ryzen 7 7000",
            socket="AM5",
            cores={"total": 8, "threads": 16},
        ),
    )
    assert ultra is not None and ryzen is not None
    assert (ultra.brand, ultra.model) == ("intel", "core-ultra-7-265k")
    assert ultra.attributes == {
        "socket": "lga-1851",
        "cores": "20",
        "series": "core-ultra-7-200",
    }
    assert "CORE ULTRA 7 265K" in ultra.names
    assert ryzen.model == "ryzen-7-7800x3d"


def test_storage_is_split_into_ssd_and_hdd_with_capacity_and_interface() -> None:
    ssd = _map(
        "Storage",
        _record(
            "Lexar NM790 w/Heatsink 1TB SSD M.2-2280 PCIe 4.0 NVMe",
            "Lexar",
            pns=["LNM790X001T-RN9NU"],
            storage_type="SSD",
            capacity=1000,
            interface="M.2 PCIe 4.0 x4",
            form_factor="M.2-2280",
            nvme=True,
        ),
    )
    hdd = _map(
        "Storage",
        _record(
            'Seagate SkyHawk 10TB HDD 3.5" SATA',
            "Seagate",
            pns=["ST10000VX0004"],
            storage_type="HDD",
            capacity=10000,
            interface="SATA 6.0 Gb/s",
        ),
    )
    unknown = _map(
        "Storage", _record("Foo Bar 1TB", "Foo", pns=["FOOBAR123"], storage_type=None)
    )
    assert ssd.category == "ssd" and ssd.attributes["capacity_gb"] == "1000"
    assert ssd.attributes["nvme"] == "true"
    assert hdd.category == "hdd" and hdd.attributes["capacity_gb"] == "10000"
    assert unknown is None


def test_monitor_psu_and_cooler_attributes_use_the_category_registry_names() -> None:
    monitor = _map(
        "Monitor",
        _record(
            "Sceptre E275W-FP100T 100Hz IPS Monitor",
            "Sceptre",
            pns=["E275W-FP100T.A1"],
            screen_size=27,
            resolution={"horizontalRes": 2560, "verticalRes": 1440},
            refresh_rate=100,
            panel_type="IPS",
        ),
    )
    psu = _map(
        "PSU",
        _record(
            "EVGA SuperNOVA 1000 G+ 1000 W 80+ Gold Certified Fully Modular ATX",
            "EVGA",
            pns=["120-GP-1000-X1"],
            wattage=1000,
            efficiency_rating="80+ Gold",
            modular="Full",
        ),
    )
    water = _map(
        "CPUCooler",
        _record(
            "GameMax Iceberg Water 240mm",
            "GameMax",
            pns=["ICEBERG240"],
            water_cooled=True,
        ),
    )
    assert monitor.attributes == {
        "size": "27",
        "resolution": "2560x1440",
        "refresh_rate": "100",
        "panel": "ips",
    }
    assert psu.attributes["wattage"] == "1000"
    assert psu.attributes["certification"] == "80-gold"
    assert water.attributes == {"type": "water"}


def test_long_case_name_is_cut_at_the_form_factor_description() -> None:
    entry = _map(
        "PCCase",
        _record(
            "Corsair Carbide Series Air 540 ATX Mid Tower White / Black with Acrylic Side Panel",
            "Corsair",
            pns=["CC-9011048-WW"],
            series="Carbide",
        ),
    )
    assert entry is not None
    assert entry.model == "carbide-series-air-540"


def test_marketing_title_falls_back_to_series_and_variant_or_is_skipped() -> None:
    long_title = " ".join(f"Palavra{n}" for n in range(20))
    with_series = _map(
        "Keyboard",
        _record(long_title, "Acme", pns=[""], series="K70", variant="Pro 2"),
    )
    assert with_series is not None and with_series.model == "k70-pro-2"
    report = MappingReport()
    assert (
        map_buildcores_record("Keyboard", _record(long_title, "Acme", pns=[""]), report)
        is None
    )
    assert report.skipped["nome_longo_demais"] == 1


def test_records_without_manufacturer_or_usable_code_are_skipped() -> None:
    report = MappingReport()
    no_brand = _record("Algo X1", "")
    assert map_buildcores_record("Mouse", no_brand, report) is None
    weak = _record("Mouse", "Acme")  # nome de uma palavra sem código e sem part number
    assert map_buildcores_record("Mouse", weak, report) is None
    assert report.skipped["sem_fabricante_nome_ou_id"] == 1
    assert report.skipped["sem_codigo_utilizavel"] == 1


def test_color_variants_of_one_model_become_one_entry_with_every_part_number() -> None:
    black = _map(
        "Mouse",
        _record(
            "Logitech G304 Lightspeed Black",
            "Logitech",
            pns=["910-005281"],
            series="G304",
        ),
    )
    white = _map(
        "Mouse",
        _record(
            "Logitech G304 Lightspeed White",
            "Logitech",
            pns=["910-005283"],
            series="G304",
        ),
    )
    kept = drop_ambiguous_codes(merge_same_identity([black, white]), MappingReport())
    # A cor não faz parte da identidade (as lojas raramente a escrevem no título):
    # uma entrada, com os part numbers das duas cores e o nome sem cor.
    assert len(kept) == 1
    assert set(kept[0].part_numbers) == {"910005281", "910005283"}
    assert "G304 LIGHTSPEED" in kept[0].names


# --- Wikidata: celulares ---------------------------------------------------------


def _phone(label, manufacturer, aliases=()):
    from app.products.identity_catalog_open_all import map_wikidata_device

    return map_wikidata_device(
        "smartphone",
        {
            "qid": f"Q-{label}",
            "label": label,
            "manufacturer": manufacturer,
            "aliases": list(aliases),
        },
        MappingReport(),
    )


def test_phone_from_wikidata_needs_storage_and_drops_the_brand_from_the_name() -> None:
    entry = _phone("Samsung Galaxy A55 5G", "Samsung Electronics", ["Galaxy A55"])
    assert entry is not None
    assert (entry.category, entry.brand) == ("smartphone", "samsung")
    assert entry.required_attributes == ("storage_gb",)
    assert entry.source == "wikidata" and entry.source_ref == "Q-Samsung Galaxy A55 5G"
    assert set(entry.names) == {"GALAXY A55 5G", "GALAXY A55"}


def test_phone_without_manufacturer_uses_the_label_hint_or_is_skipped() -> None:
    assert _phone("Redmi Note 14", None).brand == "xiaomi"
    assert _phone("iPhone 16", None).brand == "apple"
    report = MappingReport()
    from app.products.identity_catalog_open_all import map_wikidata_device

    assert (
        map_wikidata_device("smartphone", {"qid": "Q1", "label": "Foo 12"}, report)
        is None
    )
    assert report.skipped["sem_fabricante"] == 1


def test_phone_title_with_storage_color_and_network_matches_but_a_longer_model_does_not() -> (
    None
):
    from app.products.identity import (
        build_resolved_variant_from_fields,
        catalog_family_key,
    )
    from app.products.identity_catalog import CatalogEntrySnapshot, match_catalog

    entry = _phone("Redmi Note 14", "Xiaomi")
    snapshot = CatalogEntrySnapshot(
        category=entry.category,
        brand=entry.brand,
        family=entry.family,
        model=entry.model,
        variant="base",
        attributes={},
        required_attributes=entry.required_attributes,
        family_key=catalog_family_key(
            category=entry.category,
            brand=entry.brand,
            family=entry.family,
            model=entry.model,
        ),
        identity_key=None,
        part_numbers=(),
        names=entry.names,
        source="wikidata",
    )
    assert (
        build_resolved_variant_from_fields
    )  # import usado só para o mesmo vocabulário
    ok = match_catalog((snapshot,), "Smartphone Xiaomi Redmi Note 14 256GB 5G Preto")
    assert ok is not None and ok.model == "redmi-note-14"
    assert dict(ok.attributes)["storage_gb"] == "256"
    assert match_catalog((snapshot,), "Xiaomi Redmi Note 14 Pro 256GB") is None
    assert match_catalog((snapshot,), "Xiaomi Redmi Note 14 Pro+ 5G 256GB") is None
    # Sem capacidade no título, não decide (cai na IA, como o extrator).
    assert match_catalog((snapshot,), "Xiaomi Redmi Note 14 Preto") is None
