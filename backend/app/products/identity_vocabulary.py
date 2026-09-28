"""TASK-129 -- vocabulário padronizado da extração de identidade por IA.

Achado real (dry-run da PROD, 2026-09-27): a IA escrevia categoria, marca e
família livremente a cada lote ("motherboard" num, "placa-mae" noutro;
marca "fury" num, "kingston" noutro), e o código gravava sem conferir -- o
mesmo produto virava dois. O registro de categorias (`app.products.identity.
_CATEGORY_REGISTRY`), as grafias já aprovadas no banco e a tabela de aliases
(`product_identity_aliases`) existiam, mas a extração não consultava nenhum.

Este módulo faz as duas pontas, sempre com o que já existe no projeto:
- `load_identity_vocabulary` + `render_vocabulary_prompt`: o que a IA recebe
  (categorias oficiais com o significado e os atributos de cada uma,
  categorias genéricas já em uso, marcas/famílias já aprovadas, aliases);
- `canonicalize_fields`: a trava no código, depois do grounding e antes de
  virar identidade -- sinônimo de categoria vira a oficial e alias ativo vira
  a grafia canônica, mesmo que a IA desobedeça o prompt.

Decisão do usuário (2026-09-27) para produto fora das categorias oficiais:
"cadeira gamer ou itens genéricos deve ser preenchido de forma genérica,
mas que seja padrão" -- a categoria genérica é o tipo do produto em
português ("cadeira gamer"), sem marca/modelo inventados, e uma categoria
genérica já usada é SEMPRE reaproveitada em vez de criar variação.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.products.identity import registered_categories
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.models import ProductIdentityAlias

# Significado de cada categoria oficial, em português -- a IA escolhe pelo
# significado, nunca pela palavra do título.
CATEGORY_MEANINGS: dict[str, str] = {
    "smartphone": "celular / smartphone",
    "cpu": "processador (CPU) avulso",
    "gpu": "placa de vídeo",
    "tablet": "tablet",
    "notebook": "notebook / laptop",
    "desktop": "computador de mesa montado (PC completo)",
    "monitor": "monitor",
    "tv": "televisão / smart TV",
    "ram": "memória RAM",
    "ssd": "SSD",
    "hdd": "HD (disco rígido)",
    "motherboard": "placa-mãe",
    "psu": "fonte de alimentação de PC",
    "case": "gabinete de PC",
    "cooler": "cooler / water cooler / refrigeração de processador",
    "keyboard": "teclado",
    "mouse": "mouse",
    "headset": "headset / fone de ouvido",
    "console": "videogame / console",
    "controller": "controle / joystick de videogame",
    "camera": "câmera / webcam",
    "router": "roteador / Wi-Fi",
    "printer": "impressora",
}

# Sinônimos que a IA costuma devolver -> categoria oficial (slug).
CATEGORY_SYNONYMS: dict[str, str] = {
    "celular": "smartphone",
    "telefone": "smartphone",
    "processador": "cpu",
    "placa-de-video": "gpu",
    "placa-grafica": "gpu",
    "video-card": "gpu",
    "graphics-card": "gpu",
    "laptop": "notebook",
    "pc": "desktop",
    "computador": "desktop",
    "pc-gamer": "desktop",
    "televisao": "tv",
    "televisor": "tv",
    "smart-tv": "tv",
    "memoria": "ram",
    "memoria-ram": "ram",
    "memory": "ram",
    "hd": "hdd",
    "disco-rigido": "hdd",
    "hard-disk": "hdd",
    "placa-mae": "motherboard",
    "mainboard": "motherboard",
    "fonte": "psu",
    "fonte-de-alimentacao": "psu",
    "power-supply": "psu",
    "gabinete": "case",
    "water-cooler": "cooler",
    "air-cooler": "cooler",
    "watercooler": "cooler",
    "teclado": "keyboard",
    "fone-de-ouvido": "headset",
    "headphone": "headset",
    "fone": "headset",
    "videogame": "console",
    "video-game": "console",
    "controle": "controller",
    "joystick": "controller",
    "gamepad": "controller",
    "camera": "camera",
    "webcam": "camera",
    "roteador": "router",
    "impressora": "printer",
}

_MAX_KNOWN_FAMILIES = 150


def category_slug(value: str) -> str:
    """Mesma forma do slug da identidade (minúsculas, sem acento, `-`)."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


