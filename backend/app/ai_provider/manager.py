"""Managers dos perfis USER e ADMIN/DEV sobre provedores internos."""

from app.ai_provider.contracts import (
    AIProvider,
    AIProviderCapabilityUnsupported,
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
from app.ai_provider.openrouter import OpenRouterProvider
from app.ai_provider.telemetry import AIAttemptOutcome, record_ai_attempt
from app.core.config import Settings, get_settings
from app.core.resilience import CIRCUITS, CircuitOpenError
from app.observability.metrics import observe_resilience_event
from app.users.models import UserRole


class UserAIProviderManager:
    """Cascata exclusivamente gratuita do perfil USER.

    TASK-083: `grounding_provider` é opcional -- quando ausente (default),
    uma requisição com `require_search_grounding=True` levanta
    `AIProviderCapabilityUnsupported` sem tentar `provider` (que nunca foi
    configurado/validado para essa capability). Quando presente, toda
    requisição de grounding vai exclusivamente para ele -- `provider`
    normal nunca é tentado nesse caso.
    """

    def __init__(
        self,
        provider: AIProvider,
        *,
        groq: AIProvider | None = None,
        openrouter: AIProvider | None = None,
        grounding_provider: AIProvider | None = None,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
    ) -> None:
        self._provider = provider
        self._groq = groq
        self._openrouter = openrouter
        self._grounding_provider = grounding_provider
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_open_seconds = circuit_open_seconds

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile is not UserRole.USER:
            raise AIRequestError("USER manager accepts only USER profile")
        if request.require_search_grounding:
            if self._grounding_provider is None:
                raise AIProviderCapabilityUnsupported("search_grounding")
            provider = self._grounding_provider
            return await _attempt_provider(
                request,
                provider,
                circuit_failure_threshold=self._circuit_failure_threshold,
                circuit_open_seconds=self._circuit_open_seconds,
                fallback=False,
            )

        tiers = [self._provider]
        tiers.extend(
            provider
            for provider in (self._groq, self._openrouter)
            if provider is not None
        )
        return await _attempt_tiers(
            request,
            tiers,
            circuit_failure_threshold=self._circuit_failure_threshold,
            circuit_open_seconds=self._circuit_open_seconds,
        )


class AdminDevAIProviderManager:
    """Política gratuita preservada do perfil ADMIN.

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
        grounding_provider: AIProvider | None = None,
        max_attempts: int = 3,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("AI max_attempts must be between 1 and 3")
        self._gemini = gemini
        self._groq = groq
        self._grounding_provider = grounding_provider
        self._max_attempts = max_attempts
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_open_seconds = circuit_open_seconds

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile is not UserRole.ADMIN:
            raise AIRequestError("ADMIN manager accepts only ADMIN profile")

        if request.require_search_grounding:
            # TASK-083: grounding vai exclusivamente para o provider
            # dedicado -- Groq nunca suporta essa capability, então
            # incluí-lo aqui só adicionaria uma tentativa fadada ao erro
            # (e arriscaria mascarar a falta de grounding real com uma
            # resposta comum do Groq apresentada como verificada).
            if self._grounding_provider is None:
                raise AIProviderCapabilityUnsupported("search_grounding")
            return await _attempt_provider(
                request,
                self._grounding_provider,
                circuit_failure_threshold=self._circuit_failure_threshold,
                circuit_open_seconds=self._circuit_open_seconds,
                fallback=False,
            )

        tiers = [self._gemini]
        if self._groq is not None:
            tiers.append(self._groq)

        return await _attempt_tiers(
            request,
            tiers[: self._max_attempts],
            circuit_failure_threshold=self._circuit_failure_threshold,
            circuit_open_seconds=self._circuit_open_seconds,
        )


class DevAIProviderManager:
    """Rota paga isolada do DEV, sem fallback para providers USER/ADMIN."""

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
        if request.profile is not UserRole.DEV:
            raise AIRequestError("DEV manager accepts only DEV profile")
        return await _attempt_provider(
            request,
            self._provider,
            circuit_failure_threshold=self._circuit_failure_threshold,
            circuit_open_seconds=self._circuit_open_seconds,
            fallback=False,
        )


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
    grounding_provider = GeminiProvider(
        current.gemini_api_key_user,
        current.gemini_grounding_model,
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
    openrouter = (
        OpenRouterProvider(
            current.openrouter_api_key,
            current.openrouter_free_model,
            timeout_seconds=current.external_http_timeout_seconds,
        )
        if current.openrouter_api_key is not None
        else None
    )
    return UserAIProviderManager(
        provider,
        groq=groq,
        openrouter=openrouter,
        grounding_provider=grounding_provider,
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
    grounding_provider = GeminiProvider(
        current.gemini_api_key_admin_dev,
        current.gemini_grounding_model,
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
        grounding_provider=grounding_provider,
        max_attempts=current.safe_retry_max_attempts,
        circuit_failure_threshold=current.circuit_failure_threshold,
        circuit_open_seconds=current.circuit_open_seconds,
    )


def build_dev_ai_provider_manager(
    settings: Settings | None = None,
) -> DevAIProviderManager:
    """Monta a rota DEV paga e isolada no OpenRouter."""
    current = settings or get_settings()
    if current.openrouter_api_key is None:
        raise AIRequestError(
            "AISHOPPING_OPENROUTER_API_KEY is required for DEV profile"
        )
    provider = OpenRouterProvider(
        current.openrouter_api_key,
        current.openrouter_dev_model,
        web_search_enabled=True,
        web_search_engine=current.openrouter_web_search_engine,
        timeout_seconds=current.external_http_timeout_seconds,
    )
    return DevAIProviderManager(
        provider,
        circuit_failure_threshold=current.circuit_failure_threshold,
        circuit_open_seconds=current.circuit_open_seconds,
    )


async def _attempt_tiers(
    request: AIRequest,
    tiers: list[AIProvider],
    *,
    circuit_failure_threshold: int,
    circuit_open_seconds: float,
) -> AIResponse:
    """Tenta uma lista finita; só disponibilidade/cota permite fallback."""
    last_error: AIProviderError | None = None
    for index, provider in enumerate(tiers):
        try:
            return await _attempt_provider(
                request,
                provider,
                circuit_failure_threshold=circuit_failure_threshold,
                circuit_open_seconds=circuit_open_seconds,
                fallback=index > 0,
            )
        except (AIProviderQuotaExceeded, AIProviderUnavailable) as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise AIProviderError("provider_error", retryable=False)


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


async def _attempt_provider(
    request: AIRequest,
    provider: AIProvider,
    *,
    circuit_failure_threshold: int,
    circuit_open_seconds: float,
    fallback: bool,
) -> AIResponse:
    """Uma única tentativa contra um provider, com circuit breaker e
    telemetria -- comportamento compartilhado entre `UserAIProviderManager`,
    a cascata normal do `AdminDevAIProviderManager` e o caminho dedicado de
    grounding dos dois, para as três nunca divergirem sutilmente.

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
