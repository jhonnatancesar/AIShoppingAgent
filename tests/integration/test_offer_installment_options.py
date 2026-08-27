"""TASK-089 (DEC-069): integração real de OfferInstallmentOption contra
PostgreSQL 18 descartável -- prova FK, UNIQUE, CHECK constraints,
rollback transacional e a semântica append-only (observação N+1 nunca
altera as opções da observação N), tudo contra o banco de verdade, não
mocks. Roda só via `python scripts/run_integration_tests.py`."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    InstallmentInterestKind,
    RawCollectedOffer,
    RawInstallmentOption,
)
from app.collection.models import OfferInstallmentOption, PriceObservation
from app.collection.orchestration import CollectionOrchestrator
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


class _AlwaysMatchAIManager:
    async def generate(self, request):
        content = (
            '{"relevance": "match"}'
            if request.purpose == "classify_offer_relevance"
            else '{"display_title": "Synthetic GPU"}'
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=datetime.now(UTC),
        )


class _InstallmentProvider:
    """Devolve uma oferta com N opções de parcelamento reais -- o mesmo
    formato que `PichauProvider`/`TerabyteProvider` produziriam."""

    source_code = "pichau"

    def __init__(self, options: tuple[RawInstallmentOption, ...]) -> None:
        self._options = options

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/task089-stable-offer",
                    title="TASK-089 synthetic GPU",
                    collected_at=completed,
                    external_id="task089-stable-offer",
                    raw_price="R$ 1.900,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                    installment_options=self._options,
                ),
            ),
        )


def _seed_due_mission(sessions, now: datetime):
    with sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        user = User(display_name="TASK-089 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-089 orchestration",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission.id,
                    search_query="synthetic GPU",
                ),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission.id, store_id=store.id),
            )
        )
        return mission.id


def _run_batch_with_options(
    integration_database, now: datetime, options: tuple[RawInstallmentOption, ...]
):
    _seed_due_mission(integration_database.sessions, now)
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_InstallmentProvider(options),)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    import asyncio

    result = asyncio.run(orchestrator.run_batch(now=now))
    assert result.succeeded == 1
    with integration_database.sessions() as session:
        observation = session.scalar(select(PriceObservation))
        assert observation is not None
        return observation.id


# --- 1-3: criação de PriceObservation + múltiplas opções + leitura ---


def test_persists_and_reads_back_multiple_installment_options(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    options = (
        RawInstallmentOption(
            installment_count=1,
            raw_amount="R$ 1.615,00",
            discount_percent=Decimal("15"),
        ),
        RawInstallmentOption(
            installment_count=12,
            raw_amount="R$ 158,33",
            raw_total_amount="R$ 1.900,00",
            interest_kind=InstallmentInterestKind.INTEREST_FREE,
            is_highlighted=True,
        ),
    )

    observation_id = _run_batch_with_options(integration_database, now, options)

    with integration_database.sessions() as session:
        rows = list(
            session.scalars(
                select(OfferInstallmentOption)
                .where(OfferInstallmentOption.price_observation_id == observation_id)
                .order_by(OfferInstallmentOption.installment_count)
            )
        )
        assert [row.installment_count for row in rows] == [1, 12]
        assert rows[0].discount_percent == Decimal("15.00")
        assert rows[0].installment_total_amount is None
        assert rows[0].is_highlighted is False
        assert rows[1].installment_total_amount == Decimal("1900.0000")
        assert rows[1].interest_kind is InstallmentInterestKind.INTEREST_FREE
        assert rows[1].is_highlighted is True


def test_historical_multi_option_observation_from_before_dec070_stays_valid(
    integration_database,
) -> None:
    """DEC-070 (2026-08-20) removeu a navegação individual da Terabyte,
    mas não mudou schema nem apagou histórico -- uma observação antiga
    com a faixa completa 1x-18x (como a Terabyte gerava antes, nenhuma
    opção `is_highlighted`) continua persistindo e sendo lida normalmente
    pelo modelo atual."""
    now = datetime.now(UTC).replace(microsecond=0)
    legacy_options = tuple(
        RawInstallmentOption(
            installment_count=count,
            raw_amount=f"R$ {1000 // count},00",
            interest_kind=InstallmentInterestKind.INTEREST_FREE
            if count <= 12
            else InstallmentInterestKind.WITH_INTEREST,
        )
        for count in (1, 2, 3, 4, 6, 12, 13, 18)
    )

    observation_id = _run_batch_with_options(integration_database, now, legacy_options)

    with integration_database.sessions() as session:
        rows = list(
            session.scalars(
                select(OfferInstallmentOption)
                .where(OfferInstallmentOption.price_observation_id == observation_id)
                .order_by(OfferInstallmentOption.installment_count)
            )
        )
        assert [row.installment_count for row in rows] == [1, 2, 3, 4, 6, 12, 13, 18]
        assert all(row.is_highlighted is False for row in rows)
        assert {row.interest_kind for row in rows} == {
            InstallmentInterestKind.INTEREST_FREE,
            InstallmentInterestKind.WITH_INTEREST,
        }


# --- 4: FK ---


def test_foreign_key_rejects_orphan_price_observation_id(
    integration_database,
) -> None:
    with integration_database.sessions() as session:
        session.add(
            OfferInstallmentOption(
                price_observation_id=uuid4(),  # nunca existiu
                installment_count=1,
                installment_amount=Decimal("10.00"),
            )
        )
        with pytest.raises(IntegrityError, match="(?i)foreign key"):
            session.commit()


# --- 5: UNIQUE ---


def test_unique_constraint_rejects_duplicate_count_in_same_observation(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    observation_id = _run_batch_with_options(
        integration_database,
        now,
        (RawInstallmentOption(installment_count=12, raw_amount="R$ 158,33"),),
    )

    with integration_database.sessions() as session:
        session.add(
            OfferInstallmentOption(
                price_observation_id=observation_id,
                installment_count=12,  # já existe para esta observação
                installment_amount=Decimal("999.00"),
            )
        )
        with pytest.raises(IntegrityError, match="(?i)unique"):
            session.commit()


# --- 6: CHECK constraints ---


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("installment_count", 0),
        ("installment_count", -1),
        ("installment_amount", Decimal("-1.00")),
        ("installment_amount", Decimal("0.00")),  # parcela de R$0 não é condição real
        ("installment_total_amount", Decimal("-1.00")),
        ("installment_total_amount", Decimal("0.00")),
        ("discount_percent", Decimal("-1.00")),
        ("discount_percent", Decimal("100.01")),  # acima de 100% nunca é real
    ),
)
def test_check_constraints_reject_invalid_values(
    integration_database, field, value
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    observation_id = _run_batch_with_options(
        integration_database,
        now,
        (),
    )
    defaults = {
        "price_observation_id": observation_id,
        "installment_count": 3,
        "installment_amount": Decimal("100.00"),
    }
    defaults[field] = value

    with integration_database.sessions() as session:
        session.add(OfferInstallmentOption(**defaults))
        with pytest.raises(IntegrityError):
            session.commit()


def test_check_constraints_accept_boundary_values(integration_database) -> None:
    """100% de desconto é o teto aceito (inclusive) -- confirma que o
    limite não está fora por um (off-by-one)."""
    now = datetime.now(UTC).replace(microsecond=0)
    observation_id = _run_batch_with_options(integration_database, now, ())

    with integration_database.sessions() as session:
        session.add(
            OfferInstallmentOption(
                price_observation_id=observation_id,
                installment_count=1,
                installment_amount=Decimal("0.01"),  # menor valor positivo válido
                discount_percent=Decimal("100.00"),
            )
        )
        session.commit()  # não deve levantar


# --- 7: rollback transacional ---


def test_invalid_option_in_batch_rolls_back_the_whole_transaction(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    observation_id = _run_batch_with_options(integration_database, now, ())

    with pytest.raises(IntegrityError):
        with integration_database.sessions.begin() as session:
            session.add_all(
                (
                    OfferInstallmentOption(
                        price_observation_id=observation_id,
                        installment_count=1,
                        installment_amount=Decimal("100.00"),
                    ),
                    OfferInstallmentOption(
                        price_observation_id=observation_id,
                        installment_count=2,
                        installment_amount=Decimal("50.00"),
                    ),
                    OfferInstallmentOption(
                        price_observation_id=observation_id,
                        installment_count=0,  # inválida -- CHECK rejeita
                        installment_amount=Decimal("1.00"),
                    ),
                )
            )

    with integration_database.sessions() as session:
        remaining = session.scalars(
            select(OfferInstallmentOption).where(
                OfferInstallmentOption.price_observation_id == observation_id
            )
        ).all()
        # nem a 1x nem a 2x válidas foram persistidas -- a transação
        # inteira voltou atrás por causa da 3ª opção inválida.
        assert remaining == []


# --- 8: append-only -- observação N+1 nunca altera opções da observação N ---


def test_successive_observations_keep_independent_option_sets(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    first_id = _run_batch_with_options(
        integration_database,
        now,
        (RawInstallmentOption(installment_count=12, raw_amount="R$ 158,33"),),
    )

    # Segunda coleta da MESMA oferta/missão, mais tarde, com opções
    # DIFERENTES (ex.: a loja passou a oferecer 6x em vez de 12x).
    import asyncio
    from datetime import timedelta

    later = now + timedelta(hours=1)
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter(
            (
                _InstallmentProvider(
                    (RawInstallmentOption(installment_count=6, raw_amount="R$ 316,66"),)
                ),
            )
        ),
        ai_manager=_AlwaysMatchAIManager(),
    )
    # precisa reagendar a missão para ficar due de novo -- TASK-112 fase
    # 3B: MissionSource, não o agregado MissionSchedule, é quem decide.
    with integration_database.sessions.begin() as session:
        schedule = session.scalar(select(MissionSchedule))
        schedule.next_run_at = later
        source = session.scalar(select(MissionSource))
        source.next_run_at = later
    result = asyncio.run(orchestrator.run_batch(now=later))
    assert result.succeeded == 1

    with integration_database.sessions() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2
        second_id = observations[1].id
        assert second_id != first_id

        first_options = session.scalars(
            select(OfferInstallmentOption.installment_count).where(
                OfferInstallmentOption.price_observation_id == first_id
            )
        ).all()
        second_options = session.scalars(
            select(OfferInstallmentOption.installment_count).where(
                OfferInstallmentOption.price_observation_id == second_id
            )
        ).all()
        # a observação antiga continua com 12x, intocada
        assert first_options == [12]
        # a observação nova tem só 6x -- "estado atual" é dela, não da antiga
        assert second_options == [6]
