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

MISSION_SOURCE_CODES = frozenset({"pichau", "terabyte", "amazon", "kabum"})
"""Fontes selecionáveis na V1, conforme `docs/TELEGRAM.md` e a TASK-055."""


class IntentError(ValueError):
    """Indica intenção malformada antes de qualquer decisão de domínio."""


class IntentKind(StrEnum):
    """Vocabulário fechado de intenções reconhecidas pelo intérprete."""

    CREATE_MISSION = "create_mission"
    QUERY_MISSION = "query_mission"
    MISSION_COMMAND = "mission_command"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IntentParameters:
    """Parâmetros opcionais extraídos da mensagem.

    Todos os campos reaproveitam conceitos já existentes em
    `MissionCriteria` e `mission_sources`; nenhum campo novo de domínio é
    introduzido por este contrato.
    """

    search_query: str | None = None
    target_amount: Decimal | None = None
    target_currency: str | None = None
    sources: tuple[str, ...] = ()
    mission_reference: str | None = None

    def __post_init__(self) -> None:
        if self.search_query is not None and not self.search_query.strip():
            raise IntentError("search_query must not be blank when informed")
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


def _is_iso4217(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 3
        and value.isascii()
        and value.isalpha()
        and value.isupper()
    )
