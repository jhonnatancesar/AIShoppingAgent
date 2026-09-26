"""TASK-128 etapa 2 -- leitura da página do produto pelo worker, sem
navegador real: extrator dos dados do PRÓPRIO produto, contrato do
resultado, provider base (aba de detalhe Edge/CDP) e adapter."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import ProductPageRead, ProductPageReadStatus
from app.collection.errors import CollectionContractError
from app.collection.product_page import (
    MAX_CONTEXT_CHARS,
    PRODUCT_PAGE_SCRIPT,
    format_product_page_context,
)
from app.collection.providers.stores import KabumProvider, MagaluProvider

# ---------------------------------------------------------------------------
# format_product_page_context
# ---------------------------------------------------------------------------


def _ld(*documents) -> list[str]:
    return [json.dumps(document) for document in documents]


def test_context_keeps_only_the_first_product_and_its_structured_fields() -> None:
    """Vitrines de "relacionados" costumam vir como Product seguintes --
    só o primeiro entra (marca de outro item nunca vira evidência)."""
    payload = {
        "jsonld": _ld(
            {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "Organization", "name": "Loja"},
                    {
                        "@type": ["Product", "Thing"],
                        "name": "Headset Gamer  Preto",
                        "brand": {"@type": "Brand", "name": "HyperX"},
                        "category": "Headsets",
                        "model": "Cloud Stinger 2",
                        "mpn": "519T1AA",
                        "sku": 12345,
                        "additionalProperty": [
                            {"name": "Conexão", "value": "P2"},
                            {"name": "sem valor"},
                            "lixo",
                        ],
                        "description": "Headset leve para jogos.",
                    },
                ],
            },
            {"@type": "Product", "name": "Mouse", "brand": "Logitech"},
        ),
    }

    context = format_product_page_context(payload)

    assert context.splitlines() == [
        "Nome: Headset Gamer Preto",
        "Marca: HyperX",
        "Categoria: Headsets",
        "Modelo: Cloud Stinger 2",
        "Part number (MPN): 519T1AA",
        "SKU da loja: 12345",
        "Especificações: Conexão: P2",
        "Descrição: Headset leve para jogos.",
    ]
    assert "Logitech" not in context


def test_context_reads_breadcrumb_from_json_ld_before_the_dom() -> None:
    payload = {
        "jsonld": _ld(
            [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"name": "Games"},
                        {"item": {"name": "Cadeiras"}},
                        "lixo",
                    ],
                }
            ]
        ),
        "breadcrumb": ["Nunca usado"],
    }

    assert (
        format_product_page_context(payload) == "Trilha de categorias: Games > Cadeiras"
    )


def test_context_falls_back_to_dom_breadcrumb_titles_and_meta_description() -> None:
    payload = {
        "jsonld": ["{não é json", 42, _ld({"@type": "BreadcrumbList"})[0]],
        "breadcrumb": ["Casa", "  ", "Cadeiras de escritório"],
        "h1": "Cadeira Gamer Reclinável",
        "og_title": "Cadeira Gamer Reclinável",
        "description": "Cadeira com apoio lombar.",
    }

    assert format_product_page_context(payload).splitlines() == [
        "Trilha de categorias: Casa > Cadeiras de escritório",
        "Título da página: Cadeira Gamer Reclinável",
        "Descrição: Cadeira com apoio lombar.",
    ]


def test_context_ignores_non_text_values_and_is_none_when_nothing_useful() -> None:
    assert format_product_page_context({}) is None
    assert (
        format_product_page_context(
            {
                "jsonld": "não é lista",
                "breadcrumb": "não é lista",
                "h1": True,
                "og_title": {"x": 1},
                "description": "   ",
            }
        )
        is None
    )
    assert (
        format_product_page_context(
            {"jsonld": _ld({"@type": "Product", "brand": [{"x": 1}, "Acme"]})}
        )
        == "Marca: Acme"
    )


def test_context_is_capped() -> None:
    long_name = "x" * 2000
    payload = {
        "jsonld": _ld(
            {
                "@type": "Product",
                "name": long_name,
                "additionalProperty": [
                    {"name": f"spec{n}", "value": "v" * 80} for n in range(30)
                ],
                "description": "d" * 2000,
            }
        )
    }

    context = format_product_page_context(payload)

    assert len(context) <= MAX_CONTEXT_CHARS
    assert context.count("spec") == 15


# ---------------------------------------------------------------------------
# ProductPageRead
# ---------------------------------------------------------------------------


def test_read_result_requires_context_exactly_when_the_page_was_read() -> None:
    assert ProductPageRead(ProductPageReadStatus.READ, "Marca: X").context
    assert ProductPageRead(ProductPageReadStatus.FAILED).context is None
    with pytest.raises(CollectionContractError):
        ProductPageRead(ProductPageReadStatus.READ, "  ")
    with pytest.raises(CollectionContractError):
        ProductPageRead(ProductPageReadStatus.EMPTY, "Marca: X")


# ---------------------------------------------------------------------------
# PlaywrightStoreProvider.read_product_page
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status


class _Page:
    def __init__(self, *, status: int | None = 200, payload=None) -> None:
        self._status = status
        self._payload = payload
        self.visited: list[str] = []
        self.scripts: list[str] = []

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        return None if self._status is None else _Response(self._status)

    async def evaluate(self, script):
        self.scripts.append(script)
        return self._payload


class _Transport:
    def __init__(self, page: _Page) -> None:
        self._page = page

    @asynccontextmanager
    async def open_blank_page(self):
        yield self._page


def _kabum(page: _Page, namespace: str = "task128-page-read") -> KabumProvider:
    return KabumProvider(cdp_transport=_Transport(page), circuit_namespace=namespace)


def test_provider_reads_the_product_data_from_the_detail_tab() -> None:
    page = _Page(payload={"jsonld": _ld({"@type": "Product", "brand": "HyperX"})})

    result = asyncio.run(_kabum(page).read_product_page("https://x/produto/1"))

    assert result == ProductPageRead(ProductPageReadStatus.READ, "Marca: HyperX")
    assert page.visited == ["https://x/produto/1"]
    assert page.scripts == [PRODUCT_PAGE_SCRIPT]


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (_Page(payload={"h1": None}), ProductPageReadStatus.EMPTY),
        (_Page(payload="não é objeto"), ProductPageReadStatus.EMPTY),
        (_Page(status=None), ProductPageReadStatus.FAILED),
        (_Page(status=403), ProductPageReadStatus.FAILED),
        (_Page(status=500), ProductPageReadStatus.FAILED),
    ],
)
def test_provider_page_read_failures_and_empty_pages(page, expected) -> None:
    result = asyncio.run(_kabum(page).read_product_page("https://x/produto/1"))
    assert result.status is expected


def test_provider_never_navigates_while_the_store_circuit_is_not_closed() -> None:
    page = _Page(payload={"h1": "x"})
    provider = _kabum(page, namespace="task128-page-read-open-circuit")
    for _ in range(10):
        provider._circuit.record_failure(transient=True)

    result = asyncio.run(provider.read_product_page("https://x/produto/1"))

    assert result.status is ProductPageReadStatus.FAILED
    assert page.visited == []


def test_store_without_detail_transport_does_not_support_page_reads() -> None:
    """Magalu, por decisão antiga, nunca abre página de produto."""
    result = asyncio.run(MagaluProvider().read_product_page("https://x/p/1"))
    assert result.status is ProductPageReadStatus.UNSUPPORTED


# ---------------------------------------------------------------------------
# CollectionAdapter.read_product_page
# ---------------------------------------------------------------------------


class _ReadingProvider:
    source_code = "kabum"

    def __init__(self, outcome) -> None:
        self._outcome = outcome
        self.urls: list[str] = []

    async def collect(self, request):  # pragma: no cover - não usado aqui
        raise AssertionError

    async def read_product_page(self, url):
        self.urls.append(url)
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class _PlainProvider:
    source_code = "pichau"

    async def collect(self, request):  # pragma: no cover - não usado aqui
        raise AssertionError


def _read(adapter, source_code, url="https://x/produto/1"):
    return asyncio.run(adapter.read_product_page(source_code, url))


def test_adapter_page_read_dispatch_and_failure_isolation() -> None:
    ok = ProductPageRead(ProductPageReadStatus.READ, "Marca: X")
    provider = _ReadingProvider(ok)
    adapter = CollectionAdapter((provider, _PlainProvider()))

    assert _read(adapter, "kabum") is ok
    assert provider.urls == ["https://x/produto/1"]
    assert _read(adapter, "pichau").status is ProductPageReadStatus.UNSUPPORTED
    assert _read(adapter, "shopee").status is ProductPageReadStatus.UNSUPPORTED
    assert _read(adapter, "kabum", "javascript:alert(1)").status is (
        ProductPageReadStatus.FAILED
    )
    assert provider.urls == ["https://x/produto/1"], "URL inválida nunca navega"

    failing = CollectionAdapter((_ReadingProvider(RuntimeError("timeout")),))
    assert _read(failing, "kabum").status is ProductPageReadStatus.FAILED

    broken = CollectionAdapter((_ReadingProvider("não é ProductPageRead"),))
    with pytest.raises(CollectionContractError):
        _read(broken, "kabum")


def test_adapter_page_read_never_swallows_cancellation() -> None:
    adapter = CollectionAdapter((_ReadingProvider(asyncio.CancelledError()),))
    with pytest.raises(asyncio.CancelledError):
        _read(adapter, "kabum")
