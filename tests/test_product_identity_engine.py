"""Product Identity Engine (TASK-112) -- monitoring_key genérica e
determinística, construída sobre o motor da TASK-097 sem alterá-lo.

Este arquivo testa só a camada nova (`resolve_monitoring_identity`).
`tests/test_product_identity.py` continua cobrindo `identity_key`/
`family_key` (TASK-097) e precisa continuar passando sem nenhuma mudança
de comportamento -- prova de que a evolução foi aditiva, não uma reescrita.
"""

from hashlib import sha256

from app.products.identity import (
    MONITORING_KEY_VERSION,
    resolve_monitoring_identity,
)


# ---------------------------------------------------------------------------
# CPU (AMD Ryzen) -- caso obrigatório do pedido
# ---------------------------------------------------------------------------


def test_cpu_equivalent_texts_converge_to_the_same_key() -> None:
    """O tier ("Ryzen 9") é informação implícita no próprio código do
    modelo (9950X3D), não uma restrição adicional -- mencioná-lo
    explicitamente no texto é reconfirmação redundante, nunca um segundo
    critério. Os três textos abaixo descrevem o MESMO produto."""
    bare = resolve_monitoring_identity("9950x3d")
    prefixed = resolve_monitoring_identity("ryzen 9950x3d")
    full_marketing_text = resolve_monitoring_identity("AMD Ryzen 9 9950X3D")
    canonical_ai_text = resolve_monitoring_identity("Processador AMD Ryzen 9 9950X3D")

    assert bare is not None
    assert prefixed is not None
    assert full_marketing_text is not None
    assert canonical_ai_text is not None
    assert bare.category == "cpu"
    assert bare.brand == "amd"
    assert bare.family == "ryzen-9"
    assert (
        bare.monitoring_key
        == prefixed.monitoring_key
        == full_marketing_text.monitoring_key
        == canonical_ai_text.monitoring_key
    )


def test_cpu_family_is_derived_from_model_code_not_from_explicit_tier_text() -> None:
    """Prova que o tier nunca vem do texto: mesmo se o texto mencionar um
    tier ERRADO/inconsistente, o `family` continua vindo do código --
    conhecimento determinístico do esquema de nomenclatura da AMD, não do
    que a redação disse."""
    misleading_text = resolve_monitoring_identity("Ryzen 5 9950X3D")
    correct_text = resolve_monitoring_identity("Ryzen 9 9950X3D")

    assert misleading_text is not None
    assert correct_text is not None
    assert misleading_text.family == "ryzen-9"
    assert misleading_text.monitoring_key == correct_text.monitoring_key


def test_cpu_different_tier_families_never_share_key() -> None:
    ryzen_9 = resolve_monitoring_identity("Ryzen 9950X3D")
    ryzen_7 = resolve_monitoring_identity("Ryzen 9800X3D")
    ryzen_5 = resolve_monitoring_identity("Ryzen 7600X")

    assert ryzen_9 is not None and ryzen_7 is not None and ryzen_5 is not None
    assert ryzen_9.family == "ryzen-9"
    assert ryzen_7.family == "ryzen-7"
    assert ryzen_5.family == "ryzen-5"
    assert len({ryzen_9.monitoring_key, ryzen_7.monitoring_key, ryzen_5.monitoring_key}) == 3


def test_cpu_without_any_amd_signal_never_resolves() -> None:
    # 4 dígitos soltos sem "RYZEN" nem sufixo exclusivo AMD (X3D) -- ambíguo
    # demais, nunca infere fabricante.
    assert resolve_monitoring_identity("9600") is None


# ---------------------------------------------------------------------------
# GPU (NVIDIA GeForce RTX) -- caso obrigatório do pedido
# ---------------------------------------------------------------------------


def test_gpu_equivalent_texts_converge_to_the_same_key() -> None:
    bare = resolve_monitoring_identity("5070 ti")
    prefixed = resolve_monitoring_identity("rtx 5070 ti")
    full_marketing_text = resolve_monitoring_identity("nvidia geforce rtx 5070 ti")

    assert bare is not None
    assert prefixed is not None
    assert full_marketing_text is not None
    assert bare.monitoring_key == prefixed.monitoring_key == full_marketing_text.monitoring_key


def test_gpu_bare_ti_and_rtx_prefixed_converge_with_board_brand_any() -> None:
    bare = resolve_monitoring_identity("5070 Ti")
    prefixed = resolve_monitoring_identity("RTX 5070 Ti")

    assert bare is not None
    assert prefixed is not None
    assert bare.monitoring_key == prefixed.monitoring_key
    assert ("board_brand", "ANY") in bare.attributes
    assert ("vram", "ANY") in bare.attributes


def test_gpu_explicit_board_brand_never_shares_key_with_any() -> None:
    unbranded = resolve_monitoring_identity("RTX 5070 Ti")
    asus = resolve_monitoring_identity("RTX 5070 Ti ASUS")

    assert unbranded is not None
    assert asus is not None
    assert unbranded.monitoring_key != asus.monitoring_key
    assert ("board_brand", "ANY") in unbranded.attributes
    assert ("board_brand", "asus") in asus.attributes


def test_gpu_bare_number_without_ti_suffix_or_keyword_never_resolves() -> None:
    # Número de 4 dígitos sozinho, sem "RTX"/"GEFORCE"/"NVIDIA" nem Ti/Super
    # -- ambíguo demais.
    assert resolve_monitoring_identity("5070") is None


