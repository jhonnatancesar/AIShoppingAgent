"""Redesenho MATCH/MISSING/CONTRADICTORY do matcher determinístico de
identidade (`app.products.identity_learning`, rodada de 2026-09-14) --
testes puros, sem banco: cobrem só a classificação por dimensão usada
antes de decidir se vale a pena gastar uma chamada de árbitro de IA.
"""

from app.products.identity_ai import AIIdentityExtraction
from app.products.identity_arbiter import ListingEvidence
from app.products.identity_learning import (
    DimensionVerdict,
    _ArbitrationCandidate,
    _classify_candidate_against_extraction,
    _classify_dimension,
    _has_unrecognized_model_suffix,
    _listing_evidence_from_candidate,
    _listing_evidence_from_extraction,
    _resolved_from_candidate,
)


def _candidate(
    *,
    variant: str = "base",
    attributes: dict[str, str] | None = None,
    manufacturer_part_number: str | None = None,
) -> _ArbitrationCandidate:
    return _ArbitrationCandidate(
        category="motherboard",
        brand="asus",
        family="tuf-gaming",
        model="b650-plus",
        variant=variant,
        attributes=attributes or {},
        store_sku="STORE-SKU-A",
        manufacturer_part_number=manufacturer_part_number,
        family_key="asus-tuf-gaming-b650-plus",  # gitleaks:allow -- slug de teste, não é segredo
        identity_key="asus-tuf-gaming-b650-plus-base",
    )


def _extraction(
    *,
    variant: str | None = None,
    attributes: dict[str, str] | None = None,
    store_sku: str | None = None,
    manufacturer_part_number: str | None = None,
) -> AIIdentityExtraction:
    return AIIdentityExtraction(
        category="motherboard",
        brand="ASUS",
        family="TUF Gaming",
        model="B650-Plus",
        variant=variant,
        store_sku=store_sku,
        manufacturer_part_number=manufacturer_part_number,
        attributes=attributes or {},
        ai_provider="stub",
        ai_model="stub-model",
    )


class TestClassifyDimension:
    def test_match_simples(self) -> None:
        assert _classify_dimension("DDR5", "DDR5") is DimensionVerdict.MATCH

    def test_match_tolera_pontuacao_diferente(self) -> None:
        assert (
            _classify_dimension("90MB1CG0-M0EAY0", "90MB1CG0M0EAY0")
            is DimensionVerdict.MATCH
        )

    def test_missing_quando_conhecido_ausente(self) -> None:
        assert _classify_dimension(None, "DDR5") is DimensionVerdict.MISSING

    def test_missing_quando_observado_ausente(self) -> None:
        assert _classify_dimension("DDR5", None) is DimensionVerdict.MISSING

    def test_missing_quando_ambos_ausentes(self) -> None:
        assert _classify_dimension(None, None) is DimensionVerdict.MISSING

    def test_contradictory_quando_valores_incompativeis(self) -> None:
        assert _classify_dimension("DDR5", "DDR4") is DimensionVerdict.CONTRADICTORY


class TestClassifyCandidateAgainstExtraction:
    def test_atributo_em_falta_de_um_lado_nao_e_contradicao(self) -> None:
        candidate = _candidate(attributes={"memory-type": "DDR5"})
        extraction = _extraction(attributes={})
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert verdicts["attribute:memory-type"] is DimensionVerdict.MISSING
        assert not any(v is DimensionVerdict.CONTRADICTORY for v in verdicts.values())

    def test_atributo_realmente_contraditorio(self) -> None:
        candidate = _candidate(attributes={"memory-type": "DDR5"})
        extraction = _extraction(attributes={"memory_type": "DDR4"})
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert verdicts["attribute:memory-type"] is DimensionVerdict.CONTRADICTORY

    def test_chave_de_atributo_com_grafia_diferente_e_normalizada_antes_de_comparar(
        self,
    ) -> None:
        """`candidate.attributes` (persistido) usa slug (`memory-type`),
        `extraction.attributes` (bruto, vindo direto da IA) pode devolver
        `memory_type` -- achado real (2026-09-14, suíte de integração):
        sem normalizar os dois lados pela MESMA regra, uma contradição
        real (DDR5 vs DDR4) virava dois MISSING (uma dimensão só do
        candidato, outra só da extração), nunca chegando ao árbitro."""
        candidate = _candidate(attributes={"memory-type": "DDR5"})
        extraction = _extraction(attributes={"memory_type": "DDR4"})
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert len(verdicts) == 1, "as duas grafias devem colapsar numa única dimensão"
        assert verdicts["attribute:memory-type"] is DimensionVerdict.CONTRADICTORY

    def test_manufacturer_part_number_ausente_de_um_lado_e_missing(self) -> None:
        candidate = _candidate(manufacturer_part_number="90MB1CG0-M0EAY0")
        extraction = _extraction(manufacturer_part_number=None)
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert verdicts["manufacturer_part_number"] is DimensionVerdict.MISSING

    def test_manufacturer_part_number_diferente_e_contradictory(self) -> None:
        candidate = _candidate(manufacturer_part_number="90MB1CG0-M0EAY0")
        extraction = _extraction(manufacturer_part_number="90MB1XY9-DIFFERENT")
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert verdicts["manufacturer_part_number"] is DimensionVerdict.CONTRADICTORY

    def test_store_sku_nunca_entra_na_classificacao(self) -> None:
        candidate = _candidate()
        extraction = _extraction(store_sku="OUTRA-LOJA-SKU-DIFERENTE")
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert not any(key.startswith("store_sku") for key in verdicts)
        assert verdicts == {}

    def test_store_sku_ausente_tambem_nunca_entra_na_classificacao(self) -> None:
        candidate = _candidate()
        extraction = _extraction(store_sku=None)
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert not any(key.startswith("store_sku") for key in verdicts)

    def test_variant_base_conhecido_e_ausente_no_observado_e_missing(self) -> None:
        candidate = _candidate(variant="base")
        extraction = _extraction(variant=None)
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert "variant" not in verdicts, (
            "sem variant conhecido e sem variant observado -- nenhuma "
            "evidência a classificar"
        )

    def test_variant_conhecido_ausente_no_observado_e_missing(self) -> None:
        candidate = _candidate(variant="pro")
        extraction = _extraction(variant=None)
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert verdicts["variant"] is DimensionVerdict.MISSING

    def test_variant_contraditorio(self) -> None:
        candidate = _candidate(variant="pro")
        extraction = _extraction(variant="lite")
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert verdicts["variant"] is DimensionVerdict.CONTRADICTORY

    def test_sem_nenhuma_evidencia_disponivel_verdicts_vazio(self) -> None:
        candidate = _candidate()
        extraction = _extraction()
        verdicts = _classify_candidate_against_extraction(candidate, extraction)
        assert verdicts == {}


