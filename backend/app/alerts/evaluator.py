"""Avalia fatos de preço sem persistir, publicar ou notificar eventos."""

from dataclasses import dataclass
from uuid import UUID

from app.collection.models import PriceObservation
from app.collection.normalization import Availability
from app.events import (
    AggregateType,
    EventType,
    PriceDecreasedPayload,
    PriceTargetReachedPayload,
    validate_event_payload,
)
from app.missions.models import Mission, MissionCriteria, MissionStatus


class PriceAlertEvaluationError(ValueError):
    """Indica entidades inconsistentes para avaliação de alertas."""


type PriceAlertPayload = PriceDecreasedPayload | PriceTargetReachedPayload


@dataclass(frozen=True, slots=True)
class PriceAlertCandidate:
    """Evento de alerta validado, ainda não persistido nem publicado."""

    event_type: EventType
    aggregate_type: AggregateType
    aggregate_id: UUID
    payload: PriceAlertPayload

    def __post_init__(self) -> None:
        spec = validate_event_payload(self.event_type, self.payload)
        if spec.aggregate_type is not self.aggregate_type:
            raise PriceAlertEvaluationError(
                "candidate aggregate does not match catalog"
            )


def evaluate_price_alerts(
    mission: Mission,
    criteria: MissionCriteria,
    current: PriceObservation,
    previous: PriceObservation | None = None,
) -> tuple[PriceAlertCandidate, ...]:
    """Avalia queda e cruzamento do alvo para uma nova observação persistida.

    V1 (DEC-045): a série de monitoramento compara sempre `amount` (preço do
    produto), nunca `total_amount`. `total_amount` mistura item e frete, e
    quando o frete muda de conhecido para desconhecido (ou vice-versa) entre
    duas observações, comparar `total_amount` gera alerta falso — ex.: item
    sobe de R$2000 para R$2050 mas o frete conhecido de R$100 desaparece, e
    total (2100 → 2050) pareceria uma queda. Custo final com frete continua
    exclusivo de `app.purchase` (TASK-038/039), que não muda aqui.
    """
    _validate_entities(mission, criteria, current, previous)
    if mission.status is not MissionStatus.ACTIVE:
        return ()
    if current.availability is not Availability.AVAILABLE:
        return ()

    candidates: list[PriceAlertCandidate] = []
    if (
        previous is not None
        and previous.availability is Availability.AVAILABLE
        and previous.currency == current.currency
        and current.amount < previous.amount
    ):
        # Campos legados `*_total` do catálogo (DEC-045): para alertas da V1
        # carregam o preço do produto (`amount`), não `total_amount`, para
        # manter a mesma base usada na decisão acima. Não renomear os campos.
        payload = PriceDecreasedPayload(
            offer_id=current.offer_id,
            observation_id=current.id,
            previous_observation_id=previous.id,
            previous_total=previous.amount,
            current_total=current.amount,
            currency=current.currency,
        )
        candidates.append(
            PriceAlertCandidate(
                EventType.PRICE_DECREASED_V1,
                AggregateType.OFFER,
                current.offer_id,
                payload,
            )
        )

    if _target_was_reached(criteria, current, previous):
        target_amount = criteria.target_amount
        if target_amount is None:  # protected by _target_was_reached
            raise AssertionError("target amount is required")
        payload = PriceTargetReachedPayload(
            mission_id=mission.id,
            offer_id=current.offer_id,
            observation_id=current.id,
            target_total=target_amount,
            current_total=current.amount,
            currency=current.currency,
        )
        candidates.append(
            PriceAlertCandidate(
                EventType.PRICE_TARGET_REACHED_V1,
                AggregateType.MISSION,
                mission.id,
                payload,
            )
        )

    return tuple(candidates)


def _target_was_reached(
    criteria: MissionCriteria,
    current: PriceObservation,
    previous: PriceObservation | None,
) -> bool:
    target = criteria.target_amount
    currency = criteria.target_currency
    if target is None:
        return False
    if currency != current.currency or current.amount > target:
        return False
    return not (
        previous is not None
        and previous.availability is Availability.AVAILABLE
        and previous.currency == currency
        and previous.amount <= target
    )


def _validate_entities(
    mission: Mission,
    criteria: MissionCriteria,
    current: PriceObservation,
    previous: PriceObservation | None,
) -> None:
    identifiers = (mission.id, criteria.mission_id, current.id, current.offer_id)
    if any(not isinstance(identifier, UUID) for identifier in identifiers):
        raise PriceAlertEvaluationError("persisted entity identifiers are required")
    if criteria.mission_id != mission.id:
        raise PriceAlertEvaluationError("criteria do not belong to mission")
    if (criteria.target_amount is None) is not (criteria.target_currency is None):
        raise PriceAlertEvaluationError("target amount and currency must form a pair")
    if previous is None:
        return
    if not isinstance(previous.id, UUID):
        raise PriceAlertEvaluationError("persisted previous observation is required")
    if previous.offer_id != current.offer_id:
        raise PriceAlertEvaluationError("observations must belong to the same offer")
    if previous.id == current.id:
        raise PriceAlertEvaluationError("observations must be distinct")
    if previous.observed_at > current.observed_at:
        raise PriceAlertEvaluationError("previous observation must not be newer")
