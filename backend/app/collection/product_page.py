"""TASK-128 (etapa 2): dados do PRÓPRIO produto numa página de loja, para
a IA tentar de novo quando só o título não bastou ("não entendi").

O script roda no navegador do worker (mesma página de detalhe do
enriquecimento, Edge/CDP) e só devolve dados crus; toda a interpretação
fica em `format_product_page_context`, pura e testável. Nunca manda o
texto da página inteira: vitrines de "produtos relacionados"/"compre
junto" trariam marcas e modelos de OUTROS itens, e o grounding
anti-invenção passa a aceitar o que estiver neste contexto -- por isso
só entram os dados estruturados do primeiro `Product` (JSON-LD), a
trilha de categorias, o título da página e a descrição da própria loja.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping

PRODUCT_PAGE_SCRIPT = """
() => {
  const text = (element) =>
    element && element.textContent ? element.textContent.trim() : null;
  const meta = (selector) => {
    const element = document.querySelector(selector);
    return element ? element.getAttribute("content") : null;
  };
  return {
    jsonld: Array.from(
      document.querySelectorAll('script[type="application/ld+json"]')
    ).slice(0, 20).map((script) => (script.textContent || "").slice(0, 50000)),
    h1: text(document.querySelector("h1")),
    og_title: meta('meta[property="og:title"]'),
    description: meta('meta[name="description"]'),
    breadcrumb: Array.from(
      document.querySelectorAll(
        'nav[aria-label*="breadcrumb" i] a, [class*="breadcrumb" i] a'
      )
    ).map((link) => (link.textContent || "").trim()).filter(Boolean).slice(0, 10),
  };
}
"""

MAX_CONTEXT_CHARS = 1500
_MAX_FIELD_CHARS = 300
_MAX_DESCRIPTION_CHARS = 400
_MAX_PROPERTIES = 15


def _clean(value: object, limit: int = _MAX_FIELD_CHARS) -> str | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:limit] or None


def _types(node: Mapping[str, object]) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {value for value in values if isinstance(value, str)}


def _walk(node: object) -> Iterator[Mapping[str, object]]:
    """Todos os objetos de um documento JSON-LD, inclusive `@graph` e
    listas no topo -- ordem do documento preservada."""
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, Mapping):
        yield node
        yield from _walk(node.get("@graph"))


def _json_ld_nodes(raw_scripts: object) -> list[Mapping[str, object]]:
    nodes: list[Mapping[str, object]] = []
    if not isinstance(raw_scripts, list):
        return nodes
    for raw in raw_scripts:
        if not isinstance(raw, str):
            continue
        try:
            document = json.loads(raw)
        except ValueError:
            continue
        nodes.extend(_walk(document))
    return nodes


def _named(value: object) -> str | None:
    """`brand` pode vir como texto ou como objeto `{"name": ...}`."""
    if isinstance(value, Mapping):
        return _clean(value.get("name"))
    if isinstance(value, list):
        return next((name for item in value if (name := _named(item))), None)
    return _clean(value)


def _product_lines(product: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    for label, value in (
        ("Nome", _clean(product.get("name"))),
        ("Marca", _named(product.get("brand"))),
        ("Categoria", _named(product.get("category"))),
        ("Modelo", _named(product.get("model"))),
        ("Part number (MPN)", _clean(product.get("mpn"))),
        ("SKU da loja", _clean(product.get("sku"))),
    ):
        if value:
            lines.append(f"{label}: {value}")
    properties = product.get("additionalProperty")
    if isinstance(properties, list):
        pairs = []
        for item in properties[:_MAX_PROPERTIES]:
            if isinstance(item, Mapping):
                name, value = (
                    _clean(item.get("name"), 60),
                    _clean(item.get("value"), 80),
                )
                if name and value:
                    pairs.append(f"{name}: {value}")
        if pairs:
            lines.append("Especificações: " + "; ".join(pairs))
    description = _clean(product.get("description"), _MAX_DESCRIPTION_CHARS)
    if description:
        lines.append(f"Descrição: {description}")
    return lines


def _breadcrumb_names(nodes: list[Mapping[str, object]], dom: object) -> list[str]:
    for node in nodes:
        if "BreadcrumbList" not in _types(node):
            continue
        elements = node.get("itemListElement")
        if not isinstance(elements, list):
            continue
        names = []
        for element in elements:
            if not isinstance(element, Mapping):
                continue
            item = element.get("item")
            name = _clean(element.get("name"), 80) or (
                _clean(item.get("name"), 80) if isinstance(item, Mapping) else None
            )
            if name:
                names.append(name)
        if names:
            return names
    if isinstance(dom, list):
        return [name for item in dom if (name := _clean(item, 80))]
    return []


def format_product_page_context(payload: Mapping[str, object]) -> str | None:
    """Texto compacto (no máximo `MAX_CONTEXT_CHARS`) com os dados do
    produto -- `None` quando a página não trouxe nada aproveitável.
    Só o PRIMEIRO `Product` do JSON-LD entra (os seguintes costumam ser
    vitrines de outros itens)."""
    nodes = _json_ld_nodes(payload.get("jsonld"))
    lines: list[str] = []
    product = next((node for node in nodes if "Product" in _types(node)), None)
    if product is not None:
        lines.extend(_product_lines(product))
    breadcrumb = _breadcrumb_names(nodes, payload.get("breadcrumb"))
    if breadcrumb:
        lines.append("Trilha de categorias: " + " > ".join(breadcrumb))
    for label, key in (
        ("Título da página", "h1"),
        ("Título de compartilhamento", "og_title"),
    ):
        value = _clean(payload.get(key))
        if value and all(value not in line for line in lines):
            lines.append(f"{label}: {value}")
    if not any(line.startswith("Descrição:") for line in lines):
        description = _clean(payload.get("description"), _MAX_DESCRIPTION_CHARS)
        if description:
            lines.append(f"Descrição: {description}")
    context = "\n".join(lines)[:MAX_CONTEXT_CHARS].strip()
    return context or None
