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
    `self.calls`."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self._content,
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


def _seed_unresolved_motherboard(integration_database) -> tuple:
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
        product = Product(id=uuid4(), name=_MOBO_TITLE)
        session.add(product)
        session.flush()
        offer = Offer(
            id=uuid4(),
            product_id=product.id,
            store_id=store,
            external_id="reprocess-script-test",
            url="https://example.invalid/reprocess-script-test",
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
            raw_evidence={"title": _MOBO_TITLE},
        )
        session.add(observation)
        session.flush()
        return product.id, offer.id, observation.id


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
