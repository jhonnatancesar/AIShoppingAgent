"""TASK-123: script de backfill de identidade via IA, contra PostgreSQL
descartável -- nunca PROD.

`reprocess_unresolved_products` (o mecanismo em si) já é provado por
`test_product_identity_learning.py::test_reprocess_unresolved_offer_after_definition_is_learned`
-- este arquivo prova o SCRIPT que faltava pra ligar esse mecanismo ao
banco real: `--dry-run` não persiste nada (mas ainda chama a IA pra
preview real, não simulado), `--apply` resolve e nunca perde
Offer/PriceObservation já coletada, e idempotência (rodar duas vezes
não reprocessa o que já foi resolvido)."""

import asyncio
import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability
from app.missions.models import (
    Mission,
    MissionStatus,
    MonitoringItem,  # noqa: F401 -- registra o mapper p/ FK collection_runs.monitoring_item_id
)
from app.offers.models import Offer
from app.products.identity_ai import BATCH_EXTRACT_IDENTITY_PURPOSE
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "reprocess_unresolved_product_identity.py"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "reprocess_unresolved_product_identity", _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StaticIdentityAIManager:
    """Mesmo espírito de `_StaticIdentityAIManager` de
    `test_product_identity_learning.py` -- fronteira de IA controlada,
    devolve sempre a MESMA extração estruturada, contável em
    `self.calls`.

    Também responde ao modo em LOTE (`BATCH_EXTRACT_IDENTITY_PURPOSE`,
    TASK-123 2026-09-20): devolve um array JSON com a MESMA extração
    estática para cada "id" que o pedido enviou -- `self.calls` continua
    contando CHAMADAS de IA (uma por lote), não produtos, provando que o
    lote realmente reduz o número de chamadas."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if request.purpose == BATCH_EXTRACT_IDENTITY_PURPOSE:
            # A mensagem do usuário tem uma frase em linguagem natural
            # ANTES do array JSON (achado real de PROD, 2026-09-20,
            # `v1.3.22` -- ver `extract_product_identities_via_ai_batch`)
            # -- o array sempre começa no primeiro `[`.
            user_content = request.messages[-1].content
            requested_items = json.loads(user_content[user_content.index("[") :])
            single = json.loads(self._content)
            content = json.dumps(
                [{"id": item["id"], **single} for item in requested_items]
            )
        else:
            content = self._content
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=content,
            finished_at=datetime.now(UTC),
        )


_MOBO_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": "wifi", "store_sku": null, '
    '"manufacturer_part_number": null, '
    '"attributes": {"socket": "AM5", "chipset": "B650"}}'
)

_MOBO_TITLE = "Placa-mãe Asus TUF Gaming B650-Plus WiFi DDR5 AM5"


def _run(coro):
    return asyncio.run(coro)


def _seed_unresolved_product(integration_database, title: str) -> tuple:
    """Product ad-hoc sem identity_key (mesmo estado real encontrado em
    PROD -- placa-mãe sem extrator determinístico), com uma Offer e uma
    PriceObservation real já coletada -- prova depois que nenhuma das
    duas se perde."""
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store.id).where(Store.code == "amazon"))
        user = User(display_name="reprocess-test", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id, title="reprocess-test", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        product = Product(id=uuid4(), name=title)
        session.add(product)
        session.flush()
        offer = Offer(
            id=uuid4(),
            product_id=product.id,
            store_id=store,
            external_id=f"reprocess-script-test-{uuid4()}",
            url=f"https://example.invalid/reprocess-script-test-{uuid4()}",
        )
        session.add(offer)
        session.flush()
        run = CollectionRun(
            id=uuid4(),
            mission_id=mission.id,
            monitoring_item_id=None,
            store_id=store,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=NOW,
        )
        session.add(run)
        session.flush()
        observation = PriceObservation(
            id=uuid4(),
            offer_id=offer.id,
            collection_run_id=run.id,
            amount=Decimal("899.90"),
            currency="BRL",
            total_amount=Decimal("899.90"),
            availability=Availability.AVAILABLE,
            observed_at=NOW,
            raw_evidence={"title": title},
        )
        session.add(observation)
        session.flush()
        return product.id, offer.id, observation.id


def _seed_unresolved_motherboard(integration_database) -> tuple:
    return _seed_unresolved_product(integration_database, _MOBO_TITLE)


def test_dry_run_calls_ai_but_persists_nothing(integration_database) -> None:
    module = _load_script_module()
    product_id, _offer_id, _observation_id = _seed_unresolved_motherboard(
        integration_database
    )
    manager = _StaticIdentityAIManager(_MOBO_EXTRACTION)

    _run(
        module.run(
            apply=False,
            limit=50,
            ai_manager=manager,
            arbiter_ai_manager=manager,
        )
    )

    # A IA FOI chamada de verdade (preview real, nunca simulado) --
    # só a persistência é que não aconteceu.
    assert manager.calls == 1

    with integration_database.sessions() as session:
        product = session.get(Product, product_id)
        assert product is not None
        assert product.identity_key is None, (
            "dry-run nunca deve persistir a identidade aprendida"
        )


def test_count_only_reports_total_without_any_ai_call(integration_database) -> None:
    """`--count-only` (achado real 2026-09-20, pergunta do usuário sobre
    custo de IA): reporta o total REAL do backlog, sem `limit`, e
    NUNCA constrói/chama IA -- nem `ai_manager` nem `arbiter_ai_manager`
    são tocados. Seguro rodar a qualquer momento para saber o tamanho
    real antes de decidir o ritmo de `--dry-run`/`--apply`."""
    module = _load_script_module()
    _seed_unresolved_motherboard(integration_database)
    manager = _StaticIdentityAIManager(_MOBO_EXTRACTION)

    _run(
        module.run(
            apply=False,
            limit=50,
            count_only=True,
            ai_manager=manager,
            arbiter_ai_manager=manager,
        )
    )

    assert manager.calls == 0, "--count-only nunca deve chamar IA"

    with integration_database.sessions() as session:
        products = list(
            session.scalars(select(Product).where(Product.identity_key.is_(None)))
        )
        assert len(products) == 1, (
            "--count-only nunca deve alterar o backlog, só contá-lo"
        )


def test_apply_resolves_identity_without_losing_offer_or_observation(
    integration_database,
) -> None:
    module = _load_script_module()
    product_id, offer_id, observation_id = _seed_unresolved_motherboard(
        integration_database
    )
    manager = _StaticIdentityAIManager(_MOBO_EXTRACTION)

    _run(
        module.run(
            apply=True,
            limit=50,
            ai_manager=manager,
            arbiter_ai_manager=manager,
        )
    )

    assert manager.calls == 1

    with integration_database.sessions() as session:
        offer = session.get(Offer, offer_id)
        assert offer is not None, "Offer nunca pode ser perdida"
        observation = session.get(PriceObservation, observation_id)
        assert observation is not None, "PriceObservation nunca pode ser perdida"
        assert observation.offer_id == offer.id

        resolved_product = session.get(Product, offer.product_id)
        assert resolved_product is not None
        assert resolved_product.identity_key is not None
        assert resolved_product.category == "motherboard"
        assert resolved_product.brand == "asus"


def test_apply_twice_is_idempotent(integration_database) -> None:
    module = _load_script_module()
    _seed_unresolved_motherboard(integration_database)
    manager = _StaticIdentityAIManager(_MOBO_EXTRACTION)

    _run(
        module.run(apply=True, limit=50, ai_manager=manager, arbiter_ai_manager=manager)
    )
    assert manager.calls == 1

    # Segunda rodada: o Product já tem identity_key, então nem entra
    # mais na lista de candidatos -- zero chamadas novas de IA.
    _run(
        module.run(apply=True, limit=50, ai_manager=manager, arbiter_ai_manager=manager)
    )
    assert manager.calls == 1, (
        "segunda rodada não deve reprocessar o que já foi resolvido"
    )

    with integration_database.sessions.begin() as session:
        same_identity_products = list(
            session.scalars(select(Product).where(Product.category == "motherboard"))
        )
        assert len(same_identity_products) == 1, (
            "nunca deve haver duas linhas pra mesma identidade"
        )


def test_batch_of_thirteen_products_uses_two_ai_calls_not_thirteen(
    integration_database,
) -> None:
    """Prova o pedido real do usuário (2026-09-20, ajustado no mesmo dia
    depois que o usuário revelou que o backlog real tem 371 Products,
    não a dúzia suposta ao escolher os números originais): lote de
    `_BATCH_SIZE` (10) produtos por chamada de IA -- 13 Products
    DIFERENTES (nenhum reaproveitável entre si antes da extração) devem
    gastar só 2 chamadas de IA (10 + 3), nunca 13. Também é a regressão
    do bug real encontrado nesta mesma rodada: `session.rollback()`
    entre lotes expirava produtos já carregados e/ou descartava
    trabalho já aplicado de um lote anterior -- aqui os 13 sobrevivem,
    todos com a MESMA identidade (mesmos campos na extração estática),
    o que força o caminho de MERGE de `apply_learned_identity` dentro
    do próprio lote e entre lotes (só o primeiro promovido vira
    canônico; os outros 12 migram as Offers pra ele e são removidos)."""
    module = _load_script_module()
    titles = [_MOBO_TITLE] + [f"{_MOBO_TITLE} - Loja {i}" for i in range(1, 13)]
    seeded = [_seed_unresolved_product(integration_database, title) for title in titles]
    product_ids = [product_id for product_id, _offer_id, _observation_id in seeded]
    offer_ids = [offer_id for _product_id, offer_id, _observation_id in seeded]
    manager = _StaticIdentityAIManager(_MOBO_EXTRACTION)

    _run(
        module.run(
            apply=True, limit=100, ai_manager=manager, arbiter_ai_manager=manager
        )
    )

    assert manager.calls == 2, (
        "13 produtos em lotes de 10 devem gastar 2 chamadas de IA (10 + 3), não 13"
    )

    with integration_database.sessions() as session:
        canonical_ids = set()
        for product_id in product_ids:
            product = session.get(Product, product_id)
            if product is not None:
                assert product.identity_key is not None, (
                    f"{product_id}: deveria ter sido resolvido nesta rodada"
                )
                canonical_ids.add(product.id)
        # Todos os 5 títulos resolvem pra MESMA identidade -- só 1
        # Product canônico deve sobrar; os outros migraram Offers e
        # foram removidos (nenhum perdido, nenhum duplicado).
        assert len(canonical_ids) == 1

        for offer_id in offer_ids:
            offer = session.get(Offer, offer_id)
            assert offer is not None, "nenhuma Offer pode ser perdida no merge"
            assert offer.product_id in canonical_ids
