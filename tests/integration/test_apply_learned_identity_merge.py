"""Regressão de dois crashes reais em PROD (2026-09-20, `v1.3.24`,
`--apply --limit 100`): `apply_learned_identity` só migrava `Offer`
antes de apagar o Product ad-hoc -- `products.id` também é referenciado
com `ON DELETE RESTRICT` por outras 6 tabelas.

1. `mission_product_alert_state` NÃO é protegida pelo invariante "só
existe linha se `identity_key IS NOT NULL`" (diferente de
`market_price_assessments`/`historical_bootstraps`/
`external_price_references`/`mission_product_selections`) -- reproduziu
o crash real (`RestrictViolation` num `DELETE FROM products`). Corrigido
com merge de verdade (nunca sobrescrito às cegas).

2. `purchase_confirmations` também referencia `products.id` com
RESTRICT, mas é IMUTÁVEL por trigger de banco
(`block_purchase_trail_mutation`) -- nem um `UPDATE` de `product_id` é
aceito. Decisão do usuário (2026-09-20): sem ocorrência real hoje (a
Mission encerra e para de coletar assim que a compra é confirmada) --
`apply_learned_identity` agora devolve `None` (nunca crasha) nesse
caso, como proteção preventiva."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.alerts.models import MissionProductAlertState
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability
from app.missions.models import (
    Mission,
    MissionStatus,
    MonitoringItem,  # noqa: F401 -- registra o mapper p/ FK collection_runs.monitoring_item_id
)
from app.offers.models import Offer
from app.products.identity import IDENTITY_VERSION, build_resolved_variant_from_fields
from app.products.identity_learning import apply_learned_identity
from app.products.models import Product
from app.purchase.models import PurchaseConfirmation
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _seed_offer_and_observation(
    session, *, product_id, store_id, external_id, mission_id
):
    offer = Offer(
        id=uuid4(),
        product_id=product_id,
        store_id=store_id,
        external_id=external_id,
        url=f"https://example.invalid/{external_id}",
    )
    session.add(offer)
    session.flush()
    run = CollectionRun(
        id=uuid4(),
        mission_id=mission_id,
        monitoring_item_id=None,
        store_id=store_id,
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
        raw_evidence={"title": "seed"},
    )
    session.add(observation)
    session.flush()
    return offer, observation


def _seed_purchase_confirmation(
    session, *, mission_id, user_id, offer_id, observation_id, product_id, store_id
):
    requested_at = NOW
    confirmation = PurchaseConfirmation(
        id=uuid4(),
        mission_id=mission_id,
        owner_user_id=user_id,
        offer_id=offer_id,
        price_observation_id=observation_id,
        product_id=product_id,
        store_id=store_id,
        seller_id=None,
        position=1,
        url="https://example.invalid/purchase",
        amount=Decimal("899.90"),
        shipping_amount=Decimal("0.00"),
        total_amount=Decimal("899.90"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        fulfillment=None,
        observed_at=NOW,
        evidence_snapshot={"title": "seed"},
        requested_at=requested_at,
        expires_at=requested_at + timedelta(minutes=15),
    )
    session.add(confirmation)
    session.flush()
    return confirmation


def _seed_mission_and_product_pair(session, *, user_id, resolved, canonical_title):
    mission_conflict = Mission(
        user_id=user_id, title="mission-conflict", status=MissionStatus.ACTIVE
    )
    mission_repoint = Mission(
        user_id=user_id, title="mission-repoint", status=MissionStatus.ACTIVE
    )
    session.add_all([mission_conflict, mission_repoint])
    session.flush()

    canonical = Product(
        id=uuid4(),
        name=canonical_title,
        category=resolved.category,
        brand=resolved.brand,
        family=resolved.family,
        model=resolved.model,
        variant=resolved.variant,
        attributes=dict(resolved.attributes),
        family_key=resolved.family_key,
        identity_key=resolved.identity_key,
        identity_version=IDENTITY_VERSION,
    )
    ad_hoc = Product(id=uuid4(), name=f"{canonical_title} ad-hoc")
    session.add_all([canonical, ad_hoc])
    session.flush()
    return mission_conflict, mission_repoint, canonical, ad_hoc


def test_merge_dedupes_conflicting_alert_state_and_repoints_the_rest(
    integration_database,
) -> None:
    """Cenário exato do primeiro crash real: ad-hoc e canônico com
    checkpoint de alerta na MESMA Mission (conflito de PK, precisa
    merge de verdade) + uma Mission onde só o ad-hoc tem checkpoint
    (repoint simples, sem conflito). Sem PurchaseConfirmation aqui --
    esse caso tem teste dedicado abaixo."""
    resolved = build_resolved_variant_from_fields(
        category="cpu",
        brand="amd",
        family="ryzen",
        model="9950x3d",
        variant=None,
        attributes={},
    )
    assert resolved is not None

    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store.id).where(Store.code == "amazon"))
        user = User(display_name="merge-test", role=UserRole.USER)
        session.add(user)
        session.flush()

        mission_conflict, mission_repoint, canonical, ad_hoc = (
            _seed_mission_and_product_pair(
                session,
                user_id=user.id,
                resolved=resolved,
                canonical_title="AMD Ryzen 9950X3D canonical",
            )
        )

        ad_hoc_offer, _ad_hoc_observation = _seed_offer_and_observation(
            session,
            product_id=ad_hoc.id,
            store_id=store,
            external_id="merge-adhoc",
            mission_id=mission_conflict.id,
        )

        # Conflito real: as DUAS Missions já têm checkpoint de alerta
        # pro ad-hoc; `mission_conflict` TAMBÉM já tem um pro canônico
        # -- é exatamente essa colisão de PK que crashava o DELETE.
        session.add_all(
            [
                MissionProductAlertState(
                    mission_id=mission_conflict.id,
                    product_id=ad_hoc.id,
                    best_notified_amount=Decimal("799.00"),  # menor -- deve VENCER
                    best_notified_currency="BRL",
                    last_notified_amount=Decimal("799.00"),
                    last_notified_at=NOW - timedelta(days=1),  # mais antigo -- PERDE
                    rearmed_at=None,
                    last_alert_event_id=None,
                ),
                MissionProductAlertState(
                    mission_id=mission_conflict.id,
                    product_id=canonical.id,
                    best_notified_amount=Decimal("850.00"),  # maior -- deve PERDER
                    best_notified_currency="BRL",
                    last_notified_amount=Decimal("850.00"),
                    last_notified_at=NOW,  # mais recente -- deve VENCER
                    rearmed_at=None,
                    last_alert_event_id=None,
                ),
                MissionProductAlertState(
                    mission_id=mission_repoint.id,
                    product_id=ad_hoc.id,
                    best_notified_amount=Decimal("910.00"),
                    best_notified_currency="BRL",
                    last_notified_amount=Decimal("910.00"),
                    last_notified_at=NOW,
                    rearmed_at=None,
                    last_alert_event_id=None,
                ),
            ]
        )
        session.flush()

        ad_hoc_id, canonical_id = ad_hoc.id, canonical.id
        mission_conflict_id, mission_repoint_id = (
            mission_conflict.id,
            mission_repoint.id,
        )

    async def _apply():
        async with integration_database.async_sessions() as session:
            product = await session.get(Product, ad_hoc_id)
            result = await apply_learned_identity(
                session, product=product, resolved=resolved
            )
            await session.commit()
            return result.id

    result_id = asyncio.run(_apply())
    assert result_id == canonical_id

    with integration_database.sessions.begin() as session:
        # Ad-hoc removido de verdade (o DELETE que crashava antes).
        assert session.get(Product, ad_hoc_id) is None

        # Mission com conflito: mesclada, nunca duplicada nem sobrescrita
        # às cegas -- vence o MENOR best_notified_amount (799, do
        # ad-hoc) e o last_notified_at MAIS RECENTE (do canônico).
        merged = session.get(
            MissionProductAlertState, (mission_conflict_id, canonical_id)
        )
        assert merged is not None
        assert merged.best_notified_amount == Decimal("799.00")
        assert merged.last_notified_amount == Decimal("850.00")
        assert merged.last_notified_at == NOW
        assert (
            session.get(MissionProductAlertState, (mission_conflict_id, ad_hoc_id))
            is None
        )

        # Mission sem conflito: linha simplesmente reapontada pro
        # canônico, valores intactos.
        repointed = session.get(
            MissionProductAlertState, (mission_repoint_id, canonical_id)
        )
        assert repointed is not None
        assert repointed.best_notified_amount == Decimal("910.00")
        assert (
            session.get(MissionProductAlertState, (mission_repoint_id, ad_hoc_id))
            is None
        )

        # Offer também migrada (comportamento já existente, confirmado
        # de novo aqui no mesmo cenário).
        offer_after = session.get(Offer, ad_hoc_offer.id)
        assert offer_after is not None
        assert offer_after.product_id == canonical_id


def test_merge_is_blocked_without_crashing_when_ad_hoc_has_purchase_confirmation(
    integration_database,
) -> None:
    """Cenário exato do segundo crash real: `purchase_confirmations` é
    IMUTÁVEL por trigger de banco -- nem migrar (`UPDATE`) é possível,
    então o merge inteiro precisa ser recusado com segurança (`None`,
    nunca uma exceção não tratada), sem alterar NADA -- nem o `Offer`,
    nem apagar o ad-hoc."""
    resolved = build_resolved_variant_from_fields(
        category="cpu",
        brand="amd",
        family="ryzen",
        model="7950x3d",
        variant=None,
        attributes={},
    )
    assert resolved is not None

    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store.id).where(Store.code == "amazon"))
        user = User(display_name="merge-blocked-test", role=UserRole.USER)
        session.add(user)
        session.flush()

        mission_conflict, _mission_repoint, canonical, ad_hoc = (
            _seed_mission_and_product_pair(
                session,
                user_id=user.id,
                resolved=resolved,
                canonical_title="AMD Ryzen 7950X3D canonical",
            )
        )

        ad_hoc_offer, ad_hoc_observation = _seed_offer_and_observation(
            session,
            product_id=ad_hoc.id,
            store_id=store,
            external_id="merge-blocked-adhoc",
            mission_id=mission_conflict.id,
        )
        purchase = _seed_purchase_confirmation(
            session,
            mission_id=mission_conflict.id,
            user_id=user.id,
            offer_id=ad_hoc_offer.id,
            observation_id=ad_hoc_observation.id,
            product_id=ad_hoc.id,
            store_id=store,
        )
        session.flush()

        ad_hoc_id, canonical_id = ad_hoc.id, canonical.id
        purchase_id = purchase.id
        offer_id = ad_hoc_offer.id

    async def _apply():
        async with integration_database.async_sessions() as session:
            product = await session.get(Product, ad_hoc_id)
            result = await apply_learned_identity(
                session, product=product, resolved=resolved
            )
            await session.commit()
            return result

    result = asyncio.run(_apply())
    assert result is None, (
        "merge com PurchaseConfirmation imutável deve recusar (None), nunca crashar"
    )

    with integration_database.sessions.begin() as session:
        # Nada mudou: ad-hoc continua existindo, sem identidade.
        ad_hoc_after = session.get(Product, ad_hoc_id)
        assert ad_hoc_after is not None
        assert ad_hoc_after.identity_key is None

        # Canônico intocado.
        assert session.get(Product, canonical_id) is not None

        # Offer NUNCA migrada -- o merge inteiro foi recusado antes de
        # tocar em qualquer coisa.
        offer_after = session.get(Offer, offer_id)
        assert offer_after is not None
        assert offer_after.product_id == ad_hoc_id

        # PurchaseConfirmation intocada (imutável, como sempre foi).
        purchase_after = session.get(PurchaseConfirmation, purchase_id)
        assert purchase_after is not None
        assert purchase_after.product_id == ad_hoc_id
