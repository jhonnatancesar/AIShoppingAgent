"""Managers de AI exclusivamente sobre o César Core."""

from app.ai_provider.cesar_core import CesarCoreAIProvider
from app.ai_provider.contracts import (
    AIProvider,
    AIProviderCapabilityUnsupported,
    AIProviderError,
    AIProviderManager,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    AIResponse,
    validate_provider_response,
)
from app.ai_provider.telemetry import AIAttemptOutcome, record_ai_attempt
from app.core.config import Settings, get_settings
from app.core.resilience import CIRCUITS, CircuitOpenError
from app.observability.metrics import observe_resilience_event
from app.users.models import UserRole


def build_user_ai_provider_manager(
    settings: Settings | None = None,
) -> AIProviderManager:
    """Monta o perfil USER oficial exclusivamente sobre o César Core."""
    current = settings or get_settings()
    return _build_cesar_core_manager(current, UserRole.USER)


def build_admin_dev_ai_provider_manager(
    settings: Settings | None = None,
) -> AIProviderManager:
    """Monta ADMIN/DEV oficial exclusivamente sobre o César Core."""
    current = settings or get_settings()
    return _build_cesar_core_manager(current, None)


class CesarCoreAIProviderManager:
    """Manager oficial: toda AI, inclusive grounding, usa o César Core."""

    def __init__(
        self,
        provider: AIProvider,
        *,
        profile: UserRole | None,
        circuit_failure_threshold: int,
        circuit_open_seconds: float,
    ) -> None:
        self._provider = provider
        self._profile = profile
        self._threshold = circuit_failure_threshold
        self._open_seconds = circuit_open_seconds

    async def generate(self, request: AIRequest) -> AIResponse:
        allowed = (
            {UserRole.USER}
            if self._profile is UserRole.USER
            else {UserRole.ADMIN, UserRole.DEV}
        )
        if request.profile not in allowed:
            raise AIRequestError("Request profile does not match manager")
        return await _attempt_provider(
            request,
            self._provider,
            circuit_failure_threshold=self._threshold,
            circuit_open_seconds=self._open_seconds,
            fallback=False,
        )


def _build_cesar_core_manager(
    settings: Settings, profile: UserRole | None
) -> CesarCoreAIProviderManager:
    if settings.cesar_core_api_key_file is None:
        raise AIRequestError("Cesar Core requires AISHOPPING_CESAR_CORE_API_KEY_FILE")
    ai_profile = "user" if profile is UserRole.USER else "admin_dev"
    return CesarCoreAIProviderManager(
        CesarCoreAIProvider(
            api_key_file=settings.cesar_core_api_key_file,
            base_url=settings.cesar_core_base_url,
            service=settings.cesar_core_service,
            ai_profile=ai_profile,
            service_class=settings.cesar_core_service_class,
            max_tokens=settings.cesar_core_max_tokens,
            timeout_seconds=settings.cesar_core_timeout_seconds,
        ),
        profile=profile,
        circuit_failure_threshold=settings.circuit_failure_threshold,
        circuit_open_seconds=settings.circuit_open_seconds,
    )


def _provider_circuit(
    provider: AIProvider, *, failure_threshold: int, open_seconds: float
):
    # O target lógico faz parte da operação para isolar circuitos do gateway.
    key = f"ai:{provider.provider_id}:{provider.model}:generate"
    return CIRCUITS.get(
        key,
        failure_threshold=failure_threshold,
        open_seconds=open_seconds,
    )


async def _attempt_provider(
    request: AIRequest,
    provider: AIProvider,
    *,
    circuit_failure_threshold: int,
    circuit_open_seconds: float,
    fallback: bool,
) -> AIResponse:
    """Uma única tentativa contra o adapter do Core, com circuit breaker e
    telemetria compartilhada pelos perfis USER e ADMIN/DEV.

    Sempre propaga a exceção (nunca "engole" para tentar de novo aqui
    dentro) -- decidir se continua para o próximo tier é responsabilidade
    exclusiva de quem chama, olhando o tipo da exceção propagada.
    """
    circuit = _provider_circuit(
        provider,
        failure_threshold=circuit_failure_threshold,
        open_seconds=circuit_open_seconds,
    )
    try:
        circuit.before_call()
        response = await provider.generate(request)
        validate_provider_response(request, response)
    except CircuitOpenError:
        observe_resilience_event("ai", "circuit_open")
        error = AIProviderUnavailable("provider_circuit_open")
        _record_provider_error(request, provider, error, fallback=fallback)
        raise error from None
    except AIProviderCapabilityUnsupported as error:
        # TASK-083: incapacidade de capability nunca conta como falha do
        # circuit breaker -- isso poluiria o estado do provider com
        # "falhas" que sempre vão se repetir para requisições comuns, sem
        # grounding, que continuam funcionando normalmente.
        _record_provider_error(request, provider, error, fallback=fallback)
        raise
    except (AIProviderQuotaExceeded, AIProviderUnavailable) as error:
        circuit.record_failure(transient=True)
        _record_provider_error(request, provider, error, fallback=fallback)
        raise
    except AIProviderError as error:
        circuit.record_failure(transient=False)
        _record_provider_error(request, provider, error, fallback=fallback)
        raise
    circuit.record_success()
    record_ai_attempt(
        request,
        provider=response.provider,
        model=response.model,
        outcome=AIAttemptOutcome.SUCCEEDED,
        fallback=fallback,
    )
    return response


def _record_provider_error(
    request: AIRequest,
    provider: AIProvider,
    error: AIProviderError,
    *,
    fallback: bool,
) -> None:
    if isinstance(error, AIProviderQuotaExceeded):
        outcome = AIAttemptOutcome.QUOTA_EXCEEDED
    elif isinstance(error, AIProviderUnavailable):
        outcome = AIAttemptOutcome.UNAVAILABLE
    else:
        outcome = AIAttemptOutcome.FAILED
    record_ai_attempt(
        request,
        provider=provider.provider_id,
        model=provider.model,
        outcome=outcome,
        fallback=fallback,
        quota_reset_at=error.quota_reset_at,
    )
