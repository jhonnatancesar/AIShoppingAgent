"""TASK-137 -- importação do BuildCores OpenDB INTEIRO (todas as categorias).

Complementa `identity_catalog_import` (que mapeia placa-mãe e RAM com regras próprias):
aqui cada pasta do repositório tem uma entrada em `FOLDER_SPECS` com a categoria do
GG e os atributos que definem o produto (os mesmos nomes do registro de categorias de
`identity.py`, quando existem). O resto do caminho é o mesmo: nome limpo sem fabricante
e sem cor/sufixo de embalagem, part numbers de verdade, casamento estrito, códigos
ambíguos descartados dos dois lados.

Atribuição: BuildCores OpenDB, ODC-By 1.0 (ver rodapé "Fontes de dados").
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.products.identity import normalize_for_grounding
from app.products.identity_catalog_import import (
    SOURCE_BUILDCORES,
    MappingReport,
    OpenCatalogEntry,
    _slug,
    _split_codes,
)

_COLORS = frozenset(
    {
        "BLACK", "WHITE", "GRAY", "GREY", "SILVER", "RED", "BLUE", "GREEN", "PINK",
        "PURPLE", "YELLOW", "ORANGE", "BROWN", "BEIGE", "GOLD", "TRANSPARENT", "CLEAR",
        "CREAM", "NAVY", "TEAL", "TITANIUM", "GUNMETAL", "CHARCOAL", "IVORY", "/", "&",
        "+", "-",
    }
)  # fmt: skip
_TRAILING_PACKAGING = re.compile(r"^(?:GDDR\d+X?|HBM\d?E?)$", re.IGNORECASE)
_MAX_NAME_TOKENS = 12


def _text(value: Any) -> str:
    return (
        _slug(str(value))
        if isinstance(value, (str, int, float)) and value != ""
        else ""
    )


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _clean(attributes: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in attributes.items() if value}


def _cpu(record: Mapping[str, Any]) -> dict[str, str]:
    cores = record.get("cores") if isinstance(record.get("cores"), dict) else {}
    return _clean(
        {
            "socket": _text(record.get("socket")),
            "cores": _number(cores.get("total")),
            "series": _text((record.get("metadata") or {}).get("series")),
        }
    )


def _gpu(record: Mapping[str, Any]) -> dict[str, str]:
    meta = record.get("metadata") or {}
    return _clean(
        {
            "gpu_vendor": _text(record.get("chipset_manufacturer")),
            "chipset": _text(record.get("chipset")),
            "vram": _number(record.get("memory")),
            "memory_type": _text(record.get("memory_type")),
            "board_brand": _slug(meta.get("manufacturer") or ""),
        }
    )


def _storage(record: Mapping[str, Any]) -> dict[str, str]:
    return _clean(
        {
            "capacity_gb": _number(record.get("capacity")),
            "interface": _text(record.get("interface")),
            "form_factor": _text(record.get("form_factor")),
            "nvme": "true" if record.get("nvme") is True else "",
        }
    )


def _psu(record: Mapping[str, Any]) -> dict[str, str]:
    return _clean(
        {
            "wattage": _number(record.get("wattage")),
            "certification": _text(record.get("efficiency_rating")),
            "form_factor": _text(record.get("form_factor")),
            "modular": _text(record.get("modular")),
        }
    )


def _monitor(record: Mapping[str, Any]) -> dict[str, str]:
    resolution = record.get("resolution")
    shown = ""
    if isinstance(resolution, dict):
        horizontal, vertical = (
            resolution.get("horizontalRes"),
            resolution.get("verticalRes"),
        )
        if isinstance(horizontal, int) and isinstance(vertical, int):
            shown = f"{horizontal}x{vertical}"
    return _clean(
        {
            "size": _number(record.get("screen_size")),
            "resolution": shown,
            "refresh_rate": _number(record.get("refresh_rate")),
            "panel": _text(record.get("panel_type")),
        }
    )


def _case(record: Mapping[str, Any]) -> dict[str, str]:
    return _clean({"form_factor": _text(record.get("form_factor"))})


def _cooler(record: Mapping[str, Any]) -> dict[str, str]:
    return _clean({"type": "water" if record.get("water_cooled") is True else "air"})


def _none(_record: Mapping[str, Any]) -> dict[str, str]:
    return {}


def _storage_category(record: Mapping[str, Any]) -> str | None:
    kind = str(record.get("storage_type") or "").upper()
    return {"SSD": "ssd", "HDD": "hdd", "SSHD": "hdd"}.get(kind)


@dataclass(frozen=True, slots=True)
class FolderSpec:
    category: str | Callable[[Mapping[str, Any]], str | None]
    attributes: Callable[[Mapping[str, Any]], dict[str, str]] = _none
    cut_words: frozenset[str] = frozenset()
    use_series_name: bool = True
    """A `series` da fonte vira nome extra (nas peças em que ela é o modelo: fonte, monitor,
    mouse...). CPU e GPU não: lá a série é a linha do chip e casaria com todos."""
    """Palavra a partir da qual o nome é descrição, não modelo (gabinete: "... Air 540
    ATX Mid Tower White with Tempered Glass" -> "... Air 540")."""


FOLDER_SPECS: dict[str, FolderSpec] = {
    "CPU": FolderSpec("cpu", _cpu, use_series_name=False),
    "GPU": FolderSpec("gpu", _gpu, use_series_name=False),
    "Storage": FolderSpec(_storage_category, _storage),
    "PSU": FolderSpec("psu", _psu),
    "Monitor": FolderSpec("monitor", _monitor),
    "PCCase": FolderSpec(
        "case",
        _case,
        cut_words=frozenset(
            {
                "ATX",
                "MICRO",
                "MINI",
                "EATX",
                "MID",
                "FULL",
                "TOWER",
                "SFF",
                "CUBE",
                "MATX",
            }
        ),
    ),
    "CPUCooler": FolderSpec("cooler", _cooler),
    "CaseFan": FolderSpec("case-fan"),
    "Laptop": FolderSpec("notebook"),
    "PrebuiltDesktop": FolderSpec("desktop"),
    "Keyboard": FolderSpec("keyboard"),
    "Mouse": FolderSpec("mouse"),
    "Mousepad": FolderSpec("mousepad"),
    "Headphones": FolderSpec("headphones"),
    "Microphone": FolderSpec("microphone"),
    "Speaker": FolderSpec("speaker"),
    "Webcam": FolderSpec("webcam"),
    "Chair": FolderSpec("chair"),
    "Desk": FolderSpec("desk"),
    "NetworkCard": FolderSpec("network-card"),
    "SoundCard": FolderSpec("sound-card"),
    "CaptureCard": FolderSpec("capture-card"),
    "ThermalCompound": FolderSpec("thermal-paste"),
    "Lighting": FolderSpec("lighting"),
    "Accessory": FolderSpec("accessory"),
    "Stand": FolderSpec("stand"),
    "VRHeadset": FolderSpec("vr-headset"),
}
"""Pastas tratadas à parte: `Motherboard` e `RAM` (`identity_catalog_import`) e `OS`
(sem produto físico; fora)."""


def _clean_name(name: str, manufacturer: str) -> str:
    tokens = name.replace(",", " ").split()
    brand_tokens = normalize_for_grounding(manufacturer).split()
    size = len(brand_tokens)
    if (
        size
        and len(tokens) > size
        and normalize_for_grounding(" ".join(tokens[:size])).split() == brand_tokens
    ):
        tokens = tokens[size:]
    return " ".join(tokens)


def _strip_trailing_noise(name: str) -> str:
    tokens = name.split()
    while tokens and (
        tokens[-1].upper().strip(",;") in _COLORS
        or _TRAILING_PACKAGING.match(tokens[-1].strip(",;"))
    ):
        tokens.pop()
    return " ".join(tokens)


def _is_code_like(token: str) -> bool:
    return (
        len(token) >= 5
        and any(c.isdigit() for c in token)
        and any(c.isalpha() for c in token)
    )


def _usable_name(normalized: str) -> bool:
    tokens = normalized.split()
    if not tokens or len(tokens) > _MAX_NAME_TOKENS:
        return False
    if len(tokens) >= 2:
        return any(any(char.isdigit() for char in token) for token in tokens)
    return _is_code_like(tokens[0])


def map_buildcores_record(
    folder: str, record: Mapping[str, Any], report: MappingReport
) -> OpenCatalogEntry | None:
    spec = FOLDER_SPECS[folder]
    report.read += 1
    meta = record.get("metadata") or {}
    manufacturer = (meta.get("manufacturer") or "").strip()
    name = (meta.get("name") or "").strip()
    opendb_id = str(record.get("opendb_id") or "").strip()
    if not manufacturer or not name or not opendb_id:
        report.skip("sem_fabricante_nome_ou_id")
        return None
    category = spec.category(record) if callable(spec.category) else spec.category
    if not category:
        report.skip("categoria_indefinida")
        return None
    cleaned = _clean_name(name, manufacturer)
    if spec.cut_words:
        kept: list[str] = []
        for token in cleaned.split():
            if token.upper().strip(",;/") in spec.cut_words and kept:
                break
            kept.append(token)
        cleaned = " ".join(kept)
    trimmed = _strip_trailing_noise(cleaned)
    part_numbers, extra_names = _split_codes(meta.get("part_numbers") or [])
    if len(trimmed.split()) > _MAX_NAME_TOKENS:
        series_variant = normalize_for_grounding(
            f"{meta.get('series') or ''} {meta.get('variant') or ''}"
        )
        short = sorted(
            (c for c in (*extra_names, series_variant) if _usable_name(c)),
            key=lambda c: (len(c.split()), c),
        )
        if not short:
            report.skip("nome_longo_demais")
            return None
        cleaned = trimmed = short[0]
    model = _slug(trimmed or cleaned)
    if not model:
        report.skip("nome_vazio")
        return None
    series_name = (
        normalize_for_grounding(meta.get("series") or "")
        if spec.use_series_name
        else ""
    )
    names = tuple(
        dict.fromkeys(
            candidate
            for candidate in (
                normalize_for_grounding(cleaned),
                normalize_for_grounding(trimmed),
                series_name,
            )
            if _usable_name(candidate)
        )
    )
    if not part_numbers and not names:
        report.skip("sem_codigo_utilizavel")
        return None
    family = _slug(meta.get("series") or "") or _slug(manufacturer)
    report.mapped += 1
    return OpenCatalogEntry(
        category=category,
        brand=_slug(manufacturer),
        family=family,
        model=model,
        attributes=spec.attributes(record),
        part_numbers=part_numbers,
        names=names,
        source=SOURCE_BUILDCORES,
        source_ref=opendb_id,
    )


# --- Wikidata (CC0): celulares, smartwatches ---------------------------------

SOURCE_WIKIDATA = "wikidata"
_CORPORATE_WORDS = frozenset(
    {
        "ELECTRONICS", "COMMUNICATIONS", "MOBILE", "INC", "INC.", "CORPORATION", "CO",
        "CO.", "LTD", "LTD.", "GROUP", "TECHNOLOGY", "TECHNOLOGIES", "COMPANY", "LIMITED",
        "CORP", "CORP.", "INTERNATIONAL", "COMPUTER",
    }
)  # fmt: skip
_LABEL_BRAND_HINTS = {
    "IPHONE": "apple", "IPAD": "apple", "REDMI": "xiaomi", "POCO": "xiaomi",
    "GALAXY": "samsung", "PIXEL": "google", "MOTO": "motorola", "XPERIA": "sony",
    "ZENFONE": "asus", "ROG": "asus", "NOTHING": "nothing",
}  # fmt: skip
_WIKIDATA_CATEGORY = {"smartphone": "smartphone", "smartwatch": "smartwatch"}


def _wikidata_brand(manufacturer: str | None, label: str) -> str:
    if manufacturer:
        words = [
            w
            for w in normalize_for_grounding(manufacturer).split()
            if w not in _CORPORATE_WORDS
        ]
        if words:
            return _slug(" ".join(words))
    first = normalize_for_grounding(label).split()[:1]
    return _LABEL_BRAND_HINTS.get(first[0], "") if first else ""


def map_wikidata_device(
    kind: str, record: Mapping[str, Any], report: MappingReport
) -> OpenCatalogEntry | None:
    """`kind`: `smartphone` ou `smartwatch` (arquivos exportados do Wikidata, CC0).

    Celular: a capacidade decide o produto (`required_attributes=("storage_gb",)`, a
    identidade nasce do título, como na pré-lista da TASK-132). Os apelidos do Wikidata
    viram nomes extras; nome repetido em dois aparelhos sai dos dois lados."""
    report.read += 1
    label = (record.get("label") or "").strip()
    qid = str(record.get("qid") or "").strip()
    if not label or not qid:
        report.skip("sem_nome_ou_id")
        return None
    brand = _wikidata_brand(record.get("manufacturer"), label)
    if not brand:
        report.skip("sem_fabricante")
        return None
    brand_words = normalize_for_grounding(brand.replace("-", " ")).split()

    def strip_brand(text: str) -> str:
        tokens = text.split()
        size = len(brand_words)
        if (
            size
            and len(tokens) > size
            and normalize_for_grounding(" ".join(tokens[:size])).split() == brand_words
        ):
            tokens = tokens[size:]
        return " ".join(tokens)

    primary = strip_brand(label)
    names = tuple(
        dict.fromkeys(
            normalized
            for normalized in (
                normalize_for_grounding(strip_brand(value))
                for value in (label, primary, *record.get("aliases", ()))
            )
            if _usable_name(normalized)
        )
    )
    model = _slug(primary)
    if not names or not model:
        report.skip("nome_sem_modelo_identificavel")
        return None
    report.mapped += 1
    return OpenCatalogEntry(
        category=_WIKIDATA_CATEGORY[kind],
        brand=brand,
        family=_slug(" ".join(primary.split()[:2])) or brand,
        model=model,
        attributes={},
        part_numbers=(),
        names=names,
        source=SOURCE_WIKIDATA,
        source_ref=qid,
        required_attributes=("storage_gb",) if kind == "smartphone" else (),
    )
