"""TASK-128 (etapa 1) -- produto nunca fica sem vínculo, Postgres real
com a migration `20260926_0001`. Decisão do usuário (2026-09-25/26):
"ela não pode não resolver, ela sempre vai ter que criar um vínculo de
alguma coisa". Prova, com banco de verdade: extração incompleta vira
vínculo PARCIAL (categoria + marca/família só quando aparecem no
título, nunca `identity_key`); título sem nem categoria fica
`awaiting_page`; os dois ficam no cache e o MESMO título nunca paga IA
de novo; falha passageira da IA nunca vai para o cache; o backlog do
backfill passa a ser só "produto sem vínculo nenhum"; e as CHECKs da
migration recusam as formas inválidas."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.products.identity_ai import (
    BATCH_EXTRACT_IDENTITY_PURPOSE,
    PartialProductLink,
)
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import (
    reprocess_unresolved_products,
    resolve_or_learn_product_variant,
    unlinked_product_criteria,
)
from app.products.models import Product
from app.users.models import UserRole
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def _extraction(
    *,
    category: str = "",
    brand: str = "",
    family: str = "",
    model: str = "",
) -> dict:
    return {
        "category": category,
        "brand": brand,
        "family": family,
        "model": model,
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": None,
        "attributes": {},
    }


class _ByTitleAIManager:
    """Fronteira de IA controlada: uma extração por título, no modo item
    único (título cru na última mensagem) e no modo lote (array JSON
    depois da frase de instrução). `self.calls` conta CHAMADAS de IA;
    título ausente do mapa = falha passageira (exceção do provedor)."""

    def __init__(self, extraction_by_title: dict[str, dict]) -> None:
        self._by_title = extraction_by_title
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        user_content = request.messages[-1].content
        if request.purpose == BATCH_EXTRACT_IDENTITY_PURPOSE:
            items = json.loads(user_content[user_content.index("[") :])
            content = json.dumps(
                [{"id": item["id"], **self._by_title[item["title"]]} for item in items]
            )
        else:
            if user_content not in self._by_title:
                raise RuntimeError("provedor indisponível")
            content = json.dumps(self._by_title[user_content])
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=content,
            finished_at=datetime.now(UTC),
        )


def _resolve(integration_database, manager, title):
    async def _run():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=manager, profile=UserRole.ADMIN
            )
            await session.commit()
            return resolved

    return asyncio.run(_run())


def _candidates(integration_database) -> list[ProductIdentityCandidate]:
    with integration_database.sessions() as session:
        return list(session.scalars(select(ProductIdentityCandidate)))


_CHAIR = "Cadeira Gamer Reclinável Preta com Almofada"
_MOUSE = "Mouse Gamer RGB 7200 DPI 6 Botões"
_HEADSET = "Headset Gamer HyperX Cloud Stinger P2"
_UNKNOWN = "Kit Promo Especial 3 em 1 Oferta Relâmpago"


def test_generic_title_gets_partial_link_and_never_calls_ai_again(
    integration_database,
) -> None:
    """Caso típico do usuário ("cadeira gamer"): a IA só fecha a
    categoria -- vira vínculo parcial no cache, e a segunda coleta do
    MESMO título (sessão nova, só o Postgres persiste) nunca paga IA."""
    manager = _ByTitleAIManager({_CHAIR: _extraction(category="Cadeira Gamer")})

    first = _resolve(integration_database, manager, _CHAIR)
    second = _resolve(integration_database, manager, _CHAIR)

    expected = PartialProductLink(category="cadeira-gamer", brand=None, family=None)
    assert first == expected
    assert second == expected
    assert manager.calls == 1, "o mesmo título nunca chama a IA de novo"
    [candidate] = _candidates(integration_database)
    assert candidate.status == "partial"
    assert candidate.category == "cadeira-gamer"
    assert candidate.identity_key is None
    assert candidate.family_key is None
    assert candidate.model is None


def test_partial_link_drops_brand_the_title_never_mentions(
    integration_database,
) -> None:
    """Anti-invenção continua valendo no parcial: marca que não aparece
    no título nunca entra no vínculo (nem no cache)."""
    manager = _ByTitleAIManager(
        {_MOUSE: _extraction(category="mouse", brand="Logitech")}
    )

    resolved = _resolve(integration_database, manager, _MOUSE)

    assert resolved == PartialProductLink(category="mouse", brand=None, family=None)
    [candidate] = _candidates(integration_database)
    assert candidate.brand is None


def test_unrecognized_title_is_cached_as_awaiting_page(integration_database) -> None:
    """Nem a categoria saiu do título: o GG registra que não entendeu
    (`awaiting_page`, a etapa 2 lê a página) -- sem vínculo ainda, mas
    também sem pagar IA a cada ciclo."""
    manager = _ByTitleAIManager({_UNKNOWN: _extraction()})

    first = _resolve(integration_database, manager, _UNKNOWN)
    second = _resolve(integration_database, manager, _UNKNOWN)

    assert first is None
    assert second is None
    assert manager.calls == 1
    [candidate] = _candidates(integration_database)
    assert candidate.status == "awaiting_page"
    assert candidate.category is None
    assert candidate.identity_key is None


def test_transient_ai_failure_is_never_cached(integration_database) -> None:
    """`None` da extração = falha de rede/provedor -- nunca vira cache,
    a próxima coleta tenta de novo."""
    manager = _ByTitleAIManager({})

    assert _resolve(integration_database, manager, _CHAIR) is None
    assert _resolve(integration_database, manager, _CHAIR) is None

    assert manager.calls == 2
    assert _candidates(integration_database) == []


def test_grounding_failure_goes_to_review_but_product_still_gets_partial_link(
    integration_database,
) -> None:
    """Extração completa com modelo que NÃO está no título: a proposta
    exata vai para revisão (`pending_review`, como sempre), mas o produto
    já sai com o vínculo parcial da parte aterrada -- e o cache devolve
    o mesmo vínculo sem nova chamada."""
    manager = _ByTitleAIManager(
        {
            _HEADSET: _extraction(
                category="headset", brand="HyperX", family="Cloud", model="Alpha"
            )
        }
    )

    first = _resolve(integration_database, manager, _HEADSET)
    second = _resolve(integration_database, manager, _HEADSET)

    expected = PartialProductLink(category="headset", brand="hyperx", family="cloud")
    assert first == expected
    assert second == expected
    assert manager.calls == 1
    [candidate] = _candidates(integration_database)
    assert candidate.status == "pending_review"
    assert candidate.identity_key is not None, (
        "a proposta exata continua inteira na fila de revisão"
    )


def _seed_ad_hoc_products(integration_database, *titles: str) -> list:
    with integration_database.sessions.begin() as session:
        products = [Product(id=uuid4(), name=title) for title in titles]
        session.add_all(products)
        session.flush()
        return [product.id for product in products]


def _unlinked_count(integration_database) -> int:
    with integration_database.sessions() as session:
        return session.scalar(
            select(func.count()).select_from(Product).where(unlinked_product_criteria())
        )


def test_reprocess_links_generic_product_and_drops_it_from_backlog(
    integration_database,
) -> None:
    """Backfill item a item (`batch_size=1`): o produto genérico ganha o
    vínculo parcial (categoria, nunca `identity_key`) e sai do backlog --
    a rodada seguinte não tem mais nada para pagar."""
    [product_id] = _seed_ad_hoc_products(integration_database, _CHAIR)
    manager = _ByTitleAIManager({_CHAIR: _extraction(category="cadeira gamer")})

    async def _reprocess():
        async with integration_database.async_sessions() as session:
            sink: dict[object, str] = {}
            count = await reprocess_unresolved_products(
                session,
                ai_manager=manager,
                profile=UserRole.ADMIN,
                apply=True,
                outcome_sink=sink,
            )
            await session.commit()
            return count, sink

    count, sink = asyncio.run(_reprocess())

    assert count == 1
    assert sink[product_id].startswith("VINCULO PARCIAL -> category=cadeira-gamer")
    with integration_database.sessions() as session:
        product = session.get(Product, product_id)
        assert product.category == "cadeira-gamer"
        assert product.identity_key is None
        assert product.family_key is None
    assert _unlinked_count(integration_database) == 0

    second_count, _ = asyncio.run(_reprocess())
    assert second_count == 0
    assert manager.calls == 1


def test_reprocess_batch_persists_partial_and_awaiting_page(
    integration_database,
) -> None:
    """Backfill em lote (`batch_size=2`, caminho da TASK-123): um título
    vira parcial, o outro `awaiting_page` -- os dois ficam no cache
    (commitados com `apply=True`), só o parcial conta como resolvido. O
    `awaiting_page` continua no backlog (ainda sem vínculo), mas a rodada
    seguinte o resolve pelo cache, sem nova chamada de IA."""
    chair_id, unknown_id = _seed_ad_hoc_products(integration_database, _CHAIR, _UNKNOWN)
    manager = _ByTitleAIManager(
        {
            _CHAIR: _extraction(category="cadeira gamer"),
            _UNKNOWN: _extraction(),
        }
    )

    async def _reprocess():
        async with integration_database.async_sessions() as session:
            count = await reprocess_unresolved_products(
                session,
                ai_manager=manager,
                profile=UserRole.ADMIN,
                batch_size=2,
                apply=True,
            )
            await session.commit()
            return count

    assert asyncio.run(_reprocess()) == 1
    assert manager.calls == 1, "um lote, uma chamada"
    statuses = {c.raw_title: c.status for c in _candidates(integration_database)}
    assert statuses == {_CHAIR: "partial", _UNKNOWN: "awaiting_page"}
    with integration_database.sessions() as session:
        assert session.get(Product, chair_id).category == "cadeira-gamer"
        assert session.get(Product, unknown_id).category is None
    # O "não entendido" sai do backlog do script: quem o resolve é a
    # varredura de página do worker (etapa 2), não uma nova rodada.
    assert _unlinked_count(integration_database) == 0

    assert asyncio.run(_reprocess()) == 0
    assert manager.calls == 1, "awaiting_page nunca paga IA de novo"


def test_reprocess_one_by_one_never_loses_awaiting_page_to_the_next_rollback(
    integration_database,
) -> None:
    """Regressão (achada na própria etapa 1): no caminho item a item
    (`batch_size=1`), `resolve_or_learn_product_variant` faz `rollback`
    antes da chamada de IA de CADA produto -- sem commit, o
    `awaiting_page` do produto anterior sumia e o título pagava IA de
    novo em toda rodada. A sessão termina com `rollback` de propósito:
    só sobrevive o que o próprio backfill commitou (`apply=True`)."""
    _seed_ad_hoc_products(integration_database, _UNKNOWN, _CHAIR)
    manager = _ByTitleAIManager(
        {
            _CHAIR: _extraction(category="cadeira gamer"),
            _UNKNOWN: _extraction(),
        }
    )

    async def _reprocess():
        async with integration_database.async_sessions() as session:
            count = await reprocess_unresolved_products(
                session, ai_manager=manager, profile=UserRole.ADMIN, apply=True
            )
            await session.rollback()
            return count

    assert asyncio.run(_reprocess()) == 1
    assert manager.calls == 2
    statuses = {c.raw_title: c.status for c in _candidates(integration_database)}
    assert statuses == {_CHAIR: "partial", _UNKNOWN: "awaiting_page"}

    asyncio.run(_reprocess())
    assert manager.calls == 2, "os dois títulos saem do cache na segunda rodada"


@pytest.mark.parametrize(
    ("status", "columns"),
    [
        # parcial nunca carrega identidade exata (fusão por identity_key)
        (
            "partial",
            {"category": "'cpu'", "identity_key": "'v1:cpu:amd'"},
        ),
        # parcial sem categoria não é vínculo nenhum
        ("partial", {"brand": "'AMD'"}),
        # "aguardando página" é justamente não ter categoria ainda
        ("awaiting_page", {"category": "'cpu'"}),
        # os estados antigos continuam exigindo a identidade completa
        ("approved", {"category": "'cpu'"}),
    ],
)
def test_database_rejects_invalid_candidate_shapes(
    integration_database, status, columns
) -> None:
    names = ", ".join(columns)
    values = ", ".join(columns.values())
    with (
        pytest.raises(IntegrityError),
        integration_database.sessions.begin() as session,
    ):
        session.execute(
            text(
                "INSERT INTO product_identity_candidates "
                f"(id, raw_title, normalized_title_hash, status, grounded, "
                f"created_at, {names}) "
                f"VALUES (:id, 'x', :hash, :status, false, now(), {values})"
            ),
            {"id": uuid4(), "hash": uuid4().hex, "status": status},
        )


def test_backfill_never_gets_stuck_on_titles_handed_to_page_read(
    integration_database,
) -> None:
    """Regressão (achada ao documentar o deploy): durante o backfill o
    worker fica PARADO (sequência obrigatória), então os "não entendidos"
    não saem do banco -- sem excluí-los do backlog, com `limit` pequeno
    toda rodada pegava os MESMOS títulos (cache, sem IA) e o resto do
    backlog nunca era processado."""
    unknown_id, chair_id = _seed_ad_hoc_products(integration_database, _UNKNOWN, _CHAIR)
    manager = _ByTitleAIManager(
        {
            _CHAIR: _extraction(category="cadeira gamer"),
            _UNKNOWN: _extraction(),
        }
    )

    async def _one_round():
        async with integration_database.async_sessions() as session:
            count = await reprocess_unresolved_products(
                session, ai_manager=manager, profile=UserRole.ADMIN, limit=1, apply=True
            )
            await session.commit()
            return count

    asyncio.run(_one_round())
    asyncio.run(_one_round())

    assert manager.calls == 2, "cada título pagou IA uma única vez"
    with integration_database.sessions() as session:
        assert session.get(Product, chair_id).category == "cadeira-gamer"
        assert session.get(Product, unknown_id).category is None
    statuses = {c.raw_title: c.status for c in _candidates(integration_database)}
    assert statuses == {_CHAIR: "partial", _UNKNOWN: "awaiting_page"}
    assert _unlinked_count(integration_database) == 0
