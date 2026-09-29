"""TASK-132 (Parte B) -- pré-lista do catálogo: produtos que mais saem no varejo
BR, de várias categorias, para o GG resolvê-los pelo catálogo (sem IA) desde o
primeiro título. Não é lista de "só celulares": celular é a categoria onde a
capacidade decide o produto (`required_attributes`), o resto entra com
identidade completa.

Convenções (as mesmas das identidades já gravadas):
- slugs em minúsculas com hífen; `variant="base"` quando não há edição;
- família/modelo no estilo que a IA e os extratores já usam (ex.:
  `intel/core-ultra/5-225f`, `samsung/galaxy-a/a56`, `apple/iphone/16` +
  variante `e`, que o extrator regex não lê);
- só entra produto com nome sem ambiguidade. Placa-mãe, memória, SSD e afins
  não entram por nome: entram sozinhos pelo part number, quando a resolução
  aprova uma identidade que o traz (`identity_catalog.learn_catalog_entry`);
- o nome de cada entrada é o texto que precisa aparecer no título (tokens
  exatos). Edição ("PRO", "PRO+", "XT") tem entrada própria; a guarda de
  palavras de edição impede a entrada base de casar um título de edição.
"""

from dataclasses import dataclass

_STORAGE = ("storage_gb",)


@dataclass(frozen=True, slots=True)
class SeedEntry:
    category: str
    brand: str
    family: str
    model: str
    names: tuple[str, ...]
    variant: str = "base"
    required_attributes: tuple[str, ...] = ()


def _phones(
    brand: str, family: str, models: tuple[str, ...], name_prefix: str
) -> list[SeedEntry]:
    return [
        SeedEntry(
            "smartphone",
            brand,
            family,
            model,
            (f"{name_prefix} {model.upper()}",),
            required_attributes=_STORAGE,
        )
        for model in models
    ]


def _with_editions(
    brand: str, family: str, models: tuple[str, ...], name_prefix: str
) -> list[SeedEntry]:
    entries: list[SeedEntry] = []
    for model in models:
        base = f"{name_prefix} {model}"
        entries += [
            SeedEntry(
                "smartphone",
                brand,
                family,
                model,
                (base,),
                required_attributes=_STORAGE,
            ),
            SeedEntry(
                "smartphone",
                brand,
                family,
                model,
                (f"{base} PRO",),
                variant="pro",
                required_attributes=_STORAGE,
            ),
            SeedEntry(
                "smartphone",
                brand,
                family,
                model,
                (f"{base} PRO+", f"{base} PRO PLUS"),
                variant="pro-plus",
                required_attributes=_STORAGE,
            ),
        ]
    return entries


def _cpu_ultra(tier: str, code: str) -> SeedEntry:
    return SeedEntry(
        "cpu",
        "intel",
        "core-ultra",
        f"{tier}-{code.lower()}",
        (f"CORE ULTRA {tier} {code.upper()}",),
    )


def _radeon(number: str, suffix: str | None = None) -> SeedEntry:
    model = f"{number}-{suffix.lower()}" if suffix else number
    name = f"RX {number} {suffix.upper()}" if suffix else f"RX {number}"
    return SeedEntry("gpu", "amd", "radeon-rx", model, (name,))


SEED_ENTRIES: tuple[SeedEntry, ...] = (
    # --- celulares (capacidade obrigatória, como no extrator de iPhone) -----
    SeedEntry(
        "smartphone",
        "apple",
        "iphone",
        "16",
        ("IPHONE 16E",),
        variant="e",
        required_attributes=_STORAGE,
    ),
    *_phones(
        "samsung",
        "galaxy-a",
        ("a06", "a15", "a16", "a26", "a35", "a36", "a55", "a56"),
        "GALAXY",
    ),
    *_phones("samsung", "galaxy-m", ("m15", "m35", "m55"), "GALAXY"),
    SeedEntry(
        "smartphone",
        "samsung",
        "galaxy-z",
        "flip6",
        ("GALAXY Z FLIP6", "GALAXY Z FLIP 6"),
        required_attributes=_STORAGE,
    ),
    SeedEntry(
        "smartphone",
        "samsung",
        "galaxy-z",
        "fold6",
        ("GALAXY Z FOLD6", "GALAXY Z FOLD 6"),
        required_attributes=_STORAGE,
    ),
    SeedEntry(
        "smartphone",
        "samsung",
        "galaxy-z",
        "flip7",
        ("GALAXY Z FLIP7", "GALAXY Z FLIP 7"),
        required_attributes=_STORAGE,
    ),
    SeedEntry(
        "smartphone",
        "samsung",
        "galaxy-z",
        "fold7",
        ("GALAXY Z FOLD7", "GALAXY Z FOLD 7"),
        required_attributes=_STORAGE,
    ),
    *_with_editions("xiaomi", "redmi-note", ("13", "14"), "REDMI NOTE"),
    *_phones("xiaomi", "redmi", ("13c", "14c", "a3", "a5"), "REDMI"),
    SeedEntry(
        "smartphone",
        "xiaomi",
        "poco",
        "x6",
        ("POCO X6",),
        required_attributes=_STORAGE,
    ),
    SeedEntry(
        "smartphone",
        "xiaomi",
        "poco",
        "x6",
        ("POCO X6 PRO",),
        variant="pro",
        required_attributes=_STORAGE,
    ),
    SeedEntry(
        "smartphone",
        "xiaomi",
        "poco",
        "x7",
        ("POCO X7",),
        required_attributes=_STORAGE,
    ),
    SeedEntry(
        "smartphone",
        "xiaomi",
        "poco",
        "x7",
        ("POCO X7 PRO",),
        variant="pro",
        required_attributes=_STORAGE,
    ),
    *_phones("motorola", "moto-g", ("g05", "g15", "g35", "g55", "g75", "g85"), "MOTO"),
    # --- processadores Intel Core Ultra (o extrator regex só lê Core i-*) ---
    _cpu_ultra("5", "225"),
    _cpu_ultra("5", "225F"),
    _cpu_ultra("5", "245K"),
    _cpu_ultra("5", "245KF"),
    _cpu_ultra("7", "265K"),
    _cpu_ultra("7", "265KF"),
    _cpu_ultra("9", "285K"),
    # --- placas de vídeo (o extrator regex só lê NVIDIA GeForce RTX) --------
    _radeon("7600"),
    _radeon("7600", "xt"),
    _radeon("7700", "xt"),
    _radeon("7800", "xt"),
    _radeon("7900", "gre"),
    _radeon("7900", "xt"),
    _radeon("7900", "xtx"),
    _radeon("9060", "xt"),
    _radeon("9070"),
    _radeon("9070", "xt"),
    SeedEntry("gpu", "intel", "arc", "b570", ("ARC B570",)),
    SeedEntry("gpu", "intel", "arc", "b580", ("ARC B580",)),
)
