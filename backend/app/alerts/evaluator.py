"""Avalia fatos de preço sem persistir, publicar ou notificar eventos.

TASK-113 (§33.9): quando existe checkpoint (`AlertCheckpoint`, espelha
`MissionProductAlertState`) para `(mission, product)`, a decisão deixa
de ser só "caiu vs. previous" e passa a exigir um dos três caminhos:

- (A) sem checkpoint (`checkpoint is None`) -- nunca houve alerta para
  este par; comportamento IDÊNTICO ao anterior a esta TASK (qualquer
  queda vs. `previous`, ou cruzamento de target, alerta).
- (B) com checkpoint, melhoria material (`app.alerts.material_
  improvement`) vs. `checkpoint.best_notified_amount`.
- (C) com checkpoint, re-alert de oportunidade: `rearmed_at` setado,
  janela mínima vencida E `MarketPriceAssessment` atual sustenta
  GOOD_DEAL/EXCELLENT_DEAL (`market_assessment_supports_realert`).

Decisão de desenho (não fixada literalmente no texto de §33.9, mas
decorrente da própria estrutura do catálogo de eventos): os caminhos
B/C só se aplicam quando `current.amount < previous.amount` também é
verdade -- `PriceDecreasedPayload` exige isso por contrato
(DEC-045, catálogo intocado) e todo cruzamento de target verdadeiro
(`_target_was_reached`) já implica uma queda estrutural (`current <=
target < previous`). Nunca foi cogitado no desenho alertar sobre um
preço que subiu ou ficou igual -- os exemplos de §1/§16/§18/§35 tratam
sempre de decidir se UMA QUEDA JÁ OCORRIDA merece notificação, nunca de
inventar alerta sem queda local. Isto preserva 100% o comportamento
anterior quando `checkpoint is None` e nunca introduz um alerta em
cenário que a versão anterior não alertaria.

A decisão final PRECISA acontecer sob lock, com o checkpoint lido no
momento exato da decisão (TASK-113, correção pós-plano ponto 2) -- esta
função é pura e não sabe nada sobre transação/lock; quem chama
(`app.collection.orchestration._persist_phase_c`) é responsável por ler
`checkpoint` sob `SELECT ... FOR UPDATE` imediatamente antes de invocar
esta função, e persistir o resultado na MESMA transação.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.alerts.material_improvement import is_material_improvement
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


@dataclass(frozen=True, slots=True)
class AlertCheckpoint:
    """Espelha `app.alerts.models.MissionProductAlertState` como valor
    simples, puro -- ver docstring do módulo."""

    best_notified_amount: Decimal
    last_notified_amount: Decimal
    last_notified_at: datetime
    rearmed_at: datetime | None


@dataclass(frozen=True, slots=True)
class MaterialImprovementPolicy:
    percent: float
    min_amount: float
    max_amount: float


def evaluate_price_alerts(
    mission: Mission,
    criteria: MissionCriteria,
    current: PriceObservation,
    previous: PriceObservation | None = None,
    *,
    checkpoint: AlertCheckpoint | None = None,
    material_improvement_policy: MaterialImprovementPolicy | None = None,
    market_assessment_supports_realert: bool = False,
    realert_window: timedelta | None = None,
    now: datetime | None = None,
) -> tuple[PriceAlertCandidate, ...]:
    """Avalia queda e cruzamento do alvo para uma nova observação persistida.

    V1 (DEC-045): a série de monitoramento compara sempre `amount` (preço do
    produto), nunca `total_amount`. `total_amount` mistura item e frete, e
    quando o frete muda de conhecido para desconhecido (ou vice-versa) entre
    duas observações, comparar `total_amount` gera alerta falso -- ex.: item
    sobe de R$2000 para R$2050 mas o frete conhecido de R$100 desaparece, e
    total (2100 → 2050) pareceria uma queda. Custo final com frete continua
    exclusivo de `app.purchase` (TASK-038/039), que não muda aqui.
    """
    _validate_entities(mission, criteria, current, previous)
    if mission.status is not MissionStatus.ACTIVE:
        return ()
    if current.availability is not Availability.AVAILABLE:
        return ()

    is_decrease = (
        previous is not None
        and previous.availability is Availability.AVAILABLE
        and previous.currency == current.currency
        and current.amount < previous.amount
    )

    # Caminhos B/C do §33.9 comparam `current` contra `checkpoint.
    # best_notified_amount` -- não exigem estruturalmente uma queda local
    # vs. `previous` (uma Offer nova do mesmo Product, sem `previous`
    # próprio ainda, também pode ser materialmente melhor que o melhor já
    # alertado). A queda vs. `previous` só é uma exigência à parte para o
    # candidato `PRICE_DECREASED_V1` especificamente, porque
    # `PriceDecreasedPayload` (DEC-045, catálogo intocado) exige
    # `current_total < previous_total` por contrato -- nunca por causa do
    # gate em si.
    gate_open = checkpoint is None or _passes_checkpoint_gate(
        current_amount=current.amount,
        checkpoint=checkpoint,
        policy=material_improvement_policy or MaterialImprovementPolicy(0.01, 2.0, 50.0),
        market_assessment_supports_realert=market_assessment_supports_realert,
        realert_window=realert_window,
        now=now,
    )

    candidates: list[PriceAlertCandidate] = []
    if is_decrease and gate_open:
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

    if gate_open and _target_was_reached(criteria, current, previous):
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


def _passes_checkpoint_gate(
    *,
    current_amount: Decimal,
    checkpoint: AlertCheckpoint,
    policy: MaterialImprovementPolicy,
    market_assessment_supports_realert: bool,
    realert_window: timedelta | None,
    now: datetime | None,
) -> bool:
    """Caminhos B/C do §33.9 -- só chamado quando já existe checkpoint
    (não é o primeiro alerta) e já houve uma queda local vs. `previous`."""
    material = is_material_improvement(
        reference_amount=checkpoint.best_notified_amount,
        current_amount=current_amount,
        percent=policy.percent,
        min_amount=policy.min_amount,
        max_amount=policy.max_amount,
    )
    if material:
        return True
    if (
        checkpoint.rearmed_at is not None
        and realert_window is not None
        and now is not None
        and (now - checkpoint.last_notified_at) >= realert_window
        and market_assessment_supports_realert
    ):
        return True
    return False


def should_rearm(
    *,
    checkpoint: AlertCheckpoint | None,
    current_amount: Decimal,
    rearm_rise_percent: float,
) -> bool:
    """§33.8: preço subiu materialmente ACIMA de `last_notified_amount`
    depois de um alerta -- habilita um futuro re-alert (caminho C), nunca
    gera alerta por si só. Chamado pelo persistidor (Fase C) sempre que
    existe checkpoint, independente de haver candidato de alerta nesta
    observação."""
    if checkpoint is None or checkpoint.rearmed_at is not None:
        return False
    if checkpoint.last_notified_amount <= 0:
        return False
    threshold = checkpoint.last_notified_amount * (1 + Decimal(str(rearm_rise_percent)))
    return current_amount >= threshold


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