@dataclass(frozen=True, slots=True)
class IdentityVocabulary:
    """Tudo que a IA precisa ver para não inventar grafia -- montado do
    registro (código) + banco. Vazio (`IdentityVocabulary()`) ainda guia
    pelas categorias oficiais e pelos sinônimos fixos."""

    generic_categories: tuple[str, ...] = ()
    """Categorias fora do registro já em uso (ex.: "cadeira-gamer")."""
    known_families: tuple[tuple[str, str, str], ...] = ()
    """(categoria, marca, família) já APROVADAS -- grafia a reaproveitar."""
    aliases: tuple[tuple[str, str, str, str], ...] = ()
    """(categoria ou "*", campo, grafia alternativa, grafia oficial) ativos."""
    _alias_index: dict[tuple[str, str, str], str] = field(
        default_factory=dict, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        index: dict[tuple[str, str, str], str] = {}
        for category, attribute, raw, canonical in self.aliases:
            index[(category_slug(category) or "*", attribute, category_slug(raw))] = (
                category_slug(canonical)
            )
        object.__setattr__(self, "_alias_index", index)

    def _alias(self, category: str, attribute: str, value: str) -> str | None:
        key = category_slug(value)
        exact = self._alias_index.get(
            (category, attribute, key)
        ) or self._alias_index.get(("*", attribute, key))
        return exact or self._alias_prefix(category, attribute, key)

    def _alias_prefix(self, category: str, attribute: str, key: str) -> str | None:
        """TASK-130: a IA escreve a linha inteira no campo (ex.: brand=
        "fury-beast"), e o alias só cobre o fabricante ("fury" -> "kingston")
        -- aceita quando os tokens de `key` COMEÇAM pelos tokens de um alias
        ativo, nunca por substring solta ("furyx" nunca bate com "fury").
        Em empate (mais de um alias serve de prefixo), o mais específico
        (mais tokens) vence, determinístico."""
        tokens = key.split("-")
        best: tuple[int, str] | None = None
        for (scope, alias_attribute, raw), canonical in self._alias_index.items():
            if alias_attribute != attribute or scope not in (category, "*"):
                continue
            raw_tokens = raw.split("-")
            if (
                len(raw_tokens) >= len(tokens)
                or tokens[: len(raw_tokens)] != raw_tokens
            ):
                continue
            if best is None or len(raw_tokens) > best[0]:
                best = (len(raw_tokens), canonical)
        return best[1] if best else None

    def canonical_category(self, value: str | None) -> str:
        """Categoria oficial quando é sinônimo/alias; senão o slug do que
        veio (categoria genérica padronizada, ex.: "cadeira-gamer")."""
        slug = category_slug(value or "")
        if not slug:
            return ""
        official = {name for name, _attrs in registered_categories()}
        if slug in official:
            return slug
        aliased = self._alias("*", "category", slug)
        if aliased:
            return aliased
        return CATEGORY_SYNONYMS.get(slug, slug)

    def alias_source_in(
        self,
        category: str,
        attribute: str,
        value: str | None,
        present: Callable[[str], bool],
    ) -> str | None:
        """Grafia alternativa (em palavras) de um alias ATIVO cuja oficial é
        `value` e que está escrita no título (`present`) -- o fabricante
        "Kingston" é aceito no grounding de "Memória Fury Beast" porque o
        alias fury -> kingston existe e "fury" está no título."""
        target = category_slug(value or "")
        if not target:
            return None
        for (scope, alias_attribute, raw), canonical in self._alias_index.items():
            if (
                canonical == target
                and alias_attribute == attribute
                and scope in (category, "*")
                and present(raw.replace("-", " "))
            ):
                return raw.replace("-", " ")
        return None

    def canonical_value(
        self, category: str, attribute: str, value: str | None
    ) -> str | None:
        """Grafia oficial de marca/família quando há alias ativo; senão o
        próprio valor (a normalização em slug acontece na identidade)."""
        if not value or not value.strip():
            return value
        return self._alias(category, attribute, value) or value


@dataclass(frozen=True, slots=True)
class CanonicalFields:
    category: str
    brand: str | None
    family: str | None


def canonicalize_fields(
    vocabulary: IdentityVocabulary | None,
    *,
    category: str | None,
    brand: str | None,
    family: str | None,
) -> CanonicalFields:
    """A trava do código: roda DEPOIS do grounding (que confere o que está
    escrito no título) e ANTES de virar identidade/vínculo."""
    vocab = vocabulary or IdentityVocabulary()
    canonical_category = vocab.canonical_category(category)
    return CanonicalFields(
        category=canonical_category,
        brand=vocab.canonical_value(canonical_category, "brand", brand),
        family=vocab.canonical_value(canonical_category, "family", family),
    )


async def load_identity_vocabulary(session: AsyncSession) -> IdentityVocabulary:
    """Três consultas curtas, só leitura. Chamado uma vez por resolução/lote,
    antes da chamada de IA (nenhuma transação fica aberta durante a IA --
    quem chama faz o rollback de sempre antes de chamar)."""
    official = {name for name, _attrs in registered_categories()}
    used = (
        await session.scalars(
            select(ProductIdentityCandidate.category)
            .where(
                ProductIdentityCandidate.category.is_not(None),
                ProductIdentityCandidate.status.in_(("approved", "partial")),
            )
            .distinct()
        )
    ).all()
    generic = tuple(sorted({c for c in used if c and c not in official}))
    families = (
        await session.execute(
            select(
                ProductIdentityCandidate.category,
                ProductIdentityCandidate.brand,
                ProductIdentityCandidate.family,
            )
            .where(ProductIdentityCandidate.status == "approved")
            .distinct()
            .order_by(
                ProductIdentityCandidate.category,
                ProductIdentityCandidate.brand,
                ProductIdentityCandidate.family,
            )
            .limit(_MAX_KNOWN_FAMILIES)
        )
    ).all()
    aliases = (
        await session.execute(
            select(
                ProductIdentityAlias.category,
                ProductIdentityAlias.attribute_name,
                ProductIdentityAlias.raw_value_normalized,
                ProductIdentityAlias.canonical_value,
            )
            .where(ProductIdentityAlias.status == "active")
            .order_by(
                ProductIdentityAlias.category, ProductIdentityAlias.attribute_name
            )
        )
    ).all()
    return IdentityVocabulary(
        generic_categories=generic,
        known_families=tuple((c, b, f) for c, b, f in families if c and b and f),
        aliases=tuple(tuple(row) for row in aliases),
    )


def render_vocabulary_prompt(vocabulary: IdentityVocabulary | None) -> str:
    """Bloco do prompt com o vocabulário -- sempre inclui as categorias
    oficiais (com significado e atributos); o resto só quando existe."""
    vocab = vocabulary or IdentityVocabulary()
    lines = [
        "VOCABULÁRIO OBRIGATÓRIO (use exatamente estas grafias):",
        "",
        "Categorias oficiais -- category = o nome da esquerda, escolhido pelo "
        "SIGNIFICADO do produto (nunca traduza nem varie a grafia); em "
        "attributes use só os nomes de atributo listados para a categoria:",
    ]
    for name, attributes in registered_categories():
        meaning = CATEGORY_MEANINGS.get(name, name)
        attrs = ", ".join(attributes) if attributes else "(nenhum)"
        lines.append(f"- {name}: {meaning}. Atributos: {attrs}")
    lines += [
        "",
        "Produto que não é de nenhuma categoria oficial (ex.: cadeira gamer, "
        "mesa, mochila): category = o tipo do produto em português, curto e "
        'genérico (ex.: "cadeira gamer"), sem marca nem modelo. Se já existir '
        "uma categoria genérica abaixo com o mesmo significado, use ELA, "
        "exatamente como está.",
    ]
    if vocab.generic_categories:
        lines.append(
            "Categorias genéricas já em uso: " + ", ".join(vocab.generic_categories)
        )
    if vocab.known_families:
        lines += [
            "",
            "Marcas e famílias já cadastradas (categoria | marca | família) -- "
            "se o produto for dessa marca/família, repita a MESMA grafia:",
        ]
        lines += [f"- {c} | {b} | {f}" for c, b, f in vocab.known_families]
    if vocab.aliases:
        lines += [
            "",
            "Grafias alternativas conhecidas (campo: alternativa -> oficial) -- "
            "sempre devolva a oficial:",
        ]
        lines += [
            f"- {c} {a}: {raw} -> {canonical}" for c, a, raw, canonical in vocab.aliases
        ]
    return "\n".join(lines)


# Decisão do usuário (2026-09-27): "a questão da cor é somente se o usuário
# especificar no pedido da missão" -- cor NUNCA separa produto (mesmo
# gráfico/comparação para todas as cores). Quando a missão pede uma cor,
# quem filtra é a classificação de relevância, que compara o título real
# de cada oferta com o pedido.
NON_IDENTITY_ATTRIBUTES = frozenset({"color", "cor", "colour"})
COLOR_WORDS = frozenset(
    {
        "preto",
        "preta",
        "branco",
        "branca",
        "prata",
        "prateado",
        "cinza",
        "grafite",
        "chumbo",
        "azul",
        "vermelho",
        "vermelha",
        "rosa",
        "pink",
        "verde",
        "amarelo",
        "amarela",
        "roxo",
        "roxa",
        "lilas",
        "violeta",
        "dourado",
        "dourada",
        "ouro",
        "bege",
        "marrom",
        "laranja",
        "vinho",
        "titanio",
        "natural",
        "meia-noite",
        "luz-das-estrelas",
        "estelar",
        "black",
        "white",
        "silver",
        "gray",
        "grey",
        "blue",
        "red",
        "green",
        "yellow",
        "purple",
        "gold",
        "orange",
        "brown",
        "midnight",
        "starlight",
        "graphite",
        "titanium",
        "space-gray",
        "space-grey",
        "rose",
        "coral",
        "lavanda",
        "lavender",
        "creme",
        "cream",
    }
)


def is_color(value: str | None) -> bool:
    """Valor que só descreve cor (ex.: "Rosa", "Luz das Estrelas",
    "Preto Fosco" não -- "fosco" não é cor, então fica)."""
    slug = category_slug(value or "")
    if not slug:
        return False
    if slug in COLOR_WORDS:
        return True
    return all(part in COLOR_WORDS for part in slug.split("-"))


def strip_color(
    variant: str | None, attributes: dict[str, str]
) -> tuple[str | None, dict[str, str]]:
    """Tira a cor do que entra na identidade: variante que é só cor vira
    `None` e atributo de cor sai da lista (a identidade usa variante e
    TODOS os atributos -- cor em qualquer um deles separaria o produto)."""
    kept = {
        name: value
        for name, value in attributes.items()
        if category_slug(name) not in NON_IDENTITY_ATTRIBUTES
    }
    return (None if is_color(variant) else variant), kept
