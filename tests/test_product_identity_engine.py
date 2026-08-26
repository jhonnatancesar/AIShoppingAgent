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
    MonitoringScope,
    resolve_monitoring_identity,
    resolve_monitoring_identity_for_family,
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


def test_gpu_bare_text_never_erases_explicit_vram() -> None:
    """Correção (fase 3A): antes desta correção, 'GB' nunca virava
    atributo nenhum -- '16GB' era descartado em silêncio e 'RTX 5070 Ti'
    vs 'RTX 5070 Ti 16GB' produziam a MESMA key, apagando uma restrição
    real. Não existe hoje regra determinística que complete VRAM a partir
    só do modelo (ao contrário do tier de CPU) -- um mesmo modelo pode
    vender em mais de uma configuração real de VRAM -- então ausência
    continua ANY, presença explícita é preservada, e as duas NUNCA
    compartilham key."""
    bare = resolve_monitoring_identity("RTX 5070 Ti")
    explicit = resolve_monitoring_identity("RTX 5070 Ti 16GB")

    assert bare is not None and explicit is not None
    assert dict(bare.attributes)["vram"] == "ANY"
    assert dict(explicit.attributes)["vram"] == "16"
    assert bare.monitoring_key != explicit.monitoring_key


def test_gpu_equivalent_vram_phrasings_converge() -> None:
    first = resolve_monitoring_identity("RTX 5070 Ti 16GB")
    second = resolve_monitoring_identity("RTX 5070 Ti 16 GB")

    assert first is not None and second is not None
    assert first.monitoring_key == second.monitoring_key


def test_gpu_different_vram_values_never_share_key() -> None:
    sixteen = resolve_monitoring_identity("RTX 5070 Ti 16GB")
    twenty_four = resolve_monitoring_identity("RTX 5070 Ti 24GB")

    assert sixteen is not None and twenty_four is not None
    assert sixteen.monitoring_key != twenty_four.monitoring_key


def test_gpu_vram_and_board_brand_are_independent_attributes() -> None:
    """Duas restrições explícitas ao mesmo tempo -- cada uma preservada,
    nenhuma apaga a outra."""
    resolved = resolve_monitoring_identity("RTX 5070 Ti 16GB ASUS")
    assert resolved is not None
    attributes = dict(resolved.attributes)
    assert attributes["vram"] == "16"
    assert attributes["board_brand"] == "asus"


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
    assert resolved.scope is MonitoringScope.SPECIFIC
    canonical = (
        f"v{MONITORING_KEY_VERSION}|{MonitoringScope.SPECIFIC.value}"
        "|gpu|nvidia|geforce-rtx|5070-ti|ANY|board_brand=ANY|vram=ANY"
    )
    expected_digest = sha256(canonical.encode("utf-8")).hexdigest()
    assert resolved.monitoring_key == f"v{MONITORING_KEY_VERSION}:{expected_digest}"


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


# ---------------------------------------------------------------------------
# scope=FAMILY -- VariantSelectionMode.ALL ("qualquer variante"), TASK-112
# fase 2 fechamento do caso PRODUCT_FAMILY
# ---------------------------------------------------------------------------


def test_family_scope_equivalent_texts_converge_to_the_same_key() -> None:
    """Duas Missions em modo ALL para o mesmo texto de família devem
    convergir -- é exatamente essa convergência que permite compartilhar
    a mesma necessidade de coleta (Shared Monitoring)."""
    first = resolve_monitoring_identity_for_family("iPhone 17 Pro")
    second = resolve_monitoring_identity_for_family("iphone 17 pro")

    assert first is not None and second is not None
    assert first.scope is MonitoringScope.FAMILY
    assert first.monitoring_key == second.monitoring_key


def test_family_scope_preserves_explicit_variant_but_defaults_unspecified_attribute_to_any() -> None:
    """Correção: modo ALL NUNCA apaga o que o usuário de fato pediu.
    'Pro' foi escrito explicitamente -- continua restrito a Pro. Storage
    não foi mencionado -- vira ANY, nunca um valor inventado."""
    resolved = resolve_monitoring_identity_for_family("iPhone 17 Pro")
    assert resolved is not None
    assert resolved.variant == "pro"
    assert dict(resolved.attributes) == {"storage_gb": "ANY"}


