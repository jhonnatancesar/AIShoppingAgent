"""Aprendizado de identidade de produto assistido por IA -- Postgres
real (rodada de 2026-09-12). Prova, com banco de verdade: categoria
NOVA (fora dos 5 extratores regex de smartphone/CPU/GPU) resolvida via
IA controlada e grounding determinístico; reuso sem nova chamada de IA
(sobrevive porque é Postgres, não cache em processo); unificação do
MESMO produto vindo de 2 lojas com textos de título diferentes; e
reprocessamento seguro de uma Offer cujo Product não foi resolvido no
momento da coleta original."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.ai_provider import AIRequestError, AIResponse
from app.offers.models import Offer
from app.products.identity_arbiter import ARBITRATE_IDENTITY_PURPOSE
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import (
    reprocess_unresolved_products,
    resolve_or_learn_product_variant,
)
from app.products.models import Product
from app.stores.models import Store
from app.users.models import UserRole
from sqlalchemy import select, text

pytestmark = pytest.mark.integration


class _StaticIdentityAIManager:
    """Fronteira de IA controlada (mesmo espírito de `_AlwaysMatchAIManager`
    desta suíte) -- devolve sempre a MESMA extração estruturada para
    `extract_product_identity_via_ai` (contável em `self.calls`, para
    provar reuso sem nova chamada) e um veredito FIXO para o árbitro
    (`app.products.identity_arbiter`, contável em `self.arbiter_calls`,
    separado de `self.calls` porque representa uma fronteira de IA
    conceitualmente diferente -- checkpoint 3, 2026-09-13). Sem
    `arbiter_verdict` explícito, o stub nunca devolve um JSON de
    veredito válido -- mesmo fail-closed (`INCONCLUSIVE`) que o
    árbitro real aplicaria a um provedor que devolvesse algo
    inesperado."""

    def __init__(self, content: str, *, arbiter_verdict: str | None = None) -> None:
        self._content = content
        self._arbiter_verdict = arbiter_verdict
        self.calls = 0
        self.arbiter_calls = 0

    async def generate(self, request):
        if request.purpose == ARBITRATE_IDENTITY_PURPOSE:
            self.arbiter_calls += 1
            content = (
                json.dumps({"verdict": self._arbiter_verdict})
                if self._arbiter_verdict
                else "sem veredito configurado neste stub"
            )
        else:
            self.calls += 1
            content = self._content
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=content,
            finished_at=datetime.now(UTC),
        )


_MONITOR_EXTRACTION = (
    '{"category": "monitor", "brand": "LG", "family": "UltraGear", '
    '"model": "27GP850", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, '
    '"attributes": {"refresh_rate_hz": "165Hz"}}'
)


def test_new_category_is_learned_and_approved_via_grounded_ai_extraction(
    integration_database,
) -> None:
    """ "monitor" nunca teve extrator regex (só smartphone/CPU/GPU) -- a
    extração assistida por IA, grounded no título real, resolve mesmo
    assim, sem nenhuma alteração de código para essa categoria nova."""
    manager = _StaticIdentityAIManager(_MONITOR_EXTRACTION)
    title = "Monitor Gamer LG UltraGear 27GP850 27 polegadas 165Hz"

    async def _run():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    import asyncio

    resolved = asyncio.run(_run())
    assert resolved is not None
    assert resolved.category == "monitor"
    assert resolved.brand == "lg"
    assert resolved.model == "27gp850"
    assert manager.calls == 1

    with integration_database.sessions.begin() as session:
        candidate = session.scalar(select(ProductIdentityCandidate))
        assert candidate is not None
        assert candidate.status == "approved"
        assert candidate.grounded is True
        assert candidate.identity_key == resolved.identity_key


def test_same_title_reuses_learned_candidate_without_calling_ai_again(
    integration_database,
) -> None:
    """Mesmo título, segunda vez (processo/"restart" diferente -- sessão
    async nova a cada chamada, só o Postgres persiste) -- nunca chama a
    IA de novo; resolve direto do candidato `approved` já persistido."""
    manager = _StaticIdentityAIManager(_MONITOR_EXTRACTION)
    title = "Monitor Gamer LG UltraGear 27GP850 27 polegadas 165Hz"

    import asyncio

    async def _resolve_once():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    first = asyncio.run(_resolve_once())
    assert manager.calls == 1

    second = asyncio.run(_resolve_once())
    assert manager.calls == 1, "reuso esperado -- nenhuma chamada nova à IA"
    assert second is not None
    assert second.identity_key == first.identity_key

    with integration_database.sessions.begin() as session:
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 1, "mesmo título nunca cria um segundo candidato"


def test_two_stores_different_wording_unify_on_same_product(
    integration_database,
) -> None:
    """Amazon e Kabum descrevem o MESMO monitor com textos de anúncio
    diferentes -- cada título distinto passa por sua PRÓPRIA extração
    (hashes de título diferentes, nunca reuso de cache por título
    exato), mas como os campos estruturados extraídos coincidem
    (mesma marca/família/modelo), o `identity_key` calculado é IGUAL --
    por isso as duas ofertas, coletadas de lojas diferentes, resolvem
    para o MESMO Product canônico via `_resolve_global_product`-like
    lookup por `identity_key` (aqui provado diretamente pela igualdade
    das chaves, sem precisar rodar o orchestrator inteiro).

    Achado real ao escrever este teste (rodada de 2026-09-12): o título
    Kabum menciona "27GP850-B" -- o sufixo "-B" pode ser uma REVISÃO/
    VARIANTE real do modelo que o candidato Amazon (aprendido só como
    "27GP850") não capturou. O reconhecimento por tokens
    (`_find_reusable_candidate_by_tokens`/`_has_unrecognized_model_
    suffix`) detecta esse sufixo curto não reconhecido e RECUSA
    reaproveitar por tokens de propósito (fail-closed) -- por isso o
    Kabum ainda dispara sua PRÓPRIA chamada de extração aqui, mesmo
    compartilhando marca/família/modelo-base com o candidato Amazon.
    Como marca/família/modelo já batem por tokens, a zona cinzenta cai
    no árbitro de IA (checkpoint 3, 2026-09-13) em vez de virar
    automaticamente uma extração isolada -- aqui configurado para
    decidir SAME_PRODUCT (é de fato a mesma revisão do mesmo monitor),
    fundindo no MESMO `identity_key` do candidato Amazon via alias, sem
    depender de coincidência entre duas extrações independentes."""
    amazon_title = "Monitor Gamer LG UltraGear 27GP850 27 polegadas 165Hz"
    kabum_title = "LG UltraGear 27GP850-B Monitor Gamer 165Hz FreeSync Premium"
    amazon_manager = _StaticIdentityAIManager(_MONITOR_EXTRACTION)
    kabum_manager = _StaticIdentityAIManager(
        _MONITOR_EXTRACTION, arbiter_verdict="SAME_PRODUCT"
    )

    import asyncio

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_amazon = asyncio.run(_resolve(amazon_manager, amazon_title))
    from_kabum = asyncio.run(_resolve(kabum_manager, kabum_title))

    assert from_amazon is not None and from_kabum is not None
    assert from_amazon.identity_key == from_kabum.identity_key
    assert amazon_manager.calls == 1
    assert kabum_manager.calls == 1

    with integration_database.sessions.begin() as session:
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 2, (
            "dois títulos distintos -- dois candidatos, mesma identity_key"
        )
        assert {c.identity_key for c in candidates} == {from_amazon.identity_key}


def test_reprocess_unresolved_offer_after_definition_is_learned(
    integration_database,
) -> None:
    """Uma Offer coletada ANTES de "monitor" existir como categoria
    aprendida fica com Product ad-hoc (`identity_key IS NULL`,
    `Product.name` = título bruto). Depois que a mesma família/modelo é
    aprendida (aqui, diretamente via `resolve_or_learn_product_variant`
    -- simula uma segunda oferta já resolvida), reprocessar essa Offer
    antiga migra ela para o Product CANÔNICO (mesmo `identity_key`) e
    remove o ad-hoc -- nunca duplica Product, nunca perde a Offer."""
    title = "Monitor Gamer LG UltraGear 27GP850 27 polegadas 165Hz"

    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        ad_hoc_product = Product(id=uuid4(), name=title)
        session.add(ad_hoc_product)
        session.flush()
        offer = Offer(
            product_id=ad_hoc_product.id,
            store_id=store.id,
            external_id="dev-identity-learning-reprocess",
            url="https://example.invalid/dev-identity-learning-reprocess",
        )
        session.add(offer)
        session.flush()
        offer_id = offer.id

    manager = _StaticIdentityAIManager(_MONITOR_EXTRACTION)

    import asyncio

    async def _learn_from_another_offer():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    # A MESMA Offer antiga tem o título EXATO já aprendido acima -- então
    # o reprocessamento a seguir reaproveita o candidato já `approved`
    # sem precisar de uma terceira chamada de IA.
    learned = asyncio.run(_learn_from_another_offer())
    assert learned is not None
    assert manager.calls == 1

    async def _reprocess():
        async with integration_database.async_sessions() as session:
            count = await reprocess_unresolved_products(
                session, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return count

    reprocessed_count = asyncio.run(_reprocess())
    assert reprocessed_count >= 1
    assert manager.calls == 1, (
        "reprocessamento reaproveita o candidato aprendido, sem nova chamada"
    )

    with integration_database.sessions.begin() as session:
        offer_after = session.get(Offer, offer_id)
        assert offer_after is not None
        migrated_product = session.get(Product, offer_after.product_id)
        assert migrated_product is not None
        assert migrated_product.identity_key == learned.identity_key
        # O ad-hoc original não sobrevive só se um canônico DIFERENTE já
        # existia; aqui o ad-hoc pode ter sido o próprio promovido (não
        # havia nenhum outro Product com essa identity_key ainda) -- os
        # dois casos são válidos, o que importa é a Offer nunca ficar
        # órfã e nunca haver dois Products com a MESMA identity_key.
        same_identity_products = list(
            session.scalars(
                select(Product).where(Product.identity_key == learned.identity_key)
            )
        )
        assert len(same_identity_products) == 1


class _ByTitleIdentityAIManager:
    """Fronteira de IA controlada que devolve uma extração DIFERENTE por
    título (chaveada pelo próprio título recebido) -- necessária para
    `test_reprocess_two_distinct_products_both_needing_real_extraction_
    dont_lose_each_other` abaixo, onde os 2 Products têm identidades
    REALMENTE diferentes (precisa provar que o segundo não atropela o
    primeiro, não só que os dois "funcionam")."""

    def __init__(self, extraction_by_title: dict[str, str]) -> None:
        self._by_title = extraction_by_title
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        title = request.messages[-1].content
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self._by_title[title],
            finished_at=datetime.now(UTC),
        )


def test_reprocess_two_distinct_products_both_needing_real_extraction_dont_lose_each_other(
    integration_database,
) -> None:
    """Regressão de um bug REAL encontrado e corrigido em 2026-09-20
    (achado ao implementar lote de IA para TASK-123, mas o bug já
    existia antes, no caminho sequencial de sempre -- nenhum teste
    anterior cobria 2+ Products precisando de extração de IA de
    verdade na MESMA chamada de `reprocess_unresolved_products`, só 1):

    1. CRASH: o `session.rollback()` que protege contra
    `idle_in_transaction_session_timeout` antes de CADA chamada de IA
    expira TODAS as instâncias já carregadas da sessão -- reler
    `Product.name` de um Product ainda não processado (carregado
    junto no mesmo `SELECT` inicial) depois desse rollback disparava
    um lazy-load síncrono fora do greenlet async.

    2. PERDA SILENCIOSA: mesmo sem crashar, esse MESMO rollback desfaz
    a transação inteira -- se o primeiro Product já tinha sido
    resolvido e aplicado (mas ainda não commitado) quando o segundo
    precisa de nova chamada de IA, o rollback do segundo apagava o
    trabalho do primeiro.

    Corrigido capturando `(id, title)` como valores simples antes de
    qualquer rollback, e commitando cada resolução imediatamente
    quando `apply=True` (aqui: `reprocess_unresolved_products` chamado
    com uma sessão real, seguido de commit do chamador -- o ponto é
    que NENHUM dos dois Products pode desaparecer ou quebrar)."""
    title_a = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5"
    title_b = "Placa-mãe Gigabyte AORUS Elite AX B650 DDR5 AM5"

    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        product_a = Product(id=uuid4(), name=title_a)
        product_b = Product(id=uuid4(), name=title_b)
        session.add_all([product_a, product_b])
        session.flush()
        session.add_all(
            [
                Offer(
                    product_id=product_a.id,
                    store_id=store.id,
                    external_id="dev-identity-reprocess-distinct-a",
                    url="https://example.invalid/dev-identity-reprocess-distinct-a",
                ),
                Offer(
                    product_id=product_b.id,
                    store_id=store.id,
                    external_id="dev-identity-reprocess-distinct-b",
                    url="https://example.invalid/dev-identity-reprocess-distinct-b",
                ),
            ]
        )
        session.flush()
        product_id_a, product_id_b = product_a.id, product_b.id

    extraction_a = json.dumps(
        {
            "category": "motherboard",
            "brand": "ASUS",
            "family": "TUF Gaming",
            "model": "B650-Plus",
            "variant": "wifi",
            "store_sku": None,
            "manufacturer_part_number": None,
            "attributes": {"socket": "AM5", "chipset": "B650"},
        }
    )
    extraction_b = json.dumps(
        {
            "category": "motherboard",
            "brand": "Gigabyte",
            "family": "AORUS Elite",
            "model": "AX",
            "variant": None,
            "store_sku": None,
            "manufacturer_part_number": None,
            "attributes": {"socket": "AM5", "chipset": "B650"},
        }
    )
    manager = _ByTitleIdentityAIManager({title_a: extraction_a, title_b: extraction_b})

    async def _reprocess():
        async with integration_database.async_sessions() as session:
            count = await reprocess_unresolved_products(
                session,
                ai_manager=manager,
                profile=UserRole.ADMIN,
                arbiter_ai_manager=manager,
                apply=True,
            )
            await session.commit()
            return count

    reprocessed_count = asyncio.run(_reprocess())
    assert reprocessed_count == 2
    assert manager.calls == 2

    with integration_database.sessions.begin() as session:
        resolved_a = session.get(Product, product_id_a)
        resolved_b = session.get(Product, product_id_b)
        assert resolved_a is not None and resolved_a.identity_key is not None, (
            "Product A não pode desaparecer nem ficar sem identidade"
        )
        assert resolved_b is not None and resolved_b.identity_key is not None, (
            "Product B não pode desaparecer nem ficar sem identidade"
        )
        assert resolved_a.identity_key != resolved_b.identity_key


# ---------------------------------------------------------------------------
# Placas-mãe -- categoria pedida explicitamente pelo dono do produto, coleta
# de lojas DIFERENTES com textos DIFERENTES (não o mesmo título reprocessado,
# que sozinho não prova aprendizado REUTILIZÁVEL -- ver docstring de
# `identity_learning._find_reusable_candidate_by_tokens`), reuso por tokens
# contra conhecimento já aprovado (sem nova chamada de IA), e separação de
# variantes por atributo (DDR4 vs DDR5), nunca só por marca/família/modelo.
# ---------------------------------------------------------------------------

_MOBO_DDR5_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, "attributes": {"memory_type": "DDR5"}}'
)
_MOBO_DDR4_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, "attributes": {"memory_type": "DDR4"}}'
)


def test_motherboard_different_stores_different_wording_reuses_by_tokens_without_new_ai_call(
    integration_database,
) -> None:
    """Placa-mãe real, coletada de DUAS lojas com wording DIFERENTE:
    Amazon aprende via IA (1a chamada); Kabum, com um título totalmente
    diferente mas mencionando a MESMA marca/família/modelo/DDR, reaproveita
    o candidato JÁ APROVADO por reconhecimento de tokens (`_find_
    reusable_candidate_by_tokens`) -- SEM uma segunda chamada de IA. Isto é
    o que prova aprendizado REUTILIZÁVEL de verdade: o segundo título nunca
    é idêntico ao primeiro (não é cache de mesmo texto)."""
    amazon_title = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5"
    kabum_title = (
        "ASUS TUF GAMING B650-PLUS (WI-FI) DDR5 - Chipset AMD B650, Socket AM5"
    )
    amazon_manager = _StaticIdentityAIManager(_MOBO_DDR5_EXTRACTION)
    kabum_manager = _StaticIdentityAIManager(_MOBO_DDR5_EXTRACTION)

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_amazon = asyncio.run(_resolve(amazon_manager, amazon_title))
    assert from_amazon is not None
    assert amazon_manager.calls == 1

    from_kabum = asyncio.run(_resolve(kabum_manager, kabum_title))
    assert from_kabum is not None
    assert kabum_manager.calls == 0, (
        "título diferente da MESMA placa-mãe/DDR precisa reaproveitar por "
        "tokens -- nunca uma nova chamada de IA"
    )
    assert from_kabum.identity_key == from_amazon.identity_key
    assert from_kabum.category == "motherboard"
    assert from_kabum.attributes == (("memory-type", "DDR5"),)

    with integration_database.sessions.begin() as session:
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 2, (
            "dois títulos distintos -- dois registros (hash exato de cada "
            "um), mesma identity_key -- reuso por tokens ainda persiste um "
            "alias para o hash do segundo título, para resolver ainda mais "
            "rápido (por hash exato) numa próxima vez"
        )
        assert {c.identity_key for c in candidates} == {from_amazon.identity_key}
        assert all(c.status == "approved" for c in candidates)


def test_motherboard_different_memory_variant_never_reuses_and_gets_distinct_identity(
    integration_database,
) -> None:
    """Mesma marca/família/modelo de placa-mãe, mas DDR4 em vez de DDR5 --
    variante DIFERENTE (atributo distinto), nunca reaproveitada pelo
    reconhecimento por tokens (`memory_type=DDR5` do candidato aprovado não
    bate contra um título que diz DDR4) -- dispara sua PRÓPRIA extração via
    IA. Como marca/família/modelo ainda batem por tokens, a zona cinzenta
    cai no árbitro de IA (checkpoint 3, 2026-09-13); aqui configurado para
    decidir DIFFERENT_PRODUCT (é de fato uma contradição real de atributo,
    não uma diferença de escrita) -- recebe um `identity_key` DIFERENTE do
    DDR5, nunca colapsado no mesmo produto só porque marca/família/modelo
    coincidem."""
    ddr5_title = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5"
    ddr4_title = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR4 AM5"
    ddr5_manager = _StaticIdentityAIManager(_MOBO_DDR5_EXTRACTION)
    ddr4_manager = _StaticIdentityAIManager(
        _MOBO_DDR4_EXTRACTION, arbiter_verdict="DIFFERENT_PRODUCT"
    )

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_ddr5 = asyncio.run(_resolve(ddr5_manager, ddr5_title))
    from_ddr4 = asyncio.run(_resolve(ddr4_manager, ddr4_title))

    assert from_ddr5 is not None and from_ddr4 is not None
    assert ddr5_manager.calls == 1
    assert ddr4_manager.calls == 1, (
        "DDR4 nunca deveria reaproveitar o candidato DDR5 -- precisa da "
        "sua PRÓPRIA extração via IA"
    )
    assert from_ddr5.identity_key != from_ddr4.identity_key, (
        "DDR4 e DDR5 são variantes DISTINTAS da mesma placa-mãe -- nunca "
        "o mesmo identity_key só porque marca/família/modelo coincidem"
    )


_MOBO_DDR5_WITH_PART_NUMBER_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": "AMZ-B650TUF-001", '
    '"manufacturer_part_number": "90MB1CG0-M0EAY0", '
    '"attributes": {"memory_type": "DDR5"}}'
)
_MOBO_DDR4_SAME_PART_NUMBER_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": "KBM-99887", '
    '"manufacturer_part_number": "90MB1CG0M0EAY0", '
    '"attributes": {"memory_type": "DDR4"}}'
)


def test_matching_manufacturer_part_number_merges_without_spending_an_arbiter_call(
    integration_database,
) -> None:
    """checkpoint 3, seção 6: evidência determinística tem prioridade sobre
    o árbitro de IA -- quando as duas lojas informam o MESMO
    `manufacturer_part_number` (mesmo escrito com pontuação diferente,
    `values_match_ignoring_punctuation`), a fusão acontece SEM gastar
    nenhuma chamada ao árbitro, mesmo quando o atributo reportado
    diverge (DDR4 vs DDR5 aqui -- provavelmente erro de catalogação de
    uma das lojas, não uma variante real, já que o FABRICANTE confirma
    ser a MESMA peça). `store_sku` DIFERE entre as duas lojas de
    propósito (nunca é comparável entre lojas -- só o
    `manufacturer_part_number`, atribuído pelo fabricante, é evidência
    de igualdade entre lojas)."""
    amazon_title = (
        "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5 "
        "SKU AMZ-B650TUF-001 P/N 90MB1CG0-M0EAY0"
    )
    kabum_title = (
        "Placa-mãe ASUS TUF GAMING B650-PLUS (WI-FI) DDR4 - Chipset AMD "
        "B650, Socket AM5 - Codigo KBM-99887 - MPN 90MB1CG0M0EAY0"
    )
    amazon_manager = _StaticIdentityAIManager(_MOBO_DDR5_WITH_PART_NUMBER_EXTRACTION)
    # Sem `arbiter_verdict`: se o código chamar o árbitro por engano aqui,
    # o stub devolve um JSON que não é um veredito válido, e o árbitro
    # fail-closed para INCONCLUSIVE -- o teste falharia de forma visível
    # (nenhum merge) em vez de passar silenciosamente por acidente.
    kabum_manager = _StaticIdentityAIManager(_MOBO_DDR4_SAME_PART_NUMBER_EXTRACTION)

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_amazon = asyncio.run(_resolve(amazon_manager, amazon_title))
    from_kabum = asyncio.run(_resolve(kabum_manager, kabum_title))

    assert from_amazon is not None and from_kabum is not None
    assert amazon_manager.calls == 1
    assert kabum_manager.calls == 1
    assert kabum_manager.arbiter_calls == 0, (
        "manufacturer_part_number igual (só com pontuação diferente) é "
        "evidência forte o bastante para fundir sem gastar uma chamada "
        "de IA no árbitro"
    )
    assert from_amazon.identity_key == from_kabum.identity_key, (
        "mesmo manufacturer_part_number -- MESMA peça do fabricante, "
        "mesmo identity_key, apesar do atributo divergente reportado"
    )

    with integration_database.sessions.begin() as session:
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 2
        assert {c.store_sku for c in candidates} == {
            "AMZ-B650TUF-001",
            "KBM-99887",
        }, "store_sku precisa continuar distinto por loja, nunca comparado entre lojas"
        assert all(c.manufacturer_part_number is not None for c in candidates)


_MOBO_DDR5_INCONCLUSIVE_BASE_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, "attributes": {"memory_type": "DDR5"}}'
)
_MOBO_DDR5_INCONCLUSIVE_CHALLENGER_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, "attributes": {"memory_type": "DDR3"}}'
)


def test_inconclusive_arbiter_verdict_never_merges_and_stays_fail_closed(
    integration_database,
) -> None:
    """Zona cinzenta em que o árbitro não consegue decidir com segurança
    (`INCONCLUSIVE`, ex.: evidência insuficiente/contraditória) -- fail
    -closed: NUNCA funde no candidato existente, mesmo compartilhando
    marca/família/modelo por tokens. A segunda oferta segue seu próprio
    caminho normal de extração/grounding e recebe um `identity_key`
    PRÓPRIO, distinto do primeiro -- o mesmo resultado prático de um
    DIFFERENT_PRODUCT, mas chegando por incerteza, não por contradição
    confirmada."""
    first_title = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5"
    second_title = (
        "ASUS TUF GAMING B650-PLUS (WI-FI) DDR3 - Chipset AMD B650, Socket AM5"
    )
    first_manager = _StaticIdentityAIManager(_MOBO_DDR5_INCONCLUSIVE_BASE_EXTRACTION)
    second_manager = _StaticIdentityAIManager(
        _MOBO_DDR5_INCONCLUSIVE_CHALLENGER_EXTRACTION, arbiter_verdict="INCONCLUSIVE"
    )

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_first = asyncio.run(_resolve(first_manager, first_title))
    from_second = asyncio.run(_resolve(second_manager, second_title))

    assert from_first is not None and from_second is not None
    assert first_manager.calls == 1
    assert second_manager.calls == 1
    assert second_manager.arbiter_calls == 1
    assert from_first.identity_key != from_second.identity_key, (
        "INCONCLUSIVE nunca funde -- fail-closed, cada título fica com "
        "seu próprio identity_key até haver evidência melhor"
    )

    with integration_database.sessions.begin() as session:
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 2
        assert all(c.status == "approved" for c in candidates), (
            "INCONCLUSIVE não impede a segunda oferta de ser aprovada "
            "como sua PRÓPRIA identidade -- só impede a fusão automática "
            "com o candidato incerto"
        )


class _ProfileGatedIdentityAIManager:
    """Fake que reproduz FIELMENTE a validação real de
    `CesarCoreAIProviderManager.generate` (`app.ai_provider.manager`):
    rejeita com `AIRequestError` qualquer `request.profile` fora de
    `allowed_profiles` -- diferente de `_StaticIdentityAIManager` (que
    ignora profile por completo). Existe para provar, com Postgres
    real, o achado do checkpoint 3 (2026-09-13): `identity_arbiter`
    sempre pede `profile=UserRole.USER`; um manager ADMIN/DEV (a wiring
    real de `app.collection.worker.run_worker`) rejeita essa chamada se
    for reaproveitado para o árbitro sem um `arbiter_ai_manager`
    dedicado."""

    def __init__(self, content: str, *, allowed_profiles: frozenset[UserRole]) -> None:
        self._content = content
        self._allowed_profiles = allowed_profiles
        self.calls = 0
        self.arbiter_calls = 0

    async def generate(self, request):
        if request.profile not in self._allowed_profiles:
            raise AIRequestError("Request profile does not match manager")
        if request.purpose == ARBITRATE_IDENTITY_PURPOSE:
            self.arbiter_calls += 1
        else:
            self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self._content,
            finished_at=datetime.now(UTC),
        )


def test_gray_zone_stays_inconclusive_when_arbiter_reuses_admin_dev_manager(
    integration_database,
) -> None:
    """Reproduz o BUG real encontrado (checkpoint 3, 2026-09-13) antes da
    correção: sem um `arbiter_ai_manager` dedicado, `resolve_or_learn_
    product_variant` só recebe o manager ADMIN/DEV (mesma wiring de
    `app.collection.worker.run_worker`) -- o árbitro tenta chamá-lo com
    `profile=UserRole.USER`, o manager rejeita (`AIRequestError`), e o
    `except Exception` fail-closed do árbitro devolve `INCONCLUSIVE`
    silenciosamente, mesmo quando as duas ofertas são o MESMO produto
    físico. Continua fail-closed (nunca funde por engano), mas prova
    que o árbitro nunca decide de verdade nesta wiring -- exatamente o
    comportamento real observado em produção antes da correção."""
    admin_dev_only = frozenset({UserRole.ADMIN, UserRole.DEV})
    first_title = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5 (gray zone A)"
    second_title = (
        "ASUS TUF GAMING B650-PLUS (WI-FI) DDR4 - Chipset AMD B650, "
        "Socket AM5 (gray zone A)"
    )
    first_manager = _ProfileGatedIdentityAIManager(
        _MOBO_DDR5_EXTRACTION, allowed_profiles=admin_dev_only
    )
    second_manager = _ProfileGatedIdentityAIManager(
        _MOBO_DDR4_EXTRACTION, allowed_profiles=admin_dev_only
    )

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_first = asyncio.run(_resolve(first_manager, first_title))
    from_second = asyncio.run(_resolve(second_manager, second_title))

    assert from_first is not None and from_second is not None
    assert second_manager.arbiter_calls == 0, (
        "o manager ADMIN/DEV rejeita a requisição do árbitro (profile "
        "USER) antes mesmo de contar como uma chamada bem-sucedida"
    )
    assert from_first.identity_key != from_second.identity_key, (
        "sem arbiter_ai_manager dedicado, o árbitro nunca decide de "
        "verdade nesta wiring -- cai em INCONCLUSIVE fail-closed, "
        "nunca funde (mesmo sendo o mesmo produto físico real)"
    )


def test_gray_zone_resolves_for_real_with_dedicated_user_arbiter_manager(
    integration_database,
) -> None:
    """Correção real (checkpoint 3, 2026-09-13): passando um `arbiter_
    ai_manager` USER-only dedicado (produção: `build_user_ai_provider_
    manager`), o árbitro consegue de fato chamar a IA e decidir a zona
    cinzenta -- mesmo com o `ai_manager` principal restrito a ADMIN/DEV
    (mesma wiring real de `app.collection.worker.run_worker`)."""
    admin_dev_only = frozenset({UserRole.ADMIN, UserRole.DEV})
    user_only = frozenset({UserRole.USER})
    first_title = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5 (gray zone B)"
    second_title = (
        "ASUS TUF GAMING B650-PLUS (WI-FI) DDR4 - Chipset AMD B650, "
        "Socket AM5 (gray zone B)"
    )
    first_manager = _ProfileGatedIdentityAIManager(
        _MOBO_DDR5_EXTRACTION, allowed_profiles=admin_dev_only
    )
    second_manager = _ProfileGatedIdentityAIManager(
        _MOBO_DDR4_EXTRACTION, allowed_profiles=admin_dev_only
    )
    arbiter_manager = _ProfileGatedIdentityAIManager(
        json.dumps({"verdict": "SAME_PRODUCT"}), allowed_profiles=user_only
    )

    async def _resolve(manager, title, *, arbiter_ai_manager=None):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session,
                raw_title=title,
                ai_manager=manager,
                profile=UserRole.ADMIN,
                arbiter_ai_manager=arbiter_ai_manager,
            )
            await session.commit()
            return resolved

    from_first = asyncio.run(_resolve(first_manager, first_title))
    from_second = asyncio.run(
        _resolve(second_manager, second_title, arbiter_ai_manager=arbiter_manager)
    )

    assert from_first is not None and from_second is not None
    assert arbiter_manager.arbiter_calls == 1, (
        "o manager USER dedicado precisa ter recebido de verdade a "
        "chamada do árbitro (não o manager ADMIN/DEV principal)"
    )
    assert from_first.identity_key == from_second.identity_key, (
        "com o manager correto, o árbitro decide SAME_PRODUCT de "
        "verdade e funde no MESMO identity_key"
    )


# ---------------------------------------------------------------------------
# Redesenho MATCH/MISSING/CONTRADICTORY (pedido de 2026-09-14, seção 3) --
# prova de ponta a ponta (Postgres real) que uma dimensão apenas AUSENTE de
# um lado (MISSING) nunca, sozinha, bloqueia o reaproveitamento
# determinístico do candidato já aprovado nem gasta uma chamada ao árbitro
# de IA -- só uma CONTRADIÇÃO real (valor presente e incompatível nos dois
# lados) justifica gastar essa chamada.
# ---------------------------------------------------------------------------

_MOBO_WIFI_WITH_PART_NUMBER_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": "wifi", "store_sku": "AMZ-B650TUF-002", '
    '"manufacturer_part_number": "90MB1CG0-M0EAY0", '
    '"attributes": {"memory_type": "DDR5"}}'
)
_MOBO_NO_WIFI_NO_PART_NUMBER_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": "KBM-77665", '
    '"manufacturer_part_number": null, "attributes": {"memory_type": "DDR5"}}'
)


def test_manufacturer_part_number_missing_on_one_side_still_merges_without_arbiter_call(
    integration_database,
) -> None:
    """Seção 11, item 5 do pedido de 2026-09-14: `manufacturer_part_number`
    conhecido só de UM lado (a segunda loja simplesmente nunca imprime o
    P/N no título) é MISSING, nunca CONTRADICTORY -- não pode, sozinho,
    impedir o reaproveitamento do candidato já aprovado. O segundo título
    também omite "WiFi" (variant conhecida do candidato), o que já é
    suficiente para `_find_reusable_candidate_by_tokens` (reconhecimento
    puro por token em texto bruto, sem valor observado real) recusar o
    reaproveitamento direto e cair na extração via IA + classificação por
    dimensão -- é exatamente ali, com um valor observado real para
    comparar, que MISSING vs CONTRADICTORY passa a fazer diferença.
    memory_type (DDR5) é idêntico nos dois títulos (MATCH), variant e
    manufacturer_part_number são MISSING dos dois lados -- nenhuma
    dimensão é CONTRADICTORY, então o merge acontece automaticamente,
    sem gastar nenhuma chamada ao árbitro."""
    amazon_title = (
        "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5 "
        "SKU AMZ-B650TUF-002 P/N 90MB1CG0-M0EAY0"
    )
    kabum_title = (
        "Placa-mãe ASUS TUF GAMING B650-PLUS DDR5 - Chipset AMD B650, "
        "Socket AM5 - Codigo KBM-77665"
    )
    amazon_manager = _StaticIdentityAIManager(_MOBO_WIFI_WITH_PART_NUMBER_EXTRACTION)
    # Sem `arbiter_verdict`: se o código chamar o árbitro por engano, o
    # stub devolve um JSON inválido e o árbitro fail-closed vira
    # INCONCLUSIVE -- o teste falharia de forma visível (nenhum merge),
    # nunca passaria por acidente escondendo uma regressão.
    kabum_manager = _StaticIdentityAIManager(_MOBO_NO_WIFI_NO_PART_NUMBER_EXTRACTION)

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_amazon = asyncio.run(_resolve(amazon_manager, amazon_title))
    from_kabum = asyncio.run(_resolve(kabum_manager, kabum_title))

    assert from_amazon is not None and from_kabum is not None
    assert amazon_manager.calls == 1
    assert kabum_manager.calls == 1, (
        "título sem 'WiFi' precisa da SUA PRÓPRIA extração via IA -- o "
        "reconhecimento puro por token não pode presumir a variant ausente"
    )
    assert kabum_manager.arbiter_calls == 0, (
        "manufacturer_part_number e variant ausentes de um lado são "
        "MISSING, nunca CONTRADICTORY -- não bastam para justificar uma "
        "chamada ao árbitro de IA"
    )
    assert from_amazon.identity_key == from_kabum.identity_key, (
        "sem nenhuma dimensão CONTRADICTORY, o candidato já aprovado é "
        "reaproveitado -- MISSING nunca impede identidade/reuso"
    )

    with integration_database.sessions.begin() as session:
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 2
        assert {c.store_sku for c in candidates} == {
            "AMZ-B650TUF-002",
            "KBM-77665",
        }


_MOBO_CHIPSET_KNOWN_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "Prime", '
    '"model": "B650M-A", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, "attributes": {"chipset": "B650"}}'
)
_MOBO_CHIPSET_UNMENTIONED_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "Prime", '
    '"model": "B650M-A", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, "attributes": {}}'
)


def test_deterministic_missing_only_case_never_spends_an_arbiter_call(
    integration_database,
) -> None:
    """Seção 11, item 14 do pedido de 2026-09-14: caso determinístico
    CLARO (só existe uma dimensão MISSING -- `chipset`, nunca mencionado
    no segundo título -- nenhuma outra dimensão em jogo: sem variant, sem
    manufacturer_part_number dos dois lados) nunca gasta uma chamada ao
    árbitro de IA. O árbitro continua reservado só para zona cinzenta
    REAL, isto é, quando existe uma CONTRADIÇÃO de verdade (ver
    `test_motherboard_different_memory_variant_never_reuses_and_gets_distinct_identity`
    e `test_inconclusive_arbiter_verdict_never_merges_and_stays_fail_closed`
    para os casos CONTRADICTORY reais desta suíte) -- nunca para compensar
    um MISSING tratado incorretamente como contradição."""
    first_title = "Placa-mãe Asus Prime B650M-A AM5 Chipset B650"
    second_title = "Placa-mãe ASUS PRIME B650M-A - Socket AM5"
    first_manager = _StaticIdentityAIManager(_MOBO_CHIPSET_KNOWN_EXTRACTION)
    second_manager = _StaticIdentityAIManager(_MOBO_CHIPSET_UNMENTIONED_EXTRACTION)

    async def _resolve(manager, title):
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    from_first = asyncio.run(_resolve(first_manager, first_title))
    from_second = asyncio.run(_resolve(second_manager, second_title))

    assert from_first is not None and from_second is not None
    assert first_manager.calls == 1
    assert second_manager.calls == 1, (
        "título sem menção ao chipset precisa da sua PRÓPRIA extração "
        "via IA -- reconhecimento por token não pode presumir o valor "
        "ausente"
    )
    assert second_manager.arbiter_calls == 0, (
        "única dimensão em jogo (chipset) é MISSING, não CONTRADICTORY -- "
        "caso determinístico claro, o árbitro nunca precisa ser chamado"
    )
    assert from_first.identity_key == from_second.identity_key, (
        "MISSING puro nunca impede reaproveitamento determinístico do "
        "candidato já aprovado"
    )


class _SlowStaticIdentityAIManager:
    """Mesmo contrato de `_StaticIdentityAIManager`, mas com um atraso
    artificial (`asyncio.sleep`) antes de responder -- simula o I/O de
    rede real de uma chamada de IA, para provar que a transação NÃO fica
    "idle in transaction" durante essa espera (checkpoint 3, 2026-09-13:
    achado real de produção, `idle_in_transaction_session_timeout` do
    PostgreSQL derrubando a sessão porque a transação ficava aberta e
    ociosa durante a chamada de IA)."""

    def __init__(self, content: str, *, delay_seconds: float) -> None:
        self._content = content
        self._delay_seconds = delay_seconds
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        await asyncio.sleep(self._delay_seconds)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self._content,
            finished_at=datetime.now(UTC),
        )


def test_rollback_before_ai_call_survives_idle_in_transaction_session_timeout(
    integration_database,
) -> None:
    """Regressão do bug real de produção (checkpoint 3, 2026-09-13):
    `resolve_or_learn_product_variant` precisa dar `await session.
    rollback()` ANTES de qualquer chamada de IA (I/O de rede) -- senão a
    sessão fica com uma transação aberta e OCIOSA durante essa espera, e o
    PostgreSQL a derruba assim que `idle_in_transaction_session_timeout`
    é excedido (`InterfaceError`/`InFailedSqlTransaction` no primeiro
    comando seguinte). Prova de verdade, com Postgres real: configura um
    timeout de sessão bem curto, força uma chamada de IA deliberadamente
    mais lenta do que esse timeout, e confirma que a sessão ainda
    consegue: (a) persistir o candidato aprendido, e (b) fazer commit sem
    erro -- só é possível se a transação não estava mais aberta/ociosa
    durante a espera pela IA."""
    manager = _SlowStaticIdentityAIManager(_MONITOR_EXTRACTION, delay_seconds=0.3)
    title = "Monitor Gamer LG UltraGear 27GP850 27 polegadas 165Hz (timeout regression)"

    async def _run():
        async with integration_database.async_sessions() as session:
            await session.execute(
                text("SET idle_in_transaction_session_timeout = '100ms'")
            )
            # `SET` (não-LOCAL) precisa de um commit para "travar" o valor
            # na sessão -- do contrário fica preso à transação implícita
            # que o próximo `SELECT`/`INSERT` já reabre.
            await session.commit()

            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            # Se a transação tivesse ficado aberta e ociosa durante o
            # `asyncio.sleep` de `manager.generate`, o Postgres já teria
            # derrubado a sessão antes daqui -- este commit é a prova.
            await session.commit()
            return resolved

    resolved = asyncio.run(_run())

    assert resolved is not None
    assert manager.calls == 1

    with integration_database.sessions.begin() as session:
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 1
        assert candidates[0].status == "approved"
        assert candidates[0].category == "monitor"


class _FirstBatchSucceedsSecondFailsAIManager:
    """Primeira chamada de lote sempre sucesso (extração estática
    válida para cada id recebido); a segunda em diante devolve
    conteúdo que NÃO é um array JSON -- reproduz o achado real em PROD
    (2026-09-20, `v1.3.20`): a maioria dos lotes de um `--dry-run` real
    falhou na extração (`product_identity_ai_batch_extraction_failed`).
    Neste cenário só é chamada em modo lote (3 produtos novos, sem
    candidato aprovado ainda -- não há zona cinzenta pra acionar o
    árbitro)."""

    def __init__(self, extraction: str) -> None:
        self._extraction = extraction
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self.calls == 1:
            requested_items = json.loads(request.messages[-1].content)
            single = json.loads(self._extraction)
            content = json.dumps(
                [{"id": item["id"], **single} for item in requested_items]
            )
        else:
            content = "isto não é um array JSON válido"
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=content,
            finished_at=datetime.now(UTC),
        )


def test_dry_run_outcome_report_survives_a_later_batchs_rollback(
    integration_database,
) -> None:
    """Regressão de um achado real em PROD (2026-09-20, `v1.3.20`,
    relatado pelo Claude que executou o deploy): em `--dry-run`
    (`apply=False`, nada commitado de propósito) com múltiplos lotes,
    o `session.rollback()` de um lote SEGUINTE -- disparado
    incondicionalmente ANTES da chamada de IA daquele lote, MESMO que
    essa chamada depois falhe -- desfazia da sessão a resolução de um
    lote ANTERIOR que nunca tinha sido commitada. `resolved_count`
    (contador Python simples) continuava certo, mas reconsultar o
    Product via `session.get` depois do fato (como o script fazia
    antes desta correção) mostrava "sem identidade" pra tudo --
    contradição real entre o resumo e o detalhe, exatamente o que o
    operador encontrou e corretamente travou antes de aplicar.

    `outcome_sink` é a correção: populado no MOMENTO de cada resolução
    bem-sucedida, nunca depende do estado da sessão sobreviver a um
    rollback posterior."""
    mobo_extraction = json.dumps(
        {
            "category": "motherboard",
            "brand": "ASUS",
            "family": "TUF Gaming",
            "model": "B650-Plus",
            "variant": "wifi",
            "store_sku": None,
            "manufacturer_part_number": None,
            "attributes": {"socket": "AM5", "chipset": "B650"},
        }
    )
    mobo_title = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5"
    titles = [f"{mobo_title} - Loja {i}" for i in range(1, 4)]  # 3 títulos

    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        products = [Product(id=uuid4(), name=title) for title in titles]
        session.add_all(products)
        session.flush()
        session.add_all(
            [
                Offer(
                    product_id=product.id,
                    store_id=store.id,
                    external_id=f"dry-run-batch-rollback-{index}",
                    url=f"https://example.invalid/dry-run-batch-rollback-{index}",
                )
                for index, product in enumerate(products)
            ]
        )
        session.flush()
        product_ids = [product.id for product in products]

    manager = _FirstBatchSucceedsSecondFailsAIManager(mobo_extraction)

    async def _reprocess():
        async with integration_database.async_sessions() as session:
            outcome_sink: dict[object, str] = {}
            count = await reprocess_unresolved_products(
                session,
                ai_manager=manager,
                profile=UserRole.ADMIN,
                arbiter_ai_manager=manager,
                limit=50,
                batch_size=2,  # 3 produtos -> lotes de [2, 1]
                apply=False,
                outcome_sink=outcome_sink,
            )
            await session.rollback()  # mesmo fim de `--dry-run` real
            return count, outcome_sink

    resolved_count, outcome_sink = asyncio.run(_reprocess())

    assert manager.calls == 2, "2 lotes esperados (3 produtos, lote de 2)"
    assert resolved_count == 2, (
        "só o primeiro lote (2 produtos) resolve; o segundo falha na IA"
    )
    # A correção real: outcome_sink tem exatamente 2 entradas (as do
    # lote que teve sucesso), SOBREVIVENDO ao rollback do segundo lote
    # -- nunca 0, que era o sintoma real encontrado em PROD.
    assert len(outcome_sink) == 2
    resolved_ids = set(outcome_sink)
    assert resolved_ids.issubset(set(product_ids))
    # Os 2 títulos do primeiro lote compartilham a mesma família/modelo
    # -- um vira RESOLVIDO (promovido), o outro MERGE (Offers migradas).
    messages = list(outcome_sink.values())
    assert any(message.startswith("RESOLVIDO ->") for message in messages)
    assert any("Offers migradas" in message for message in messages)

    # Nada persistido de verdade -- mesma garantia de sempre do dry-run.
    with integration_database.sessions.begin() as session:
        still_unresolved = list(
            session.scalars(select(Product).where(Product.identity_key.is_(None)))
        )
        assert {product.id for product in still_unresolved} == set(product_ids), (
            "dry-run nunca deve persistir nenhuma identidade, mesmo a resolvida"
        )
