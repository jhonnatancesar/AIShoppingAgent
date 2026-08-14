"""Testes do resolvedor de identidade de produto (TASK-083 SUBETAPA 3).

Só mocks/fakes -- nenhuma chamada real a Kabum/Amazon, nenhum Playwright
de verdade. O resolver ainda não está integrado ao CollectionOrchestrator
(SUBETAPA 4); estes testes cobrem só o componente isolado.
"""

import asyncio
import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.collection import identity_resolution
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    ProductIdentityResolver,
    RawCollectedOffer,
    ResolvedProductIdentity,
)
from app.collection.identity_resolution import StoreProductIdentityResolver
from app.collection.providers.stores import KabumProvider
from app.core.resilience import CircuitOpenError


def _offer(
    source_code: str, title: str, *, external_id: str = "1"
) -> RawCollectedOffer:
    return RawCollectedOffer(
        source_code=source_code,
        url=f"https://{source_code}.example.invalid/{external_id}",
        title=title,
        collected_at=datetime.now(UTC),
        external_id=external_id,
    )


class _FakeProvider:
    """Fake de `CollectionProvider` -- offers fixos, erro fixo, ou delay."""

    def __init__(
        self,
        source_code: str,
        *,
        offers: tuple[RawCollectedOffer, ...] = (),
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.source_code = source_code
        self._offers = offers
        self._error = error
        self._delay = delay
        self.calls = 0

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        now = datetime.now(UTC)
        return CollectionResult(self.source_code, now, now, self._offers)


class _PoisonProvider:
    """Falha o teste se `.collect()` for chamado."""

    def __init__(self, source_code: str) -> None:
        self.source_code = source_code

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        raise AssertionError(f"{self.source_code} não deveria ser consultado aqui")


# --- A: Kabum resolve no primeiro resultado -> Amazon não é chamada ---


@pytest.mark.anyio
async def test_scenario_a_kabum_resolves_first_result_amazon_not_called() -> None:
    kabum = _FakeProvider(
        "kabum",
        offers=(_offer("kabum", "Processador AMD Ryzen 7 9800X3D 8-Core AM5"),),
    )
    amazon = _PoisonProvider("amazon")
    resolver = StoreProductIdentityResolver([kabum, amazon])

    identity = await resolver.resolve("9800X3D")

    assert identity == ResolvedProductIdentity(
        model="9800X3D",
        search_query="Processador AMD Ryzen 7 9800X3D",
        source="kabum",
    )
    assert kabum.calls == 1


# --- B: ignora candidato de SKU vizinho antes do candidato correto ---


@pytest.mark.anyio
async def test_scenario_b_ignores_neighbor_sku_before_correct_candidate() -> None:
    kabum = _FakeProvider(
        "kabum",
        offers=(
            _offer("kabum", "Processador AMD Ryzen 7 7800X3D", external_id="1"),
            _offer("kabum", "Processador AMD Ryzen 7 9800X3D", external_id="2"),
        ),
    )
    resolver = StoreProductIdentityResolver([kabum, _PoisonProvider("amazon")])

    identity = await resolver.resolve("9800X3D")

    assert identity is not None
    assert identity.search_query == "Processador AMD Ryzen 7 9800X3D"


# --- C: Kabum vazio -> Amazon resolve ---


@pytest.mark.anyio
async def test_scenario_c_empty_kabum_falls_back_to_amazon() -> None:
    kabum = _FakeProvider("kabum", offers=())
    amazon = _FakeProvider(
        "amazon", offers=(_offer("amazon", "Processador AMD Ryzen 7 9800X3D"),)
    )
    resolver = StoreProductIdentityResolver([kabum, amazon])

    identity = await resolver.resolve("9800X3D")

    assert identity is not None
    assert identity.source == "amazon"


# --- D: Kabum lança exceção/timeout -> Amazon resolve ---


@pytest.mark.anyio
async def test_scenario_d_kabum_exception_falls_back_to_amazon() -> None:
    kabum = _FakeProvider("kabum", error=RuntimeError("falha inesperada do provider"))
    amazon = _FakeProvider(
        "amazon", offers=(_offer("amazon", "Processador AMD Ryzen 7 9800X3D"),)
    )
    resolver = StoreProductIdentityResolver([kabum, amazon])

    identity = await resolver.resolve("9800X3D")

    assert identity is not None
    assert identity.source == "amazon"


@pytest.mark.anyio
async def test_scenario_d_kabum_timeout_falls_back_to_amazon() -> None:
    kabum = _FakeProvider("kabum", delay=1.0)
    amazon = _FakeProvider(
        "amazon", offers=(_offer("amazon", "Processador AMD Ryzen 7 9800X3D"),)
    )
    resolver = StoreProductIdentityResolver(
        [kabum, amazon], per_provider_timeout_seconds=0.05
    )

    identity = await resolver.resolve("9800X3D")

    assert identity is not None
    assert identity.source == "amazon"


# --- E: Kabum + Amazon inconclusivos -> None ---


@pytest.mark.anyio
async def test_scenario_e_both_inconclusive_returns_none() -> None:
    kabum = _FakeProvider(
        "kabum", offers=(_offer("kabum", "Processador AMD Ryzen 9 9900X3D"),)
    )
    amazon = _FakeProvider(
        "amazon", offers=(_offer("amazon", "Processador AMD Ryzen 9 9950X3D"),)
    )
    resolver = StoreProductIdentityResolver([kabum, amazon])

    assert await resolver.resolve("9800X3D") is None


# --- F: Kabum + Amazon falham -> None ---


@pytest.mark.anyio
async def test_scenario_f_both_fail_returns_none() -> None:
    kabum = _FakeProvider("kabum", error=RuntimeError("boom"))
    amazon = _FakeProvider("amazon", error=RuntimeError("boom"))
    resolver = StoreProductIdentityResolver([kabum, amazon])

    assert await resolver.resolve("9800X3D") is None


# --- G: separador tolerado ("9800-X3D" reconhecido como 9800X3D) ---


@pytest.mark.anyio
async def test_scenario_g_tolerates_separator_in_title() -> None:
    kabum = _FakeProvider(
        "kabum", offers=(_offer("kabum", "Processador AMD Ryzen 7 9800-X3D"),)
    )
    resolver = StoreProductIdentityResolver([kabum, _PoisonProvider("amazon")])

    identity = await resolver.resolve("9800X3D")

    assert identity is not None
    assert identity.search_query == "Processador AMD Ryzen 7 9800-X3D"


# --- H: SKU vizinho nunca aceito ---


@pytest.mark.anyio
async def test_scenario_h_neighbor_sku_never_accepted() -> None:
    kabum = _FakeProvider(
        "kabum", offers=(_offer("kabum", "Processador AMD Ryzen 9 9900X3D"),)
    )
    amazon = _FakeProvider("amazon", offers=())
    resolver = StoreProductIdentityResolver([kabum, amazon])

    assert await resolver.resolve("9800X3D") is None


# --- I: para de examinar candidatos após correspondência conclusiva ---


@pytest.mark.anyio
async def test_scenario_i_stops_scanning_after_conclusive_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_titles: list[str] = []
    original = identity_resolution.title_matches_model

    def _spy(model: str, title: str) -> bool:
        checked_titles.append(title)
        return original(model, title)

    monkeypatch.setattr(identity_resolution, "title_matches_model", _spy)

    kabum = _FakeProvider(
        "kabum",
        offers=(
            _offer("kabum", "Processador AMD Ryzen 7 9800X3D", external_id="1"),
            _offer(
                "kabum",
                "Processador AMD Ryzen 7 9800X3D Segunda Unidade",
                external_id="2",
            ),
        ),
    )
    resolver = StoreProductIdentityResolver([kabum, _PoisonProvider("amazon")])

    identity = await resolver.resolve("9800X3D")

    assert identity is not None
    assert len(checked_titles) == 1


# --- J: resolver não persiste nada nem depende de Session/AsyncSession ---


def test_resolver_module_has_no_persistence_or_session_dependency() -> None:
    """Verifica os bindings importados pelo módulo (não a prosa das
    docstrings, que menciona Session/AsyncSession só para explicar a
    ausência dessa dependência)."""
    module_names = {name.lower() for name in vars(identity_resolution)}
    assert not any("session" in name for name in module_names)
    assert not any("sqlalchemy" in name for name in module_names)


def test_resolve_signature_takes_only_model_no_session_argument() -> None:
    signature = inspect.signature(StoreProductIdentityResolver.resolve)
    assert list(signature.parameters) == ["self", "model"]


# --- K: circuit_namespace de identidade isolado do circuito de coleta normal ---


def test_scenario_k_default_providers_use_isolated_circuit_namespace() -> None:
    resolver = StoreProductIdentityResolver()  # instâncias reais dedicadas
    kabum_identity = resolver._providers[0]  # noqa: SLF001
    assert kabum_identity.source_code == "kabum"

    for _ in range(kabum_identity._circuit.failure_threshold):  # noqa: SLF001
        kabum_identity._circuit.record_failure(transient=True)  # noqa: SLF001

    with pytest.raises(CircuitOpenError):
        kabum_identity._circuit.before_call()  # noqa: SLF001

    normal_kabum = KabumProvider()  # namespace "search" (default), mesma fonte
    normal_kabum._circuit.before_call()  # noqa: SLF001 -- não levanta


# --- L: timeout do primeiro provider não impede o segundo (orçamento) ---


@pytest.mark.anyio
async def test_scenario_l_provider_timeout_does_not_block_the_next_one() -> None:
    kabum = _FakeProvider("kabum", delay=1.0)
    amazon = _FakeProvider(
        "amazon", offers=(_offer("amazon", "Processador AMD Ryzen 7 9800X3D"),)
    )
    resolver = StoreProductIdentityResolver(
        [kabum, amazon], per_provider_timeout_seconds=0.05
    )

    identity = await resolver.resolve("9800X3D")

    assert identity is not None
    assert identity.source == "amazon"


def test_total_budget_is_the_coherent_sum_of_per_provider_budgets() -> None:
    """Orçamento global = soma dos orçamentos por provider -- único
    mecanismo de deadline, sem camada de retry/deadline nova por cima."""
    resolver = StoreProductIdentityResolver(
        [_PoisonProvider("kabum"), _PoisonProvider("amazon")],
        per_provider_timeout_seconds=6.0,
    )
    assert resolver._total_budget_seconds == 12.0  # noqa: SLF001


# --- contrato: ResolvedProductIdentity / ProductIdentityResolver ---


def test_resolved_product_identity_rejects_blank_fields() -> None:
    from app.collection.contracts import CollectionContractError

    with pytest.raises(CollectionContractError):
        ResolvedProductIdentity(model="", search_query="x", source="kabum")
    with pytest.raises(CollectionContractError):
        ResolvedProductIdentity(model="9800X3D", search_query="", source="kabum")
    with pytest.raises(CollectionContractError):
        ResolvedProductIdentity(model="9800X3D", search_query="x", source="")


def test_store_resolver_satisfies_protocol_structurally() -> None:
    assert isinstance(StoreProductIdentityResolver(), ProductIdentityResolver)


def test_resolver_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="per_provider_timeout_seconds"):
        StoreProductIdentityResolver(
            [_PoisonProvider("kabum")], per_provider_timeout_seconds=0
        )


@pytest.mark.anyio
async def test_resolver_uses_uuid_placeholder_mission_id_per_call() -> None:
    """`CollectionRequest.mission_id` é obrigatório mas irrelevante para
    resolução de identidade (não persiste, não referencia missão real
    nesta subetapa) -- confirma que um placeholder válido é gerado sem
    exigir nenhum contexto externo."""
    captured: list[CollectionRequest] = []

    class _CapturingProvider:
        source_code = "kabum"

        async def collect(self, request: CollectionRequest) -> CollectionResult:
            captured.append(request)
            now = datetime.now(UTC)
            return CollectionResult(self.source_code, now, now, ())

    resolver = StoreProductIdentityResolver(
        [_CapturingProvider(), _PoisonProvider("amazon")]
    )
    assert await resolver.resolve("9800X3D") is None
    assert len(captured) == 1
    assert isinstance(captured[0].mission_id, UUID)
    assert captured[0].search_query == "9800X3D"
