"""Testes de grounding/busca via AIProviderManager (TASK-083).

Só mocks -- nenhuma chamada real de IA. Cobre a distinção estrutural
entre "pesquisa disponibilizada ao provider" (`AIRequest.
require_search_grounding`), "pesquisa realmente executada"
(`AIResponse.grounding_performed`, só a partir de metadado real da API) e
evidência (`AIResponse.grounding_sources`).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.ai_provider import (
    AdminDevAIProviderManager,
    AIMessage,
    AIMessageRole,
    AIProviderCapabilityUnsupported,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIResponse,
    GeminiProvider,
    GroqProvider,
    UserAIProviderManager,
)
from app.users.models import UserRole
from pydantic import SecretStr


def _request(*, require_search_grounding: bool = False) -> AIRequest:
    return AIRequest(
        request_id=uuid4(),
        profile=UserRole.ADMIN,
        purpose="interpret_purchase_intent",
        messages=(AIMessage(AIMessageRole.USER, "9800X3D"),),
        requested_at=datetime.now(UTC),
        require_search_grounding=require_search_grounding,
    )


class _FakeModels:
    """Mesmo papel do fake em test_gemini_user_profile.py, com suporte a
    devolver `candidates`/`grounding_metadata` simulados."""

    def __init__(self, *, response_text: str = "resposta", candidates=None) -> None:
        self.response_text = response_text
        self.candidates = candidates
        self.call: dict | None = None

    async def generate_content(self, **kwargs):
        self.call = kwargs
        result = SimpleNamespace(text=self.response_text)
        if self.candidates is not None:
            result.candidates = self.candidates
        return result


class _FakeAsyncClient:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = _FakeAsyncClient(models)

    def close(self) -> None:
        pass


def _gemini_provider(models: _FakeModels) -> GeminiProvider:
    client = _FakeClient(models)
    return GeminiProvider(SecretStr("test-key"), client_factory=lambda **_k: client)


def _grounding_metadata(*, queries=(), chunk_uris=()):
    chunks = [
        SimpleNamespace(web=SimpleNamespace(uri=uri, title="t", domain="d"))
        for uri in chunk_uris
    ]
    return SimpleNamespace(
        web_search_queries=list(queries),
        grounding_chunks=chunks,
    )


# --- 1/8: request comum, sem grounding -> Gemini continua como antes ---


@pytest.mark.anyio
async def test_gemini_plain_request_behaves_as_before() -> None:
    models = _FakeModels(response_text="resposta comum")
    provider = _gemini_provider(models)

    response = await provider.generate(_request(require_search_grounding=False))

    assert response.content == "resposta comum"
    assert response.grounding_requested is False
    assert response.grounding_performed is False
    assert response.grounding_sources == ()
    assert models.call["config"] is None  # sem system_instruction nem grounding


# --- 2/8: request comum, sem grounding -> Groq continua como antes ---


@pytest.mark.anyio
async def test_groq_plain_request_behaves_as_before(monkeypatch) -> None:
    calls: list[dict] = []

    class _FakeGroqClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            calls.append(json)

            class _Response:
                status_code = 200

                def json(self) -> dict:
                    return {"choices": [{"message": {"content": "resposta groq"}}]}

            return _Response()

    provider = GroqProvider(SecretStr("test-key"), client_factory=_FakeGroqClient)

    response = await provider.generate(_request(require_search_grounding=False))

    assert response.content == "resposta groq"
    assert response.grounding_requested is False
    assert len(calls) == 1


# --- 3/8: grounding solicitado -> Gemini recebe a ferramenta de busca ---


@pytest.mark.anyio
async def test_gemini_grounding_requested_adds_search_tool_to_config() -> None:
    models = _FakeModels()
    provider = _gemini_provider(models)

    await provider.generate(_request(require_search_grounding=True))

    config = models.call["config"]
    assert config is not None
    assert config.tools is not None
    assert config.tools[0].google_search is not None


# --- 4/8: Gemini executa grounding de fato -> metadata indica verdadeiro ---


@pytest.mark.anyio
async def test_gemini_grounding_performed_when_metadata_has_search_evidence() -> None:
    candidates = [
        SimpleNamespace(
            grounding_metadata=_grounding_metadata(
                queries=["AMD Ryzen 9800X3D linha"],
                chunk_uris=("https://amd.example.invalid/9800x3d",),
            )
        )
    ]
    models = _FakeModels(candidates=candidates)
    provider = _gemini_provider(models)

    response = await provider.generate(_request(require_search_grounding=True))

    assert response.grounding_requested is True
    assert response.grounding_performed is True
    assert response.grounding_sources == ("https://amd.example.invalid/9800x3d",)


# --- 5/8: Gemini NÃO executa grounding -> resultado indica falso ---


@pytest.mark.parametrize(
    "candidates",
    [
        [],
        [SimpleNamespace(grounding_metadata=None)],
        [SimpleNamespace(grounding_metadata=_grounding_metadata())],
    ],
    ids=["sem_candidates", "sem_metadata", "metadata_vazia"],
)
@pytest.mark.anyio
async def test_gemini_grounding_not_performed_without_real_search_evidence(
    candidates,
) -> None:
    models = _FakeModels(candidates=candidates)
    provider = _gemini_provider(models)

    response = await provider.generate(_request(require_search_grounding=True))

    assert response.grounding_requested is True
    assert response.grounding_performed is False
    assert response.grounding_sources == ()


@pytest.mark.anyio
async def test_gemini_grounding_metadata_ignored_when_not_requested() -> None:
    """Disponibilizar não é o mesmo que ter sido pedido -- se a requisição
    não pediu grounding, nem inspecionamos metadata (economia + nunca
    reportar grounding não solicitado)."""
    candidates = [
        SimpleNamespace(grounding_metadata=_grounding_metadata(queries=["algo"]))
    ]
    models = _FakeModels(candidates=candidates)
    provider = _gemini_provider(models)

    response = await provider.generate(_request(require_search_grounding=False))

    assert response.grounding_requested is False
    assert response.grounding_performed is False


# --- 6/8: grounding solicitado -> Groq informa capability não suportada ---


@pytest.mark.anyio
async def test_groq_rejects_grounding_with_typed_capability_error() -> None:
    class _PoisonClient:
        def __init__(self, **_kwargs) -> None:
            raise AssertionError("Groq não deveria nem tentar a chamada HTTP")

    provider = GroqProvider(SecretStr("test-key"), client_factory=_PoisonClient)

    with pytest.raises(AIProviderCapabilityUnsupported) as excinfo:
        await provider.generate(_request(require_search_grounding=True))

    assert excinfo.value.capability == "search_grounding"


# --- roteamento dedicado do grounding (TASK-083 SUBETAPA 2) ---
#
# Nomes de modelo exclusivos por teste: a chave do circuit breaker é
# `provider_id:model`, e `CIRCUITS` é um registro global persistente entre
# testes -- usar um nome real poluiria o circuito compartilhado com outros
# testes que usam um GeminiProvider de verdade.


class _FakeAIProvider:
    """Fake mínimo de AIProvider -- sucesso fixo ou exceção fixa."""

    def __init__(self, provider_id: str, model: str, *, result: object = "ok") -> None:
        self.provider_id = provider_id
        self.model = model
        self._result = result
        self.calls = 0

    async def generate(self, request: AIRequest):
        self.calls += 1
        if isinstance(self._result, BaseException):
            raise self._result
        return AIResponse(
            request_id=request.request_id,
            provider=self.provider_id,
            model=self.model,
            content=str(self._result),
            finished_at=datetime.now(UTC),
        )


class _PoisonAIProvider:
    """Falha o teste se for chamado -- usado para provar que um tier NÃO
    deveria ser tentado."""

    def __init__(self, provider_id: str = "poison", model: str = "poison") -> None:
        self.provider_id = provider_id
        self.model = model

    async def generate(self, request: AIRequest):
        raise AssertionError(f"{self.provider_id} não deveria ser chamado aqui")


# B) request grounding ADMIN/DEV -> usa o provider dedicado, nunca o normal/Groq


@pytest.mark.anyio
async def test_admin_dev_grounding_request_uses_dedicated_provider_only() -> None:
    grounding = _FakeAIProvider(
        "gemini", f"gemini-2.5-flash-test-{uuid4().hex[:8]}", result="verificado"
    )
    manager = AdminDevAIProviderManager(
        _PoisonAIProvider("gemini-normal"),
        groq=_PoisonAIProvider("groq"),
        grounding_provider=grounding,
    )

    response = await manager.generate(_request(require_search_grounding=True))

    assert response.content == "verificado"
    assert grounding.calls == 1


@pytest.mark.anyio
async def test_user_manager_grounding_request_uses_dedicated_provider_only() -> None:
    grounding = _FakeAIProvider(
        "gemini", f"gemini-2.5-flash-test-{uuid4().hex[:8]}", result="verificado"
    )
    manager = UserAIProviderManager(
        _PoisonAIProvider("gemini-normal"), grounding_provider=grounding
    )

    response = await manager.generate(
        AIRequest(
            request_id=uuid4(),
            profile=UserRole.USER,
            purpose="interpret_purchase_intent",
            messages=(AIMessage(AIMessageRole.USER, "9800X3D"),),
            requested_at=datetime.now(UTC),
            require_search_grounding=True,
        )
    )

    assert response.content == "verificado"
    assert grounding.calls == 1


# C) grounding_provider não configurado -> erro tipado, sem tocar nenhum tier


@pytest.mark.anyio
async def test_admin_dev_grounding_without_dedicated_provider_raises_typed_error() -> (
    None
):
    manager = AdminDevAIProviderManager(
        _PoisonAIProvider("gemini-normal"), groq=_PoisonAIProvider("groq")
    )

    with pytest.raises(AIProviderCapabilityUnsupported) as excinfo:
        await manager.generate(_request(require_search_grounding=True))
    assert excinfo.value.capability == "search_grounding"


@pytest.mark.anyio
async def test_user_manager_grounding_without_dedicated_provider_raises_typed_error() -> (
    None
):
    manager = UserAIProviderManager(_PoisonAIProvider("gemini-normal"))

    with pytest.raises(AIProviderCapabilityUnsupported) as excinfo:
        await manager.generate(
            AIRequest(
                request_id=uuid4(),
                profile=UserRole.USER,
                purpose="interpret_purchase_intent",
                messages=(AIMessage(AIMessageRole.USER, "9800X3D"),),
                requested_at=datetime.now(UTC),
                require_search_grounding=True,
            )
        )
    assert excinfo.value.capability == "search_grounding"


# D) request grounding -> Groq nunca é chamado, mesmo que o grounding falhe


@pytest.mark.anyio
async def test_admin_dev_grounding_failure_never_falls_back_to_groq() -> None:
    grounding = _FakeAIProvider(
        "gemini",
        f"gemini-2.5-flash-test-{uuid4().hex[:8]}",
        result=AIProviderQuotaExceeded(),
    )
    manager = AdminDevAIProviderManager(
        _PoisonAIProvider("gemini-normal"),
        groq=_PoisonAIProvider("groq"),
        grounding_provider=grounding,
    )

    with pytest.raises(AIProviderQuotaExceeded):
        await manager.generate(_request(require_search_grounding=True))
    # `_PoisonAIProvider("groq")` levantaria AssertionError se tivesse sido
    # chamado -- o teste passar já prova que não foi.


# F) falha de capability do próprio grounding_provider não polui o circuito


@pytest.mark.anyio
async def test_grounding_capability_failure_does_not_trip_circuit() -> None:
    model = f"gemini-2.5-flash-test-{uuid4().hex[:8]}"
    always_rejects = _FakeAIProvider(
        "gemini", model, result=AIProviderCapabilityUnsupported("search_grounding")
    )
    manager = AdminDevAIProviderManager(
        _PoisonAIProvider("gemini-normal"), grounding_provider=always_rejects
    )

    for _ in range(6):  # acima do circuit_failure_threshold padrão (5)
        with pytest.raises(AIProviderCapabilityUnsupported):
            await manager.generate(_request(require_search_grounding=True))

    # Mesmo provider/model, request nova -- se a falha de capability
    # tivesse aberto o circuito, isto levantaria CircuitOpenError em vez
    # de propagar o AIProviderCapabilityUnsupported de sempre.
    with pytest.raises(AIProviderCapabilityUnsupported):
        await manager.generate(_request(require_search_grounding=True))


# G) falha/quota no grounding não impede requisição normal no Gemini comum
# (circuitos isolados por model, sem precisar de nenhum mecanismo novo)


@pytest.mark.anyio
async def test_grounding_quota_failures_do_not_affect_normal_gemini_circuit() -> None:
    grounding = _FakeAIProvider(
        "gemini",
        f"gemini-2.5-flash-test-{uuid4().hex[:8]}",
        result=AIProviderQuotaExceeded(),
    )
    normal = _FakeAIProvider(
        "gemini", f"gemini-3.6-flash-test-{uuid4().hex[:8]}", result="resposta normal"
    )
    manager = AdminDevAIProviderManager(normal, grounding_provider=grounding)

    for _ in range(5):  # == circuit_failure_threshold padrão -- abre o circuito
        with pytest.raises(AIProviderQuotaExceeded):
            await manager.generate(_request(require_search_grounding=True))
    # Circuito do grounding agora aberto (provider/model exclusivos deste
    # teste) -- a próxima tentativa de grounding falha por indisponibilidade
    # do circuito, não mais por quota. O ponto do teste é o próximo `assert`:
    # mesmo com o circuito do grounding totalmente aberto, o Gemini normal
    # (model diferente) nunca é afetado.
    with pytest.raises(AIProviderUnavailable):
        await manager.generate(_request(require_search_grounding=True))

    response = await manager.generate(_request(require_search_grounding=False))
    assert response.content == "resposta normal"


# --- 8/8: nenhum comportamento existente muda quando o flag é False ---


@pytest.mark.anyio
async def test_default_request_without_grounding_flag_is_fully_backward_compatible() -> (
    None
):
    request = AIRequest(
        request_id=uuid4(),
        profile=UserRole.ADMIN,
        purpose="interpret_purchase_intent",
        messages=(AIMessage(AIMessageRole.USER, "oi"),),
        requested_at=datetime.now(UTC),
    )
    assert request.require_search_grounding is False

    models = _FakeModels(response_text="normal")
    provider = _gemini_provider(models)
    response = await provider.generate(request)
    assert response.content == "normal"
    assert response.grounding_requested is False
