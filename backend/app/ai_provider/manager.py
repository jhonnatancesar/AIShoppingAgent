"""Managers dos perfis USER e ADMIN/DEV sobre provedores internos."""

from app.ai_provider.contracts import (
    AIProvider,
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    AIResponse,
    validate_provider_response,
)
from app.ai_provider.gemini import GeminiProvider
from app.ai_provider.groq import GroqProvider
from app.ai_provider.telemetry import AIAttemptOutcome, record_ai_attempt
from app.core.config import Settings, get_settings
from app.core.resilience import CIRCUITS, CircuitOpenError
from app.observability.metrics import observe_resilience_event
from app.users.models import UserRole


class UserAIProviderManager:
    """Encaminha exclusivamente o perfil USER ao provider Gemini."""

    def __init__(
        self,
        provider: AIProvider,
        *,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
    ) -> None:
        self._provider = provider
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_open_seconds = circuit_open_seconds

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile is not UserRole.USER:
            raise AIRequestError("USER manager accepts only USER profile")
        circuit = _provider_circuit(
            self._provider,
            failure_threshold=self._circuit_failure_threshold,
            open_seconds=self._circuit_open_seconds,
        )
        try:
            circuit.before_call()
            response = await self._provider.generate(request)
            validate_provider_response(request, response)
        except CircuitOpenError:
            observe_resilience_event("ai", "circuit_open")
            error = AIProviderUnavailable("provider_circuit_open")
            _record_provider_error(request, self._provider, error, fallback=False)
            raise error from None
        except AIProviderError as error:
            circuit.record_failure(transient=_counts_for_circuit(error))
            _record_provider_error(request, self._provider, error, fallback=False)
            raise
        circuit.record_success()
        record_ai_attempt(
            request,
            provider=response.provider,
            model=response.model,
            outcome=AIAttemptOutcome.SUCCEEDED,
            fallback=False,
        )
        return response


class AdminDevAIProviderManager:
    """Política única de ADMIN/DEV: Gemini Flash e, se configurado, Groq como fallback.

    USER, ADMIN e DEV usam o mesmo modelo Gemini Flash para operações
    automáticas de IA (DEC-050); a distinção de papel é só de
    permissão/autorização, nunca de modelo. Fallback só por disponibilidade,
    nunca dois modelos Gemini equivalentes em sequência.
    """

    def __init__(
        self,
        gemini: AIProvider,
        *,
        groq: AIProvider | None = None,
        max_attempts: int = 3,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("AI max_attempts must be between 1 and 3")
        self._gemini = gemini
        self._groq = groq
        self._max_attempts = max_attempts
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_open_seconds = circuit_open_seconds

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile not in {UserRole.ADMIN, UserRole.DEV}:
            raise AIRequestError("ADMIN/DEV manager accepts only ADMIN or DEV profile")

        tiers = [self._gemini]
        if self._groq is not None:
            tiers.append(self._groq)

        last_error: AIProviderError | None = None
        for index, provider in enumerate(tiers[: self._max_attempts]):
            fallback = index > 0
            circuit = _provider_circuit(
                provider,
                failure_threshold=self._circuit_failure_threshold,
                open_seconds=self._circuit_open_seconds,
            )
            try:
                circuit.before_call()
                response = await provider.generate(request)
                validate_provider_response(request, response)
            except CircuitOpenError:
                observe_resilience_event("ai", "circuit_open")
                error = AIProviderUnavailable("provider_circuit_open")
                _record_provider_error(request, provider, error, fallback=fallback)
                last_error = error
                continue
            except (AIProviderQuotaExceeded, AIProviderUnavailable) as error:
                circuit.record_failure(transient=True)
                _record_provider_error(request, provider, error, fallback=fallback)
                last_error = error
                continue
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

        if last_error is not None:
            raise last_error
        raise AIProviderError("provider_error", retryable=False)


def build_user_ai_provider_manager(
    settings: Settings | None = None,
) -> UserAIProviderManager:
    """Monta o perfil USER somente quando a credencial segura está disponível."""
    current = settings or get_settings()
    if current.gemini_api_key_user is None:
        raise AIRequestError(
            "AISHOPPING_GEMINI_API_KEY_USER is required for USER profile"
        )
    provider = GeminiProvider(
        current.gemini_api_key_user,
        current.gemini_model,
        timeout_seconds=current.external_http_timeout_seconds,
    )
    return UserAIProviderManager(
        provider,
        circuit_failure_threshold=current.circuit_failure_threshold,
        circuit_open_seconds=current.circuit_open_seconds,
    )


def build_admin_dev_ai_provider_manager(
    settings: Settings | None = None,
) -> AdminDevAIProviderManager:
    """Monta a política de ADMIN/DEV: Gemini Flash e, se configurado, Groq como fallback.

    Usa uma chave Gemini dedicada (`AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`),
    separada da chave do perfil `USER`, para que a cota gratuita do
    ADMIN/DEV nunca compita com a cota compartilhada de usuários reais.
    O modelo é o mesmo Gemini Flash do perfil `USER` (`Settings.gemini_model`,
    DEC-050) — nenhum nível Gemini Pro/preview entra na cascata.
    """
    current = settings or get_settings()
    if current.gemini_api_key_admin_dev is None:
        raise AIRequestError(
            "AISHOPPING_GEMINI_API_KEY_ADMIN_DEV is required for ADMIN/DEV profile"
        )
    gemini = GeminiProvider(
        current.gemini_api_key_admin_dev,
        current.gemini_model,
        timeout_seconds=current.external_http_timeout_seconds,
    )
    groq = (
        GroqProvider(
            current.groq_api_key,
            current.groq_model,
            timeout_seconds=current.external_http_timeout_seconds,
        )
        if current.groq_api_key is not None
        else None
    )
    return AdminDevAIProviderManager(
        gemini,
        groq=groq,
        max_attempts=current.safe_retry_max_attempts,
        circuit_failure_threshold=current.circuit_failure_threshold,
        circuit_open_seconds=current.circuit_open_seconds,
    )


def _provider_circuit(
    provider: AIProvider, *, failure_threshold: int, open_seconds: float
):
    # Modelo faz parte da operação: cota do Gemini premium não bloqueia o Flash.
    key = f"ai:{provider.provider_id}:{provider.model}:generate"
    return CIRCUITS.get(
        key,
        failure_threshold=failure_threshold,
        open_seconds=open_seconds,
    )


def _counts_for_circuit(error: AIProviderError) -> bool:
    return isinstance(error, (AIProviderQuotaExceeded, AIProviderUnavailable))


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
