"""TASK-114: idempotência real do script de reparo histórico, contra
PostgreSQL descartável -- nunca PROD."""

import asyncio
import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability
from app.offers.models import Offer
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.stores.models import Store
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

_REPAIR_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "repair_cpu_identity_misclassification.py"
)


def _load_repair_module():
    spec = importlib.util.spec_from_file_location("repair_cpu_identity_misclassification", _REPAIR_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_WRONG_RAW_TITLE = (
    "MSI Placa-mãe MAG X870E Tomahawk MAX WiFi, ATX - Suporta processadores "
    "AMD Ryzen 9000/8000/7000, AM5-80A SPS VRM"
)


def _run(coro):
    return asyncio.run(coro)


def _seed_misclassified_offer(integration_database) -> tuple:
    """Reproduz o incidente real: Product category=cpu resolvido de um
    título que era, na verdade, de uma placa-mãe -- ANTES do guard atual
    existir. `resolve_product_variant` aqui é usado só para computar uma
    identity_key/family_key plausível da MESMA forma que o bug antigo
    teria feito (com um texto puramente "AMD Ryzen 9000", sem cláusula de
    compatibilidade) -- não é o texto real da Offer, que é o campo que o
    reparo de fato lê."""
    wrong_identity = resolve_product_variant("AMD Ryzen 9000")
    assert wrong_identity is not None  # pré-condição do teste
    from app.missions.models import Mission, MissionStatus
    from app.users.models import User, UserRole

    with integration_database.sessions.begin() as session:
        amazon_id = session.scalar(select(Store.id).where(Store.code == "amazon"))
        user = User(display_name="repair-test", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(user_id=user.id, title="repair-test", status=MissionStatus.ACTIVE)
        session.add(mission)
        session.flush()
        product = Product(
            id=uuid4(),
            name="Amd Ryzen 9000",
            display_name="Amd Ryzen 9000",
            category=wrong_identity.category,
            brand=wrong_identity.brand,
            family=wrong_identity.family,
            model=wrong_identity.model,
            variant=wrong_identity.variant,
            attributes={},
            family_key=wrong_identity.family_key,
            identity_key=wrong_identity.identity_key,
            identity_version=IDENTITY_VERSION,
        )
        session.add(product)
        session.flush()
        offer = Offer(
            id=uuid4(),
            product_id=product.id,
            store_id=amazon_id,
            url="https://example.invalid/repair-test-offer",
            image_url=None,
        )
        run = CollectionRun(
            id=uuid4(),
            mission_id=mission.id,
            monitoring_item_id=None,
            store_id=amazon_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=NOW,
        )
        session.add_all((offer, run))
        session.flush()
        session.add(
            PriceObservation(
                id=uuid4(),
                offer_id=offer.id,
                collection_run_id=run.id,
                amount=Decimal("1999.90"),
                currency="BRL",
                total_amount=Decimal("1999.90"),
                availability=Availability.AVAILABLE,
                observed_at=NOW,
                raw_evidence={"title": _WRONG_RAW_TITLE},
            )
        )
        return offer.id, product.id


def test_repair_apply_is_idempotent(integration_database) -> None:
    repair_run = _load_repair_module().run

    offer_id, wrong_product_id = _seed_misclassified_offer(integration_database)

    _run(repair_run(apply=True))

    with integration_database.sessions() as session:
        offer = session.get(Offer, offer_id)
        assert offer.product_id != wrong_product_id
        product_after_first_apply = offer.product_id

    # Segunda aplicação: nada deve mudar.
    _run(repair_run(apply=True))

    with integration_database.sessions() as session:
        offer = session.get(Offer, offer_id)
        assert offer.product_id == product_after_first_apply