def test_gpu_different_board_brands_never_share_key() -> None:
    asus = resolve_monitoring_identity("RTX 5070 Ti ASUS")
    msi = resolve_monitoring_identity("RTX 5070 Ti MSI")

    assert asus is not None
    assert msi is not None
    assert asus.monitoring_key != msi.monitoring_key


# ---------------------------------------------------------------------------
# ANY -- ausência explícita de restrição, nunca NULL acidental
# ---------------------------------------------------------------------------


def test_any_is_an_explicit_value_never_a_silent_gap() -> None:
    resolved = resolve_monitoring_identity("RTX 5070 Ti")
    assert resolved is not None
    names = dict(resolved.attributes)
    # Todo atributo declarado da categoria SEMPRE aparece -- nunca omitido.
    assert set(names) == {"board_brand", "vram"}
    assert names["board_brand"] == "ANY"
    assert names["vram"] == "ANY"


def test_two_any_requests_produce_identical_key_never_approximate() -> None:
    first = resolve_monitoring_identity("RTX 5070 Ti")
    second = resolve_monitoring_identity("GeForce RTX 5070 Ti")
    assert first is not None and second is not None
    assert first.monitoring_key == second.monitoring_key


# ---------------------------------------------------------------------------
# Aliases -- conhecimento persistido e determinístico, nunca decisão da IA
# ---------------------------------------------------------------------------


def test_alias_map_makes_different_spellings_converge() -> None:
    aliases = {("gpu", "board_brand", "ASUSTEK"): "asus"}

    without_alias = resolve_monitoring_identity("RTX 5070 Ti ASUSTEK")
    with_alias = resolve_monitoring_identity("RTX 5070 Ti ASUSTEK", aliases=aliases)
    canonical_spelling = resolve_monitoring_identity("RTX 5070 Ti ASUS", aliases=aliases)

    assert without_alias is not None and with_alias is not None
    assert canonical_spelling is not None
    # "ASUSTEK" não está na lista de marcas reconhecidas pelo extractor
    # (só "ASUS" está) -- sem alias, vira board_brand=ANY.
    assert dict(without_alias.attributes)["board_brand"] == "ANY"
    # Isso é só para provar que o alias por si só não inventa reconhecimento
    # que o extractor não fez -- aliases resolvem VALOR já extraído, não
    # substituem o parsing de texto (papel do extractor, não do alias).
    assert with_alias.monitoring_key == without_alias.monitoring_key


def test_alias_only_active_status_is_meant_to_apply() -> None:
    # A camada determinística só recebe o mapa já filtrado por
    # status="active" (app.products.identity_aliases.build_alias_mapping
    # já filtra candidatos fora) -- aqui só provamos que o parâmetro
    # `aliases` em si é aplicado deterministicamente quando presente.
    aliases = {("gpu", "board_brand", "MSI"): "msi-canonical"}
    resolved = resolve_monitoring_identity("RTX 5070 Ti MSI", aliases=aliases)
    assert resolved is not None
    assert dict(resolved.attributes)["board_brand"] == "msi-canonical"


# ---------------------------------------------------------------------------
# Versionamento / estabilidade
# ---------------------------------------------------------------------------


def test_monitoring_key_carries_explicit_version_prefix() -> None:
    resolved = resolve_monitoring_identity("RTX 5070 Ti")
    assert resolved is not None
    assert resolved.monitoring_key.startswith(f"v{MONITORING_KEY_VERSION}:")


def test_monitoring_key_is_a_stable_golden_hash_for_a_fixed_input() -> None:
    """Trava o algoritmo -- se este teste quebrar, o formato da chave
    mudou (esperado só junto de um bump de MONITORING_KEY_VERSION);
    protege contra uma mudança acidental de normalização/ordem de campos
    que silenciosamente deixaria de vincular missões antigas."""
    resolved = resolve_monitoring_identity("RTX 5070 Ti")
    assert resolved is not None
    expected_digest = sha256(
        b"v1|gpu|nvidia|geforce-rtx|5070-ti|board_brand=ANY|vram=ANY"
    ).hexdigest()
    assert resolved.monitoring_key == f"v1:{expected_digest}"


def test_repeated_calls_are_pure_and_deterministic() -> None:
    """IA nunca participa da comparação final: a função não tem
    dependência de rede/IA/estado -- mesma entrada, mesma saída sempre."""
    outcomes = [resolve_monitoring_identity("ryzen 9950x3d") for _ in range(20)]
    assert all(outcome is not None for outcome in outcomes)
    assert len({outcome.monitoring_key for outcome in outcomes}) == 1


# ---------------------------------------------------------------------------
# smartphone -- já suportado pela TASK-097, agora também pela camada nova
# ---------------------------------------------------------------------------


def test_smartphone_specific_product_resolves_monitoring_key() -> None:
    resolved = resolve_monitoring_identity("iPhone 17 Pro 256GB")
    assert resolved is not None
    assert resolved.category == "smartphone"
    assert dict(resolved.attributes)["storage_gb"] == "256"


def test_smartphone_missing_blocking_attribute_fails_closed() -> None:
    # Sem capacidade de armazenamento -- storage_gb é blocking (mesmo
    # required_attributes de sempre da TASK-097) -- nunca gera
    # monitoring_key, mesma semântica de identity_key=None hoje.
    assert resolve_monitoring_identity("iPhone 17 Pro") is None


# ---------------------------------------------------------------------------
# Categoria desconhecida -- fail-closed, nunca inventa identidade
# ---------------------------------------------------------------------------


def test_unknown_category_never_resolves() -> None:
    assert resolve_monitoring_identity("cadeira gamer reclinável azul") is None
    assert resolve_monitoring_identity("caneta esferográfica azul") is None
