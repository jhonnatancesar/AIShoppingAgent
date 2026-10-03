"""TASK-137 -- pedido por ESPECIFICAÇÃO: "quero uma b550", "memória ddr5 de 8gb".

O usuário quase nunca escreve o nome do produto; diz o que quer em poucas palavras.
A IA do pedido (`IntentInterpreter`) já devolve esse texto limpo (`search_query`/`model`);
aqui o código lê só as ESPECIFICAÇÕES que ele contém, usando o vocabulário que o próprio
catálogo conhece (chipsets e soquetes de placa-mãe) e padrões fechados (DDR, GB, MHz).
O que o usuário não disse fica "qualquer": o pedido vira um item de FAMÍLIA
(categoria + especificações), a coleta busca em linguagem natural e exige no título
todas as especificações pedidas (`app.collection.spec_terms`), e o funil escolhe os 1 ou 2
melhores para a IA confirmar.

Regras de segurança:

- só vira especificação se TODAS as palavras do pedido forem entendidas (palavra de
  enchimento, marca, categoria ou especificação). Sobrou palavra desconhecida ("gaming",
  "tomahawk"), o pedido é mais específico que isto e segue para o catálogo/dúvida;
- precisa de ao menos uma especificação que defina o produto (chipset ou soquete na
  placa-mãe; tipo ou capacidade na memória);
- categoria vem da palavra ("placa mãe", "memória") ou do próprio valor (b550 só existe
  como chipset de placa-mãe); especificações de categorias diferentes juntas não decidem.

Nunca usa IA.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from app.products.identity import (
    MonitoringIdentity,
    MonitoringScope,
    _build_monitoring_identity,
)
from app.products.identity_catalog import CatalogEntrySnapshot
from app.products.identity_catalog_request import KNOWN_BRANDS, _tokens

_FILLER = frozenset(
    {
        "QUERO", "QUERIA", "QRIA", "UM", "UMA", "UNS", "DE", "DA", "DO", "DAS", "DOS",
        "COM", "PARA", "O", "A", "AMD", "INTEL", "PLACA", "MAE", "MOTHERBOARD",
        "MAINBOARD", "MEMORIA", "MEMORIAS", "RAM", "POR", "FAVOR", "ATX",
    }
)  # fmt: skip
_FORM_WORDS = frozenset({"MATX", "MICRO", "MINI", "ITX"})
_GB_TOKEN = re.compile(r"^(\d{1,3})GB$")
_SPEED_TOKEN = re.compile(r"^(\d{4,5})(MHZ)?$")
_DDR_TOKEN = re.compile(r"^DDR([1-5])$")
_FORM_SUFFIX = {"M": "micro-atx", "I": "mini-itx"}
_MAX_GB = 512
_NOUN = {"motherboard": "placa mae", "ram": "memoria ram"}


def _merge_units(text: str) -> str:
    """ "8 gb" -> "8GB", "6000 mhz" -> "6000MHZ" (um token só)."""
    text = re.sub(r"(?<![A-Za-z0-9])(\d{1,3})\s*[Gg][Bb](?![A-Za-z])", r"\1GB", text)
    return re.sub(r"(?<![A-Za-z0-9])(\d{4,5})\s*[Mm][Hh][Zz]\b", r"\1MHZ", text)


@dataclass(frozen=True, slots=True)
class SpecVocabulary:
    chipsets: frozenset[str] = frozenset()
    sockets: dict[str, str] = field(default_factory=dict)  # "LGA1700" -> "lga-1700"
    brands: frozenset[str] = frozenset()


def build_vocabulary(catalog: Sequence[CatalogEntrySnapshot]) -> SpecVocabulary:
    chipsets: set[str] = set()
    sockets: dict[str, str] = {}
    brands: set[str] = set()
    for entry in catalog:
        brands.add(entry.brand.upper().replace("-", ""))
        if entry.category != "motherboard":
            continue
        chipset = str(entry.attributes.get("chipset", "")).upper()
        if chipset:
            chipsets.add(chipset)
        socket = str(entry.attributes.get("socket", ""))
        if socket:
            sockets[re.sub(r"[^A-Z0-9]", "", socket.upper())] = socket.lower()
    return SpecVocabulary(frozenset(chipsets), sockets, frozenset(brands))


@dataclass(frozen=True, slots=True)
class SpecRequest:
    category: str
    brand: str | None
    attributes: dict[str, str]  # nomes do registro de categorias
    terms: tuple[str, ...]
    query: str


def parse_spec_request(
    vocabulary: SpecVocabulary, request_texts: Sequence[str]
) -> SpecRequest | None:
    tokens: list[str] = []
    for text in request_texts:
        for token in _tokens(_merge_units(text or "")):
            if token not in tokens:
                tokens.append(token)
    if not tokens:
        return None
    words = set(tokens)
    board_cue = ("PLACA" in words and "MAE" in words) or bool(
        words & {"MOTHERBOARD", "MAINBOARD"}
    )
    ram_cue = bool(words & {"MEMORIA", "MEMORIAS", "RAM"})
    if board_cue and ram_cue:
        return None

    chipset = suffix = socket = ddr = brand = None
    form_factor: str | None = None
    if "MATX" in words or ("MICRO" in words and "ATX" in words):
        form_factor = "micro-atx"
    elif "ITX" in words:
        form_factor = "mini-itx"
    typed_chipset = typed_socket = ""
    capacity: int | None = None
    speed: str | None = None
    bare_speed: str | None = None
    brands = vocabulary.brands | KNOWN_BRANDS
    for token in tokens:
        if token in _FILLER or token in _FORM_WORDS:
            continue
        gb = _GB_TOKEN.match(token)
        ddr_match = _DDR_TOKEN.match(token)
        speed_match = _SPEED_TOKEN.match(token)
        if token in vocabulary.chipsets:
            chipset, typed_chipset = token, token
        elif (
            len(token) > 2
            and token[:-1] in vocabulary.chipsets
            and token[-1] in _FORM_SUFFIX
        ):
            chipset, suffix, typed_chipset = token[:-1], token[-1], token
        elif token in vocabulary.sockets:
            socket, typed_socket = vocabulary.sockets[token], token
        elif ddr_match:
            ddr = f"ddr{ddr_match.group(1)}"
        elif gb and 0 < int(gb.group(1)) <= _MAX_GB:
            capacity = int(gb.group(1))
        elif speed_match and 1000 <= int(speed_match.group(1)) <= 10000:
            if speed_match.group(2):
                speed = speed_match.group(1)
            else:
                bare_speed = speed_match.group(1)
        elif token in brands:
            brand = token.lower()
        else:
            return None  # palavra não entendida: mais específico que uma especificação
    if bare_speed is not None:
        if ram_cue or ddr or capacity:
            speed = speed or bare_speed
        else:
            return None

    has_board = chipset is not None or socket is not None
    if form_factor and not has_board:
        # Só o formato ("placa mae matx") é amplo demais para definir o pedido.
        return None
    has_ram = capacity is not None or speed is not None
    if has_board and has_ram:
        return None
    if has_board:
        if ram_cue:
            return None
        category = "motherboard"
    elif has_ram or (ddr and ram_cue):
        if board_cue:
            return None
        category = "ram"
    elif ddr and board_cue:
        category = "motherboard"
    else:
        return None

    attributes: dict[str, str] = {}
    terms: list[str] = []
    query = [_NOUN[category]]
    if brand:
        query.append(brand)
        terms.append(f"brand:{brand}")
    if category == "motherboard":
        if chipset:
            attributes["chipset"] = chipset.lower()
            query.append(typed_chipset.lower())
            if suffix:
                attributes["form_factor"] = _FORM_SUFFIX[suffix]
                terms.append(f"chipset_exact:{typed_chipset.lower()}")
            else:
                terms.append(f"chipset:{chipset.lower()}")
        if form_factor and "form_factor" not in attributes:
            attributes["form_factor"] = form_factor
            query.append("matx" if form_factor == "micro-atx" else "itx")
            terms.append(f"form_factor:{form_factor}")
        if socket:
            attributes["socket"] = socket
            query.append(typed_socket.lower())
            terms.append(f"socket:{socket}")
        if ddr:
            attributes["memory_type"] = ddr
            query.append(ddr)
            terms.append(f"memory_type:{ddr}")
    else:
        if ddr:
            attributes["type"] = ddr
            query.append(ddr)
            terms.append(f"type:{ddr}")
        if capacity is not None:
            attributes["capacity_gb"] = str(capacity)
            query.append(f"{capacity}gb")
            terms.append(f"capacity_gb:{capacity}")
        if speed:
            attributes["speed_mhz"] = speed
            query.append(speed)
            terms.append(f"speed_mhz:{speed}")
        if not (ddr or capacity is not None):
            return None  # só a velocidade não define a memória
    if not attributes:
        return None
    return SpecRequest(
        category=category,
        brand=brand,
        attributes=attributes,
        terms=tuple(terms),
        query=" ".join(query),
    )


def monitoring_identity_from_spec(
    catalog: Sequence[CatalogEntrySnapshot], request_texts: Sequence[str]
) -> MonitoringIdentity | None:
    """Identidade de FAMÍLIA do pedido por especificação, ou `None`."""
    spec = parse_spec_request(build_vocabulary(catalog), request_texts)
    if spec is None:
        return None
    identity = _build_monitoring_identity(
        scope=MonitoringScope.FAMILY,
        category=spec.category,
        brand=spec.brand or "any",
        family="spec",
        model="any",
        variant=None,
        attribute_values=spec.attributes,
        aliases=MappingProxyType({}),
    )
    if identity is None:
        return None
    return replace(
        identity, collection_search=(spec.query, None), collection_terms=spec.terms
    )
