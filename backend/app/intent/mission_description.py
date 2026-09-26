"""TASK-128 etapa 3 -- a descrição de uma missão nova passa pela IA antes
de virar missão, no Telegram (`/criar_missao`) e no site (formulário de
criar missão). Decisões do usuário (2026-09-26):

- pedido vago ("algo bom e barato", "um presente") -> o GG responde que
  não entendeu e pede uma descrição melhor, nos dois canais;
- a IA não respondeu depois de toda a cascata de fallback do César Core
  (cota esgotada / nenhum provedor disponível) -> o GG avisa para tentar
  abrir a missão mais tarde, nos dois canais -- nunca confunde isso com
  "pedido vago".

A descrição vai CRUA para o `IntentInterpreter` (a detecção de "só o
código do modelo", que dispara a verificação de identidade por busca,
depende do texto original); quem decide o que é vago é o próprio prompt
do interpretador (`kind: "unknown"`)."""

from dataclasses import dataclass
from enum import StrEnum

from app.ai_provider import AIProviderError
from app.intent.contracts import Intent, IntentError, IntentKind
from app.intent.interpreter import IntentInterpreter
from app.users.models import UserRole

MISSION_DESCRIPTION_UNCLEAR_MESSAGE = (
    "Não entendi o que você quer encontrar. Descreva melhor o produto — "
    'por exemplo: "placa de vídeo RTX 4060" ou "cadeira gamer".'
)
MISSION_DESCRIPTION_AI_UNAVAILABLE_MESSAGE = (
    "A cota de IA foi excedida no momento — nenhum provedor de IA conseguiu "
    "responder. Tente abrir a missão mais tarde."
)


class MissionDescriptionOutcome(StrEnum):
    UNDERSTOOD = "understood"
    UNCLEAR = "unclear"
    AI_UNAVAILABLE = "ai_unavailable"


@dataclass(frozen=True, slots=True)
class MissionDescriptionCheck:
    outcome: MissionDescriptionOutcome
    intent: Intent | None = None


def mission_description_outcome(intent: Intent) -> MissionDescriptionOutcome:
    """Só um pedido de criar missão COM o que procurar é entendido --
    qualquer outra classificação (inclusive `unknown`, que é como o
    prompt sinaliza pedido vago) vira "não entendi, descreva melhor"."""
    # `IntentParameters` já recusa `search_query` em branco quando informado.
    if (
        intent.kind is IntentKind.CREATE_MISSION
        and intent.parameters.search_query is not None
    ):
        return MissionDescriptionOutcome.UNDERSTOOD
    return MissionDescriptionOutcome.UNCLEAR


async def check_mission_description(
    interpreter: IntentInterpreter, description: str, *, profile: UserRole
) -> MissionDescriptionCheck:
    """`AIProviderError` depois da cascata do César Core = nenhum provedor
    respondeu (`AI_UNAVAILABLE`); descrição em branco ou intenção sem o
    que procurar = `UNCLEAR`."""
    try:
        intent = await interpreter.interpret(description, profile=profile)
    except AIProviderError:
        return MissionDescriptionCheck(MissionDescriptionOutcome.AI_UNAVAILABLE)
    except IntentError:
        return MissionDescriptionCheck(MissionDescriptionOutcome.UNCLEAR)
    outcome = mission_description_outcome(intent)
    if outcome is MissionDescriptionOutcome.UNCLEAR:
        return MissionDescriptionCheck(outcome)
    return MissionDescriptionCheck(outcome, intent)
