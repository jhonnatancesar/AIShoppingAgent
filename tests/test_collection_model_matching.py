"""Testes diretos do matcher compartilhado (TASK-075, extraído na TASK-083).

`tests/test_collection_orchestration.py` já prova, através do alias mantido
em `app.collection.orchestration`, que o comportamento da TASK-075
permanece idêntico após a extração. Este arquivo cobre o módulo
`app.collection.model_matching` diretamente, como consumidor de primeira
classe (usado também pela resolução de identidade da TASK-083), incluindo
os casos de SKU vizinho explicitamente exigidos para essa TASK.
"""

import pytest
from app.collection.model_matching import (
    STRONG_VARIANT_SUFFIXES,
    normalize_for_matching,
    same_code,
    title_matches_model,
)


def test_normalize_for_matching_strips_accents_and_uppercases() -> None:
    assert normalize_for_matching("Não é número") == "NAO E NUMERO"


@pytest.mark.parametrize(
    ("model", "title", "expected"),
    [
        ("9800X3D", "Processador AMD Ryzen 7 9800X3D", True),
        ("9800X3D", "Processador AMD Ryzen 7 9800-X3D", True),
        ("9800X3D", "Processador AMD Ryzen 7 9800 X3D", True),
        ("9800x3d", "Processador AMD Ryzen 7 9800X3D", True),  # case-insensitive
        # SKUs vizinhos da mesma família -- nunca podem ser aceitos como
        # correspondência do modelo pedido (motivação real da TASK-083).
        ("9800X3D", "Processador AMD Ryzen 7 7800X3D", False),
        ("9800X3D", "Processador AMD Ryzen 9 9900X3D", False),
        ("9800X3D", "Processador AMD Ryzen 7 9800X", False),
    ],
)
def test_title_matches_model_tolerates_separators_never_neighbor_skus(
    model: str, title: str, expected: bool
) -> None:
    assert title_matches_model(model, title) is expected


def test_strong_variant_suffixes_reject_unrequested_variant() -> None:
    assert title_matches_model("RTX 4070", "Placa de Vídeo RTX 4070 Ti") is False
    assert "TI" in STRONG_VARIANT_SUFFIXES


# --- same_code (TASK-083 SUBETAPA 4: detecção de identidade crua) ---


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("9800X3D", "9800X3D", True),
        ("9800X3D", "9800x3d", True),
        ("9800X3D", "9800-X3D", True),
        ("9800X3D", "9800 X3D", True),
        ("9800X3D", "9800_X3D", True),
        # Canonicalização real nunca é confundida com o código cru --
        # `same_code` exige que os dois lados sejam o código inteiro, não
        # uma substring dentro de um texto maior.
        ("9800X3D", "AMD Ryzen 7 9800X3D", False),
        ("9800X3D", "7800X3D", False),
    ],
)
def test_same_code_tolerates_separators_not_full_titles(
    a: str, b: str, expected: bool
) -> None:
    assert same_code(a, b) is expected
