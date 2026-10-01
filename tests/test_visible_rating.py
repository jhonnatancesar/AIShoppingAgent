"""TASK-136 (passo 1) -- nota e avaliações que a página do produto MOSTRA.

Textos reais vistos ao vivo em 2026-10-01: Pichau "5.0 (13 avaliações)" ao lado do
título; Kabum "4.9/5 (19 avaliações)". Produto sem avaliação aparece como "0.0 (0
avaliações)" e nunca vira nota."""

import asyncio

import pytest
from app.collection.providers import stores
from app.collection.providers.stores import (
    _KABUM_RATING_TEXT,
    _PICHAU_RATING_TEXT,
    KabumProvider,
    PichauProvider,
    _visible_rating,
)


class _Page:
    def __init__(self, text: str, *, ready: bool = True) -> None:
        self.text = text
        self.ready = ready

    async def wait_for_function(self, expression, arg=None, timeout=None):
        if not self.ready:
            raise TimeoutError("não apareceu")

    async def evaluate(self, expression):
        return self.text


def _read(pattern, text, **kwargs):
    return asyncio.run(_visible_rating(_Page(text, **kwargs), pattern))


@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        (
            _PICHAU_RATING_TEXT,
            "Frete Grátis: Sul e Sudeste 5.0 (13 avaliações) PLACA MAE MSI B550M",
            ("5.0", "13"),
        ),
        (
            _PICHAU_RATING_TEXT,
            "Sudeste 4.9 (39 avaliações) PLACA MAE GIGABYTE",
            ("4.9", "39"),
        ),
        (_KABUM_RATING_TEXT, "AVALIAÇÕES 4.9/5 (19 avaliações) ★★★★", ("4.9", "19")),
        (_KABUM_RATING_TEXT, "Avaliação 4,8 / 5 (1.065 avaliações)", ("4,8", "1065")),
    ],
)
def test_visible_rating_is_read(pattern, text, expected) -> None:
    assert _read(pattern, text) == expected


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (_PICHAU_RATING_TEXT, "Sudeste 0.0 (0 avaliações) PLACA MAE GIGABYTE"),
        (_PICHAU_RATING_TEXT, "Placa Mae 5.0 R$ 799,00"),
        (_KABUM_RATING_TEXT, "Avaliação 4.8 de 5.0 R$ 700"),
        (
            _KABUM_RATING_TEXT,
            "4.9 (19 avaliações)",
        ),  # sem o "/5" não é o bloco da Kabum
    ],
)
def test_without_reviews_or_without_the_block_nothing_is_read(pattern, text) -> None:
    assert _read(pattern, text) is None


def test_waiting_timeout_returns_none_instead_of_failing() -> None:
    assert _read(_PICHAU_RATING_TEXT, "5.0 (13 avaliações)", ready=False) is None


def test_pichau_and_kabum_read_the_rating_on_the_product_page() -> None:
    assert PichauProvider.rating_detail_enabled is True
    assert KabumProvider.rating_detail_enabled is True

    async def run(provider_cls, text):
        provider = provider_cls.__new__(provider_cls)
        return await provider.resolve_offer_rating(_Page(text))

    assert asyncio.run(run(PichauProvider, "x 5.0 (13 avaliações) y")) == ("5.0", "13")
    assert asyncio.run(run(KabumProvider, "AVALIAÇÕES 4.9/5 (19 avaliações)")) == (
        "4.9",
        "19",
    )


def test_module_exposes_the_patterns_used_by_the_stores() -> None:
    assert stores._PICHAU_RATING_TEXT is _PICHAU_RATING_TEXT
