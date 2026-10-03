"""TASK-137 -- termos obrigatórios do título para pedidos por ESPECIFICAÇÃO.

O usuário não escreve o nome do produto: ele diz "quero uma b550" ou "memória ddr5
de 8gb". Esses pedidos viram um item de família (categoria + especificações) e a
coleta só aceita títulos que cumprem TODAS as especificações pedidas. Cada termo é
`tipo:valor`; a avaliação é determinística, sem IA, e tolera o jeito que as lojas
escrevem (B550M é uma placa B550; "16GB (2x8GB)" NÃO é uma memória de 8 GB).

Tipos:

- `chipset:b550` -- B550 e B550M/B550I, mas nunca B550E nem B5500 (outro chipset);
- `chipset_exact:b650m` -- exatamente B650M (o usuário pediu o formato);
- `socket:am5` / `socket:lga-1700`;
- `form_factor:micro-atx` / `form_factor:mini-itx` ("ATX" sozinho não filtra);
- `type:ddr5` e `memory_type:ddr5` -- o tipo de memória, sem confundir com LPDDR5;
- `capacity_gb:8` -- capacidade TOTAL do kit (2x8GB = 16, não 8);
- `speed_mhz:6000`;
- `brand:msi`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_GB = re.compile(r"(?<![A-Z0-9])(\d{1,3})\s*GB(?![A-Z])")
_KIT = re.compile(r"(?<![A-Z0-9])(\d{1,2})\s*X\s*(\d{1,3})\s*GB(?![A-Z])")
_MAX_MEMORY_GB = 256


def _normalize(title: str) -> str:
    decomposed = unicodedata.normalize("NFKD", title)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[\s_]+", " ", plain.upper()).strip()


def _word(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Z0-9]){pattern}(?![A-Z0-9])")


def total_memory_gb(normalized_title: str) -> int | None:
    """Capacidade total anunciada: `2x8GB` -> 16; senão o maior valor em GB
    (até 256; números maiores são disco, ex.: SSD de 512GB). `None` quando o título não diz."""
    kit = _KIT.search(normalized_title)
    if kit:
        return int(kit.group(1)) * int(kit.group(2))
    values = [
        int(value)
        for value in _GB.findall(normalized_title)
        if 0 < int(value) <= _MAX_MEMORY_GB
    ]
    return max(values) if values else None


def title_satisfies(term: str, title: str) -> bool:
    kind, _, value = term.partition(":")
    text = _normalize(title)
    if kind == "chipset":
        return bool(_word(rf"{re.escape(value.upper())}[MI]?").search(text))
    if kind == "chipset_exact":
        return bool(_word(re.escape(value.upper())).search(text))
    if kind == "form_factor":
        if value == "micro-atx":
            return bool(_word(r"(?:M|MICRO)[ -]?ATX").search(text))
        if value == "mini-itx":
            return bool(_word(r"(?:MINI[ -]?)?ITX").search(text))
        return True
    if kind == "socket":
        if value.lower().startswith("lga-"):
            return bool(_word(rf"LGA[ -]?{re.escape(value[4:])}").search(text))
        return bool(_word(re.escape(value.upper())).search(text))
    if kind in {"type", "memory_type"}:
        return bool(_word(re.escape(value.upper())).search(text))
    if kind == "capacity_gb":
        return value.isdigit() and total_memory_gb(text) == int(value)
    if kind == "speed_mhz":
        return bool(re.search(rf"(?<![0-9]){re.escape(value)}(?![0-9])", text))
    if kind == "brand":
        return bool(_word(re.escape(value.upper())).search(text))
    # Tipo desconhecido: não filtra por engano (fail-open só para o filtro).
    return True


def title_satisfies_all(terms: Iterable[str], title: str) -> bool:
    return all(title_satisfies(term, title) for term in terms)
