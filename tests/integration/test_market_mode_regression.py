"""Regressão real de PROD (v1.2.6): `resolve_product_market_mode` chamava
`_is_high_activity` com a assinatura ANTIGA (sem `scope_id`/`mission_ids`)
depois que TASK-116 tornou esses parâmetros obrigatórios -- `TypeError` em
toda coleta real que chegasse à Fase C com `settings` configurado (achado
em produção numa coleta `kabum`).

Este teste exercita o caminho EXATO da falha real, sem mock:
`_persist_phase_c` (coleta) -> `resolve_realert_window` ->
`resolve_market_mode_hours` -> `resolve_product_market_mode` ->
`_is_high_activity`. Antes da correção, este teste falha com o mesmo
`TypeError` visto em PROD."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.collection.cadence import CadenceConfig
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.collection.orchestration import (
    PriceObservationComparison,
    _PendingOffer,
    _persist_phase_c,
    _PhaseAOutcome,
)
from app.collection.relevance import OfferRelevance
from app.core.config import Settings
from app.missions.models import Mission, MissionCriteria, MissionStatus
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
_SETTINGS = Settings(_env_file=None)


def test_market_mode_survives_high_activity_without_monitoring_item(
    integration_database,
) -> None:
    """Caminho LEGADO (sem `MonitoringItem`, `MissionSource`-based) -- o
    mesmo achado real: `Mission` sem vínculo em `mission_monitoring_items`,
    exatamente o cenário de produção que quebrou."""
    with integration_database.sessions() as session:
        store_id = session.scalar(select(Store.id).where(Store.code == "kabum"))

    with integration_database.sessions.begin() as session:
        user = User(display_name="TASK-116 regressao", role=UserRole.USER)
        product = Product(name="TASK-116 regressao synthetic product")
        session.add_all((user, product))
        session.flush()
        mission = Mission(
            user_id=user.id, title="TASK-116 regressao mission", status=MissionStatus.ACTIVE
        )
        offer = Offer(
            product_id=product.id,
            store_id=store_id,
            external_id=f"regressao-{uuid4().hex}",
            url=f"https://example.invalid/{uuid4().hex}",
        )
        session.add_all((mission, offer))
        session.flush()
        criteria = MissionCriteria(mission_id=mission.id, search_query="synthetic")
        relevance = MissionOfferRelevance(
            mission_id=mission.id,
            offer_id=offer.id,
            classification=OfferRelevance.MATCH,
            classified_at=NOW,
        )
        session.add_all((criteria, relevance))
        session.flush()

        # Três observações CHANGED dentro da janela de HIGH_ACTIVITY
        # (default: 30min/limiar 3) -- a primeira é FIRST_OBSERVATION
        # (excluída), as três seguintes têm uma anterior cada, contam.
        def _run_and_observation(amount: str, observed_at: datetime) -> None:
            run = CollectionRun(
                mission_id=mission.id,
                store_id=store_id,
                status=CollectionRunStatus.SUCCEEDED,
                started_at=observed_at,
                finished_at=observed_at,
            )
            session.add(run)
            session.flush()
            session.add(
                PriceObservation(
                    offer_id=offer.id,
                    collection_run_id=run.id,
                    amount=Decimal(amount),
                    currency="BRL",
                    total_amount=Decimal(amount),
                    availability=Availability.AVAILABLE,
                    observed_at=observed_at,
                )
            )
            session.flush()

        _run_and_observation("100.00", NOW - timedelta(minutes=25))
        _run_and_observation("110.00", NOW - timedelta(minutes=20))
        _run_and_observation("120.00", NOW - timedelta(minutes=15))
        previous_amount = Decimal("130.00")
        _run_and_observation(str(previous_amount), NOW - timedelta(minutes=10))

        previous_observation_id = session.scalar(
            select(PriceObservation.id)
            .where(PriceObservation.offer_id == offer.id)
            .order_by(PriceObservation.observed_at.desc())
            .limit(1)
        )

        current_run = CollectionRun(
            mission_id=mission.id, store_id=store_id,
            status=CollectionRunStatus.RUNNING, started_at=NOW,
        )
        session.add(current_run)
        session.flush()
        current_observation = PriceObservation(
            offer_id=offer.id,
            collection_run_id=current_run.id,
            amount=Decimal("90.00"),
            currency="BRL",
            total_amount=Decimal("90.00"),
            availability=Availability.AVAILABLE,
            observed_at=NOW,
        )
        session.add(current_observation)
        session.flush()

        mission_id = mission.id
        product_id = product.id
        offer_id = offer.id
        run_id = current_run.id
        current_observation_id = current_observation.id

    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=current_observation_id,
        amount=Decimal("90.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="synthetic",
        needs_relevance=False,
        needs_display_name=False,
        observation_created=True,
        alert_comparison=PriceObservationComparison.CHANGED,
        previous_observation_id=previous_observation_id,
        previous_amount=previous_amount,
        previous_currency="BRL",
        previous_availability=Availability.AVAILABLE,
        previous_observed_at=NOW - timedelta(minutes=10),
        forced_relevance=OfferRelevance.MATCH,
    )
    outcome = _PhaseAOutcome(
        run_id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        mission_search_query="synthetic",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(pending,),
    )

    # Antes da correção (v1.2.6): TypeError aqui -- _is_high_activity()
    # missing 2 required keyword-only arguments: 'scope_id' and
    # 'mission_ids' -- exatamente o traceback real de PROD.
    result = asyncio.run(
        _persist_phase_c(
            integration_database.async_sessions, outcome, (), settings=_SETTINGS
        )
    )
    assert result is True

    from app.market_research.service import resolve_realert_window

    async def _window() -> timedelta:
        async with integration_database.async_sessions() as session:
            return await resolve_realert_window(
                session, product_id=product_id, now=NOW, settings=_SETTINGS
            )

    window = asyncio.run(_window())
    # HIGH_ACTIVITY detectado -> janela reduzida (promo), não a normal --
    # prova que o escopo (Mission.id, caminho legado) foi propagado
    # corretamente, não só que a chamada não quebrou.
    assert window == timedelta(hours=_SETTINGS.realert_promo_hours)
    assert window != timedelta(hours=_SETTINGS.realert_normal_hours)


def test_resolve_product_market_mode_scopes_do_not_leak_across_missions(
    integration_database,
) -> None:
    """Duas Missions diferentes, mesma loja, MESMO Product -- cada uma com
    sua própria contagem de mudança (mission_offer_relevance liga cada
    Offer só à sua Mission). Prova que `_resolve_market_mode_scopes` não
    junta escopos que não deveriam se juntar."""
    from app.collection.cadence import resolve_product_market_mode

    with integration_database.sessions() as session:
        store_id = session.scalar(select(Store.id).where(Store.code == "kabum"))

    with integration_database.sessions.begin() as session:
        user = User(display_name="TASK-116 escopos", role=UserRole.USER)
        product = Product(name="TASK-116 escopos synthetic product")
        session.add_all((user, product))
        session.flush()
        mission_quiet = Mission(
            user_id=user.id, title="mission quieta", status=MissionStatus.ACTIVE
        )
        mission_busy = Mission(
            user_id=user.id, title="mission agitada", status=MissionStatus.ACTIVE
        )
        offer_quiet = Offer(
            product_id=product.id, store_id=store_id,
            external_id=f"quiet-{uuid4().hex}", url=f"https://example.invalid/{uuid4().hex}",
        )
        offer_busy = Offer(
            product_id=product.id, store_id=store_id,
            external_id=f"busy-{uuid4().hex}", url=f"https://example.invalid/{uuid4().hex}",
        )
        session.add_all((mission_quiet, mission_busy, offer_quiet, offer_busy))
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission_quiet.id, search_query="quiet"),
                MissionCriteria(mission_id=mission_busy.id, search_query="busy"),
                MissionOfferRelevance(
                    mission_id=mission_quiet.id, offer_id=offer_quiet.id,
                    classification=OfferRelevance.MATCH, classified_at=NOW,
                ),
                MissionOfferRelevance(
                    mission_id=mission_busy.id, offer_id=offer_busy.id,
                    classification=OfferRelevance.MATCH, classified_at=NOW,
                ),
            )
        )
        session.flush()

        def _seed_changes(offer_id, mission_id) -> None:
            for i, amount in enumerate(("10.00", "11.00", "12.00", "13.00")):
                observed_at = NOW - timedelta(minutes=25 - i * 5)
                run = CollectionRun(
                    mission_id=mission_id, store_id=store_id,
                    status=CollectionRunStatus.SUCCEEDED,
                    started_at=observed_at, finished_at=observed_at,
                )
                session.add(run)
                session.flush()
                session.add(
                    PriceObservation(
                        offer_id=offer_id, collection_run_id=run.id,
                        amount=Decimal(amount), currency="BRL", total_amount=Decimal(amount),
                        availability=Availability.AVAILABLE, observed_at=observed_at,
                    )
                )
                session.flush()

        _seed_changes(offer_busy.id, mission_busy.id)
        # offer_quiet: só uma observação (FIRST_OBSERVATION) -- sem mudança real.
        run_quiet = CollectionRun(
            mission_id=mission_quiet.id, store_id=store_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=NOW - timedelta(minutes=10), finished_at=NOW - timedelta(minutes=10),
        )
        session.add(run_quiet)
        session.flush()
        session.add(
            PriceObservation(
                offer_id=offer_quiet.id, collection_run_id=run_quiet.id,
                amount=Decimal("20.00"), currency="BRL", total_amount=Decimal("20.00"),
                availability=Availability.AVAILABLE, observed_at=NOW - timedelta(minutes=10),
            )
        )
        session.flush()

        product_id = product.id

    async def _decide() -> str:
        async with integration_database.async_sessions() as session:
            decision = await resolve_product_market_mode(
                session, product_id=product_id, now=NOW, config=CadenceConfig()
            )
            return decision.mode

    # A Mission agitada tem atividade real (3+ mudanças) -- pior caso vence,
    # então o Product inteiro reporta HIGH_ACTIVITY mesmo a Mission quieta
    # não tendo nenhuma mudança própria.
    assert asyncio.run(_decide()) == "high_activity"
