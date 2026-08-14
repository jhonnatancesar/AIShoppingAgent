"""Contratos agnósticos de provedor para o AI Provider Manager."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from re import fullmatch
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.users.models import UserRole


class AIManagerError(RuntimeError):
    """Erro base seguro exposto pela fronteira de IA."""


class AIRequestError(AIManagerError, ValueError):
    """Requisição inválida antes de qualquer chamada a provedor."""


class AIProviderError(AIManagerError):
    """Falha sanitizada de provedor, sem resposta bruta ou credenciais."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        quota_reset_at: datetime | None = None,
    ) -> None:
        if not _is_stable_code(code):
            raise ValueError("provider error code must use stable snake_case")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        if quota_reset_at is not None:
            _require_aware(quota_reset_at, "quota_reset_at")
        self.quota_reset_at = quota_reset_at


class AIProviderUnavailable(AIProviderError):
    def __init__(self, code: str = "provider_unavailable") -> None:
        super().__init__(code, retryable=True)


class AIProviderQuotaExceeded(AIProviderError):
    def __init__(
        self,
        code: str = "provider_quota_exceeded",
        *,
        quota_reset_at: datetime | None = None,
    ) -> None:
        super().__init__(code, retryable=False, quota_reset_at=quota_reset_at)


class AIProviderCapabilityUnsupported(AIProviderError):
    """TASK-083: provider não suporta uma capability exigida pela
    requisição (ex.: `require_search_grounding`). Nunca fallback
    silencioso dentro do provider -- sempre este erro tipado, para o
    chamador decidir explicitamente o que fazer (nunca uma `Exception`
    genérica nem uma resposta comum disfarçada de atendida)."""

    def __init__(self, capability: str) -> None:
        if not _is_stable_code(capability):
            raise ValueError("capability must use stable snake_case")
        self.capability = capability
        super().__init__(f"capability_unsupported_{capability}", retryable=False)


class AIMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class AIMessage:
    role: AIMessageRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, AIMessageRole):
            raise AIRequestError("message role must use AIMessageRole")
        _require_text(self.content, "message content")


@dataclass(frozen=True, slots=True)
class AIRequest:
    request_id: UUID
    profile: UserRole
    purpose: str
    messages: tuple[AIMessage, ...]
    requested_at: datetime
    require_search_grounding: bool = False
    """TASK-083: pede ao provider que dispõe do modelo de grounding via
    busca web quando decidir a resposta -- não obriga o modelo a
    pesquisar de fato (ver `AIResponse.grounding_performed`, a única
    fonte de verdade sobre se a busca realmente aconteceu). Default
    `False`: toda chamada existente continua funcionando sem alteração.
    Provider que não suportar a capability levanta
    `AIProviderCapabilityUnsupported` -- nunca finge suporte nem faz
    fallback silencioso por conta própria."""

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, UUID):
            raise AIRequestError("request_id must use UUID")
        if not isinstance(self.profile, UserRole):
            raise AIRequestError("profile must use USER, ADMIN or DEV")
        if not _is_stable_code(self.purpose):
            raise AIRequestError("purpose must use stable snake_case")
        if (
            not isinstance(self.messages, tuple)
            or not self.messages
            or any(not isinstance(message, AIMessage) for message in self.messages)
        ):
            raise AIRequestError("messages must be a tuple with at least one AIMessage")
        _require_aware(self.requested_at, "requested_at")
        if not isinstance(self.require_search_grounding, bool):
            raise AIRequestError("require_search_grounding must be a bool")


@dataclass(frozen=True, slots=True)
class AIResponse:
    request_id: UUID
    provider: str
    model: str
    content: str
    finished_at: datetime
    grounding_requested: bool = False
    """TASK-083: eco de `AIRequest.require_search_grounding` -- permite ao
    chamador ler o resultado sem precisar guardar a requisição original.
    Distinção A do contrato: "pesquisa foi disponibilizada ao provider"."""
    grounding_performed: bool = False
    """TASK-083: distinção B -- só `True` quando o provider confirma, a
    partir de metadado estruturado devolvido pela própria API (nunca por
    inspeção de texto), que uma busca real aconteceu. Disponibilizar a
    ferramenta (`grounding_requested=True`) não implica isto -- o modelo
    pode decidir não pesquisar."""
    grounding_sources: tuple[str, ...] = ()
    """TASK-083: distinção C -- evidência (URIs citadas) quando o
    provider expõe fontes da busca real. Pode ficar vazio mesmo com
    `grounding_performed=True` (nem toda API expõe chunk de fonte, só a
    confirmação de que buscou) -- por isso nunca é, sozinho, a condição
    para considerar a resposta verificada; use `grounding_performed`."""

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, UUID):
            raise AIRequestError("response request_id must use UUID")
        if not _is_stable_code(self.provider):
            raise AIRequestError("provider must use stable snake_case")
        _require_text(self.model, "model")
        _require_text(self.content, "response content")
        _require_aware(self.finished_at, "finished_at")
        if not isinstance(self.grounding_requested, bool):
            raise AIRequestError("grounding_requested must be a bool")
        if not isinstance(self.grounding_performed, bool):
            raise AIRequestError("grounding_performed must be a bool")
        if self.grounding_performed and not self.grounding_requested:
            raise AIRequestError(
                "grounding_performed requires grounding_requested to be true"
            )
        if not isinstance(self.grounding_sources, tuple) or any(
            not isinstance(source, str) or not source.strip()
            for source in self.grounding_sources
        ):
            raise AIRequestError(
                "grounding_sources must be a tuple of non-blank strings"
            )
        if self.grounding_sources and not self.grounding_performed:
            raise AIRequestError(
                "grounding_sources require grounding_performed to be true"
            )


@runtime_checkable
class AIProvider(Protocol):
    """Adaptador interno; somente o manager pode chamá-lo diretamente."""

    provider_id: str
    model: str

    async def generate(self, request: AIRequest) -> AIResponse: ...


@runtime_checkable
class AIProviderManager(Protocol):
    """Única porta que módulos da aplicação podem usar para acessar IA."""

    async def generate(self, request: AIRequest) -> AIResponse: ...


def validate_provider_response(request: AIRequest, response: AIResponse) -> None:
    """Rejeita respostas que não pertencem à requisição enviada."""
    if response.request_id != request.request_id:
        raise AIProviderError("provider_request_mismatch", retryable=False)
    if response.finished_at < request.requested_at:
        raise AIProviderError("provider_time_mismatch", retryable=False)


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AIRequestError(f"{field_name} must not be blank")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise AIRequestError(f"{field_name} must include a timezone")


def _is_stable_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", value) is not None
    )
