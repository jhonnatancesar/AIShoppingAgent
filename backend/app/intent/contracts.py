"""Contratos imutáveis de interpretação de intenção do usuário.

Este módulo define apenas a representação estruturada de uma intenção
derivada de uma mensagem livre. Nenhuma execução de domínio, persistência ou
integração de canal pertence a este contrato; a decisão de agir sobre a
intenção cabe a uma tarefa futura.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.missions.models import MissionCommand

MISSION_SOURCE_CODES = frozenset({"pichau", "terabyte", "amazon", "kabum", "magalu"})
"""Fontes selecionáveis na V1, conforme `docs/architecture/telegram.md` e a TASK-055."""

MODEL_CONFIDENCE_VALUES = frozenset({"alta", "baixa"})
"""TASK-083: vocabulário fechado do autorrelato de confiança da IA sobre
`model`. Nunca decide sozinho -- é um entre vários sinais que podem
acionar verificação externa em `interpreter.py`."""


class IntentError(ValueError):
    """Indica intenção malformada antes de qualquer decisão de domínio."""


class IntentKind(StrEnum):
    """Vocabulário fechado de intenções reconhecidas pelo intérprete."""

    CREATE_MISSION = "create_mission"
    QUERY_MISSION = "query_mission"
    MISSION_COMMAND = "mission_command"
    EDIT_MISSION = "edit_mission"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IntentParameters:
    """Parâmetros opcionais extraídos da mensagem.

    Todos os campos espelham conceitos de `MissionCriteria` e
    `mission_sources`; nenhum campo aqui existe sem uma coluna
    correspondente no domínio persistido. `model` (TASK-075) foi
    introduzido em conjunto com a coluna equivalente em
    `MissionCriteria.model` -- não é um conceito novo isolado deste
    contrato.
    """

    search_query: str | None = None
    model: str | None = None
    """TASK-075: modelo/variante completo do produto (ex.: "9950X3D",
    "RTX 4070 Ti"), extraído pela mesma chamada que gera search_query.
    Espelha MissionCriteria.model -- None quando não há modelo específico
    identificável com segurança; nunca reduzido (preserva sufixos como
    Ti/SUPER/XT/XTX/GRE)."""
    model_confidence: str | None = None
    """TASK-083: autorrelato da IA sobre a própria confiança em `model`/
    `search_query` -- "alta" ou "baixa" (`MODEL_CONFIDENCE_VALUES`).
    Sempre `None` quando `model` também é `None`. Não persiste no
    domínio (não espelha nenhuma coluna) -- é só um sinal de
    acionamento de verificação, descartado depois do `IntentInterpreter`."""
    display_query: str | None = None
    """TASK-083 (correção de regressão): candidato plausível da IA normal
    quando a verificação externa era necessária mas não confirmou nada
    -- só presente quando não há contradição determinística conhecida
    (`check_known_family_contradiction`). Espelha `Mission.title`
    (apresentação/contexto), nunca `MissionCriteria.search_query`
    (identidade operacional, que continua reduzida ao `model` cru
    enquanto não confirmada -- é o sinal que `_needs_identity_resolution`
    usa para saber que ainda precisa acionar o `ProductIdentityResolver`).
    `None` sempre que não houver candidato provisório a preservar."""
    target_amount: Decimal | None = None
    target_currency: str | None = None
    sources: tuple[str, ...] = ()
    mission_reference: str | None = None
    clear_target: bool = False
    """TASK-069: só tem sentido para `EDIT_MISSION` — distingue "não mexer no
    alvo" (`clear_target=False`, `target_amount=None`) de "remover o alvo
    existente" (`clear_target=True`), já que os dois casos compartilhariam
    `target_amount=None` sem este campo."""

    def __post_init__(self) -> None:
        if self.search_query is not None and not self.search_query.strip():
            raise IntentError("search_query must not be blank when informed")
        if self.model is not None and not self.model.strip():
            raise IntentError("model must not be blank when informed")
        if self.model_confidence is not None:
            if self.model is None:
                raise IntentError("model_confidence requires model to be informed")
            if self.model_confidence not in MODEL_CONFIDENCE_VALUES:
                raise IntentError("model_confidence must use MODEL_CONFIDENCE_VALUES")
        if self.display_query is not None:
            if self.model is None:
                raise IntentError("display_query requires model to be informed")
            if not self.display_query.strip():
                raise IntentError("display_query must not be blank when informed")
        if (self.target_amount is None) != (self.target_currency is None):
            raise IntentError("target_amount and target_currency must be paired")
        if self.target_amount is not None and (
            not isinstance(self.target_amount, Decimal) or self.target_amount < 0
        ):
            raise IntentError("target_amount must be a non-negative Decimal")
        if self.target_currency is not None and not _is_iso4217(self.target_currency):
            raise IntentError("target_currency must use three uppercase ASCII letters")
        if not isinstance(self.sources, tuple) or any(
            not isinstance(source, str) for source in self.sources
        ):
            raise IntentError("sources must be a tuple of strings")
        if len(set(self.sources)) != len(self.sources):
            raise IntentError("sources must not repeat")
        if any(source not in MISSION_SOURCE_CODES for source in self.sources):
            raise IntentError("sources must belong to the selectable V1 sources")
        if self.mission_reference is not None and not self.mission_reference.strip():
            raise IntentError("mission_reference must not be blank when informed")
        if not isinstance(self.clear_target, bool):
            raise IntentError("clear_target must be a bool")
        if self.clear_target and self.target_amount is not None:
            raise IntentError("clear_target and target_amount are mutually exclusive")


@dataclass(frozen=True, slots=True)
class Intent:
    """Intenção estruturada, agnóstica de canal, derivada de uma mensagem."""

    correlation_id: UUID
    kind: IntentKind
    raw_message: str
    interpreted_at: datetime
    command: MissionCommand | None = None
    parameters: IntentParameters = field(default_factory=IntentParameters)

    def __post_init__(self) -> None:
        if not isinstance(self.correlation_id, UUID):
            raise IntentError("correlation_id must use UUID")
        if not isinstance(self.kind, IntentKind):
            raise IntentError("kind must use IntentKind")
        if not isinstance(self.raw_message, str) or not self.raw_message.strip():
            raise IntentError("raw_message must not be blank")
        if (
            not isinstance(self.interpreted_at, datetime)
            or self.interpreted_at.utcoffset() is None
        ):
            raise IntentError("interpreted_at must include a timezone")
        if self.kind is IntentKind.MISSION_COMMAND:
            if not isinstance(self.command, MissionCommand):
                raise IntentError("mission_command intent requires a MissionCommand")
        elif self.command is not None:
            raise IntentError("command is only valid for the mission_command intent")
        if not isinstance(self.parameters, IntentParameters):
            raise IntentError("parameters must use IntentParameters")
        if self.kind is IntentKind.EDIT_MISSION:
            if not self.parameters.mission_reference:
                raise IntentError("edit_mission intent requires a mission_reference")
            wants_target_change = (
                self.parameters.clear_target
                or self.parameters.target_amount is not None
            )
            wants_source_change = bool(self.parameters.sources)
            if not wants_target_change and not wants_source_change:
                raise IntentError(
                    "edit_mission intent requires a target or source change"
                )


def _is_iso4217(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 3
        and value.isascii()
        and value.isalpha()
        and value.isupper()
    )