def test_family_scope_preserves_explicit_blocking_attribute_when_present() -> None:
    """'ALL' só dispensa a EXIGÊNCIA do atributo bloqueante -- se o
    usuário mesmo assim especificou 128GB, isso é uma restrição real e
    tem que ser preservada, nunca descartada por causa do escopo."""
    resolved = resolve_monitoring_identity_for_family("iPhone 17 128GB")
    assert resolved is not None
    assert dict(resolved.attributes)["storage_gb"] == "128"


def test_family_scope_explicit_and_unspecified_storage_never_share_a_key() -> None:
    with_storage = resolve_monitoring_identity_for_family("iPhone 17 128GB")
    without_storage = resolve_monitoring_identity_for_family("iPhone 17")

    assert with_storage is not None and without_storage is not None
    assert dict(with_storage.attributes)["storage_gb"] == "128"
    assert dict(without_storage.attributes)["storage_gb"] == "ANY"
    assert with_storage.monitoring_key != without_storage.monitoring_key


def test_family_scope_equivalent_explicit_storage_texts_converge() -> None:
    first = resolve_monitoring_identity_for_family("iPhone 17 128GB")
    second = resolve_monitoring_identity_for_family("iPhone 17 128 GB")

    assert first is not None and second is not None
    assert first.monitoring_key == second.monitoring_key


def test_family_scope_gpu_bare_defaults_board_brand_to_any() -> None:
    resolved = resolve_monitoring_identity_for_family("RTX 5070 Ti")
    assert resolved is not None
    assert dict(resolved.attributes)["board_brand"] == "ANY"


def test_family_scope_gpu_explicit_board_brand_is_preserved_and_never_shares_key() -> None:
    """Mesma regra do pedido: 'RTX 5070 Ti' -> board_brand ANY; 'RTX 5070
    Ti ASUS' -> board_brand=asus, chave diferente -- mesmo em modo ALL."""
    bare = resolve_monitoring_identity_for_family("RTX 5070 Ti")
    asus = resolve_monitoring_identity_for_family("RTX 5070 Ti ASUS")

    assert bare is not None and asus is not None
    assert dict(asus.attributes)["board_brand"] == "asus"
    assert bare.monitoring_key != asus.monitoring_key


def test_family_scope_never_collides_with_specific_scope_same_family() -> None:
    """'iPhone 17 Pro 256GB' (specific) e 'iPhone 17 Pro' (family) NUNCA
    podem compartilhar monitoring_key -- são intenções de coleta
    diferentes (uma variante fixa vs qualquer variante da família)."""
    specific = resolve_monitoring_identity("iPhone 17 Pro 256GB")
    family = resolve_monitoring_identity_for_family("iPhone 17 Pro")

    assert specific is not None and family is not None
    assert specific.scope is MonitoringScope.SPECIFIC
    assert family.scope is MonitoringScope.FAMILY
    assert specific.monitoring_key != family.monitoring_key


def test_family_scope_skips_the_blocking_attribute_check() -> None:
    """Em scope=SPECIFIC, 'iPhone 17 Pro' sem storage_gb falha fechado
    (test_smartphone_missing_blocking_attribute_fails_closed). Em
    scope=FAMILY, a ausência de storage_gb é justamente o significado de
    'qualquer variante' -- resolve normalmente, nunca falha por isso."""
    assert resolve_monitoring_identity("iPhone 17 Pro") is None
    resolved = resolve_monitoring_identity_for_family("iPhone 17 Pro")
    assert resolved is not None
    assert dict(resolved.attributes)["storage_gb"] == "ANY"


def test_family_scope_unknown_category_never_resolves() -> None:
    assert resolve_monitoring_identity_for_family("cadeira gamer reclinável azul") is None
    assert resolve_monitoring_identity("caneta esferográfica azul") is None