class TestResolvedFromCandidate:
    def test_reconstroi_variant_direto_das_colunas_persistidas_sem_reprocessar(
        self,
    ) -> None:
        candidate = _candidate(variant="pro", attributes={"chipset": "b650"})
        resolved = _resolved_from_candidate(candidate)

        assert resolved.category == candidate.category
        assert resolved.brand == candidate.brand
        assert resolved.family == candidate.family
        assert resolved.model == candidate.model
        assert resolved.variant == "pro"
        assert resolved.attributes == (("chipset", "b650"),)
        assert resolved.family_key == candidate.family_key
        assert resolved.identity_key == candidate.identity_key
        assert resolved.label == "Asus Tuf-Gaming B650-PLUS"


class TestHasUnrecognizedModelSuffix:
    def test_true_para_sufixo_curto_alfanumerico_desconhecido(self) -> None:
        assert (
            _has_unrecognized_model_suffix(
                "27GP850 B", "27GP850", known_tokens=frozenset()
            )
            is True
        )

    def test_false_quando_sufixo_ja_e_um_token_conhecido(self) -> None:
        assert (
            _has_unrecognized_model_suffix(
                "27GP850 B", "27GP850", known_tokens=frozenset({"B"})
            )
            is False
        )

    def test_false_quando_modelo_normalizado_fica_vazio(self) -> None:
        assert (
            _has_unrecognized_model_suffix("27GP850 B", "", known_tokens=frozenset())
            is False
        )

    def test_false_quando_modelo_nao_aparece_no_titulo(self) -> None:
        assert (
            _has_unrecognized_model_suffix(
                "OUTRO PRODUTO TOTALMENTE DIFERENTE",
                "27GP850",
                known_tokens=frozenset(),
            )
            is False
        )

    def test_false_quando_token_seguinte_e_longo_demais_para_ser_sufixo(self) -> None:
        assert (
            _has_unrecognized_model_suffix(
                "27GP850 PRO", "27GP850", known_tokens=frozenset()
            )
            is False
        )

    def test_false_quando_modelo_e_o_ultimo_token_do_titulo(self) -> None:
        assert (
            _has_unrecognized_model_suffix(
                "MONITOR LG 27GP850", "27GP850", known_tokens=frozenset()
            )
            is False
        )


class TestListingEvidenceFromCandidate:
    def test_mapeia_campos_e_trata_variant_base_como_ausencia(self) -> None:
        candidate = _candidate(variant="base", attributes={"chipset": "b650"})
        evidence = _listing_evidence_from_candidate(candidate)

        assert evidence == ListingEvidence(
            manufacturer=candidate.brand,
            family=candidate.family,
            model_name=candidate.model,
            variant=None,
            store_sku=candidate.store_sku,
            manufacturer_part_number=candidate.manufacturer_part_number,
            attributes=candidate.attributes,
        )

    def test_variant_diferente_de_base_e_preservada(self) -> None:
        candidate = _candidate(variant="pro")
        evidence = _listing_evidence_from_candidate(candidate)
        assert evidence.variant == "pro"


class TestListingEvidenceFromExtraction:
    def test_mapeia_campos_diretamente_sem_normalizar_variant(self) -> None:
        extraction = _extraction(
            variant="base", store_sku="SKU-1", manufacturer_part_number="MPN-1"
        )
        evidence = _listing_evidence_from_extraction(extraction)

        assert evidence == ListingEvidence(
            manufacturer=extraction.brand,
            family=extraction.family,
            model_name=extraction.model,
            variant="base",
            store_sku="SKU-1",
            manufacturer_part_number="MPN-1",
            attributes=extraction.attributes,
        )
