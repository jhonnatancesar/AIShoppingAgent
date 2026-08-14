"""Testes do detector determinístico de canonicalização suspeita (TASK-083)."""

import pytest
from app.intent.nomenclature import (
    check_known_family_contradiction,
    detect_unproven_enrichment,
    is_bare_model_input,
)


@pytest.mark.parametrize(
    ("model", "search_query", "expected_contradiction"),
    [
        ("9800X3D", "AMD Ryzen 9 9800X3D", True),
        ("9800X3D", "AMD Ryzen 7 9800X3D", False),
        ("7800X3D", "AMD Ryzen 9 7800X3D", True),
        ("9950X3D", "AMD Ryzen 7 9950X3D", True),
        ("9950X3D", "AMD Ryzen 9 9950X3D", False),
        ("7950X3D", "AMD Ryzen 7 7950X3D", True),
        # Fora do guardrail (não é X3D) -- nunca contradiz por essa função,
        # mesmo com um nome estranho; essa função só cobre o caso cadastrado.
        ("7600X", "AMD Ryzen 9 7600X", False),
    ],
)
def test_check_known_family_contradiction(
    model: str, search_query: str, expected_contradiction: bool
) -> None:
    result = check_known_family_contradiction(model, search_query)
    assert (result is not None) is expected_contradiction


@pytest.mark.parametrize(
    ("raw_message", "model", "expected"),
    [
        ("9800X3D", "9800X3D", True),
        ("quero um 9800x3d", "9800X3D", True),
        ("me acha um 9800-x3d pfvr", "9800X3D", True),
        ("quero um 9800x3d bom e barato", "9800X3D", False),  # palavra extra real
        (
            "quero uma 4070 ti",
            "RTX 4070 Ti",
            False,
        ),  # model tem token (RTX) que a mensagem não tem
        ("RTX 4070 Ti", "RTX 4070 Ti", True),
        ("quero um mouse gamer", None, False),
    ],
)
def test_is_bare_model_input(
    raw_message: str, model: str | None, expected: bool
) -> None:
    if model is None:
        pytest.skip(
            "is_bare_model_input exige model não nulo pelo contrato do chamador"
        )
    assert is_bare_model_input(raw_message, model) is expected


class TestDetectUnprovenEnrichment:
    def test_flags_brand_and_family_not_present_in_raw_message(self) -> None:
        assert (
            detect_unproven_enrichment("9800X3D", "9800X3D", "AMD Ryzen 9 9800X3D")
            is True
        )

    def test_flags_even_with_filler_words_stripped(self) -> None:
        assert (
            detect_unproven_enrichment(
                "quero um 9800x3d", "9800X3D", "Processador AMD Ryzen 7 9800X3D"
            )
            is True
        )

    def test_does_not_flag_when_message_already_has_extra_real_words(self) -> None:
        # "bom e barato" já é conteúdo do próprio usuário -- mensagem não é
        # mais "essencialmente só o código", então o detector geral não se
        # aplica (não é o caso que a TASK-083 pediu para cobrir).
        assert (
            detect_unproven_enrichment(
                "quero um 9800x3d bom e barato",
                "9800X3D",
                "Processador AMD Ryzen 7 9800X3D",
            )
            is False
        )

    def test_does_not_flag_when_model_itself_already_carries_the_brand_token(
        self,
    ) -> None:
        # "quero uma 4070 ti" -> model já teve que inferir "RTX" (não bare),
        # então o detector geral não dispara aqui -- é a família X3D/CPU que
        # motivou a TASK, não recanonicalização de marca já estabelecida.
        assert (
            detect_unproven_enrichment(
                "quero uma 4070 ti",
                "RTX 4070 Ti",
                "Placa de Vídeo NVIDIA RTX 4070 Ti",
            )
            is False
        )

    def test_flags_bare_gpu_code_including_brand_prefix(self) -> None:
        assert (
            detect_unproven_enrichment(
                "RTX 4070 Ti", "RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX 4070 Ti"
            )
            is True
        )

    def test_does_not_flag_generic_search_without_model(self) -> None:
        # Busca genérica: model é None -- a função nunca lança, só devolve
        # False (nada para desconfiar sem modelo identificado).
        assert (
            detect_unproven_enrichment("cadeira gamer", None, "cadeira gamer") is False
        )

    def test_works_for_unknown_model_without_any_lookup_table(self) -> None:
        # Nenhuma tabela de produto é consultada -- funciona igual para um
        # código totalmente inventado, provando que não depende de
        # catálogo.
        assert (
            detect_unproven_enrichment(
                "ZX9999QQ", "ZX9999QQ", "Processador Fabricante Fictício ZX9999QQ"
            )
            is True
        )
