"""Testes rápidos das partes puras da coordenação de coleta (TASK-079).

A prova de correção do autodeadlock resolvido (fronteira transacional,
ausência de `await` externo com lock retido, serialização por
`mission_id`) é a suíte de integração com PostgreSQL real
(`tests/integration/test_collection_orchestration.py`) -- fidelidade de
`FOR UPDATE`/upsert/isolamento de transação não é algo que um `AsyncMock`
consiga verificar de forma confiável. Este arquivo cobre só as funções
puras (sem sessão) e a validação de construção do orquestrador; os testes
com `AsyncMock` das funções que tocam `AsyncSession` ficam em
`test_collection_orchestration_async.py` -- cobertura/sinal de regressão
rápido, não a prova de concorrência real.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from app.ai_provider import AIProviderError, AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import CollectionResult, RawCollectedOffer
from app.collection.errors import (
    CollectionContractError,
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.normalization import PriceNormalizer
from app.collection.orchestration import (
    _GENERIC_SEARCH_CANDIDATE_LIMIT,
    CollectionOrchestrator,
    _failure_code,
    _filter_deterministic_candidates,
    _is_confirmed_external_block,
    _limit_generic_candidates,
    _raw_evidence,
    _safe_source,
    _select_amazon_lowest_price,
    _title_looks_like_bundle,
    _title_matches_model,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class _StubAIManager:
    """AIProviderManager de teste: respostas fixas por `purpose`, ou falha."""

    def __init__(
        self, responses: dict[str, str] | None = None, *, raises: bool = False
    ) -> None:
        self._responses = responses or {}
        self._raises = raises
        self.calls: list[str] = []

    async def generate(self, request):
        self.calls.append(request.purpose)
        if self._raises:
            raise AIProviderError("stub_failure", retryable=False)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=self._responses.get(request.purpose, "{}"),
            finished_at=datetime.now(UTC),
        )


def _raw(
    *,
    source: str = "pichau",
    external_id: str = "stable",
    title: str = "Synthetic product",
    raw_price: str = "R$ 100,00",
    url: str = "https://example.invalid/offer",
):
    return RawCollectedOffer(
        source_code=source,
        url=url,
        title=title,
        collected_at=NOW + timedelta(seconds=1),
        external_id=external_id,
        raw_price=raw_price,
        raw_currency="BRL",
        raw_shipping="Frete grátis",
        raw_availability="Em estoque",
        evidence={"nested": ["safe", object()]},
    )


def _normalized_offers(*raws: RawCollectedOffer):
    result = CollectionResult(
        raws[0].source_code, NOW, NOW + timedelta(seconds=2), raws
    )
    return PriceNormalizer().normalize_result(result).offers


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderCircuitOpenError("pichau"), "circuit_open"),
        (ProviderBlockedError("pichau", 403), "provider_blocked"),
        (CollectionNormalizationError("bad"), "normalization_failed"),
        (CollectionContractError("bad"), "normalization_failed"),
        (ProviderNavigationError("pichau", 500), "provider_unavailable"),
        (TimeoutError(), "provider_unavailable"),
        (RuntimeError(), "collection_failed"),
    ],
)
def test_failure_codes_are_closed(error: Exception, code: str) -> None:
    assert _failure_code(error) == code


@pytest.mark.parametrize(
    ("error", "confirmed"),
    [
        (ProviderBlockedError("pichau", 403), True),
        (ProviderBlockedError("pichau", 429), True),
        # 401 fica de fora de proposito (DEC-047): normalmente representa
        # autenticacao/credencial/configuracao, nao protecao anti-bot, e
        # nao deve crescer exponencialmente como se fosse rate limit.
        (ProviderBlockedError("pichau", 401), False),
        # mesmo erro, mas status ambiguo (selector ausente/oferta vazia com
        # HTTP 200): nao e bloqueio confirmado, DEC-047 nao aciona backoff.
        (ProviderBlockedError("pichau", 200), False),
        (ProviderBlockedError("pichau", None), False),
        (ProviderCircuitOpenError("pichau"), False),
        (ProviderNavigationError("pichau", 500), False),
        (TimeoutError(), False),
        (CollectionNormalizationError("bad"), False),
        (CollectionContractError("bad"), False),
        (RuntimeError("internal"), False),
    ],
)
def test_is_confirmed_external_block_only_matches_403_429(
    error: Exception, confirmed: bool
) -> None:
    assert _is_confirmed_external_block(error) is confirmed


def test_evidence_is_bounded_json_and_source_is_allowlisted() -> None:
    evidence = _raw_evidence(_raw())
    assert evidence["source_code"] == "pichau"
    assert evidence["provider_evidence"]["nested"][0] == "safe"
    assert isinstance(evidence["provider_evidence"]["nested"][1], str)
    assert _safe_source("unknown-dynamic") == "other"


# TASK-075: filtro determinístico de modelo -- casamento tolerante a
# separador, limite alfanumérico e sufixo de variante forte.
@pytest.mark.parametrize(
    ("model", "title", "expected"),
    [
        ("9950X3D", "Processador AMD Ryzen 9 9950X3D", True),
        ("9950X3D", "Processador AMD Ryzen 9 9950X3D2", False),
        ("9950X3D", "Processador AMD A9950X3D Edition", False),
        ("9950x3d", "processador amd ryzen 9 9950X3D", True),  # case-insensitive
        ("9950X3D", "Processador AMD Ryzen 9 9900X", False),  # modelo ausente
        ("RTX 4070", "Placa de Vídeo NVIDIA RTX 4070", True),
        ("RTX 4070", "Placa de Vídeo NVIDIA RTX 4070 Ti", False),
        ("RTX 4070", "Placa de Vídeo NVIDIA RTX 4070 SUPER", False),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX 4070 Ti", True),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX 4070 Ti SUPER", False),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX 4070 Ti OC 12GB", True),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX4070Ti Gaming", True),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX-4070-Ti Gaming", True),
    ],
)
def test_title_matches_model(model: str, title: str, expected: bool) -> None:
    assert _title_matches_model(model, title) is expected


def test_title_looks_like_bundle_rejects_full_system_not_asked() -> None:
    assert (
        _title_looks_like_bundle(
            "Processador AMD Ryzen 9 9950X3D",
            "Computador Gamer Completo Ryzen 9 9950X3D RTX 4090",
        )
        is True
    )


def test_title_looks_like_bundle_allows_when_mission_asks_for_bundle() -> None:
    assert (
        _title_looks_like_bundle(
            "Kit Upgrade Ryzen 9 9950X3D",
            "Kit Upgrade Placa-mãe + Ryzen 9 9950X3D",
        )
        is False
    )


def test_title_looks_like_bundle_false_for_plain_component() -> None:
    assert (
        _title_looks_like_bundle(
            "Processador AMD Ryzen 9 9950X3D",
            "Processador AMD Ryzen 9 9950X3D, 4.4 GHz, AM5",
        )
        is False
    )


def test_filter_deterministic_candidates_rejects_wrong_model_and_bundle() -> None:
    from types import SimpleNamespace

    criteria = SimpleNamespace(
        search_query="Processador AMD Ryzen 9 9950X3D", model="9950X3D"
    )
    offers = _normalized_offers(
        _raw(external_id="1", title="Processador AMD Ryzen 9 9950X3D"),
        _raw(external_id="2", title="Processador AMD Ryzen 9 9950X3D2"),
        _raw(
            external_id="3",
            title="Computador Gamer Completo Ryzen 9 9950X3D RTX 4090",
        ),
    )

    survivors = _filter_deterministic_candidates(criteria, offers)

    assert [item.raw_offer.external_id for item in survivors] == ["1"]


def test_filter_deterministic_candidates_ambiguous_without_model_survives() -> None:
    """Sem `criteria.model`, o filtro de modelo não roda -- só o de bundle."""
    from types import SimpleNamespace

    criteria = SimpleNamespace(search_query="processador AMD", model=None)
    offers = _normalized_offers(
        _raw(external_id="1", title="Processador AMD Ryzen 9 9900X"),
        _raw(external_id="2", title="Computador Gamer AMD Completo"),
    )

    survivors = _filter_deterministic_candidates(criteria, offers)

    assert [item.raw_offer.external_id for item in survivors] == ["1"]


def test_select_amazon_lowest_price_keeps_only_cheapest() -> None:
    offers = _normalized_offers(
        _raw(external_id="B01", title="Processador X", raw_price="R$ 4.500,00"),
        _raw(external_id="B02", title="Processador X", raw_price="R$ 4.000,00"),
        _raw(external_id="B03", title="Processador X", raw_price="R$ 4.500,00"),
    )

    survivors = _select_amazon_lowest_price(offers)

    assert len(survivors) == 1
    assert survivors[0].raw_offer.external_id == "B02"
    assert survivors[0].amount == Decimal("4000.00")


def test_select_amazon_lowest_price_tie_break_by_external_id() -> None:
    offers = _normalized_offers(
        _raw(external_id="B999", title="Processador X", raw_price="R$ 4.000,00"),
        _raw(external_id="B001", title="Processador X", raw_price="R$ 4.000,00"),
        _raw(external_id="B500", title="Processador X", raw_price="R$ 4.000,00"),
    )

    survivors = _select_amazon_lowest_price(offers)

    assert len(survivors) == 1
    assert survivors[0].raw_offer.external_id == "B001"


def test_select_amazon_lowest_price_empty_input() -> None:
    assert _select_amazon_lowest_price(()) == ()


# --- TASK-082: limitação de candidatos em busca genérica ---


def test_limit_generic_candidates_keeps_only_cheapest_up_to_limit() -> None:
    offers = _normalized_offers(
        _raw(external_id="1", title="Cadeira A", raw_price="R$ 900,00"),
        _raw(external_id="2", title="Cadeira B", raw_price="R$ 500,00"),
        _raw(external_id="3", title="Cadeira C", raw_price="R$ 700,00"),
        _raw(external_id="4", title="Cadeira D", raw_price="R$ 300,00"),
        _raw(external_id="5", title="Cadeira E", raw_price="R$ 1.000,00"),
    )

    survivors = _limit_generic_candidates(offers, limit=3)

    assert [item.raw_offer.external_id for item in survivors] == ["4", "2", "3"]


def test_limit_generic_candidates_tie_break_by_external_id() -> None:
    offers = _normalized_offers(
        _raw(external_id="9", title="Mouse A", raw_price="R$ 200,00"),
        _raw(external_id="1", title="Mouse B", raw_price="R$ 200,00"),
        _raw(external_id="5", title="Mouse C", raw_price="R$ 200,00"),
    )

    survivors = _limit_generic_candidates(offers, limit=2)

    assert [item.raw_offer.external_id for item in survivors] == ["1", "5"]


def test_limit_generic_candidates_fewer_than_limit_keeps_all() -> None:
    offers = _normalized_offers(
        _raw(external_id="1", title="Teclado A", raw_price="R$ 200,00"),
    )

    survivors = _limit_generic_candidates(offers, limit=_GENERIC_SEARCH_CANDIDATE_LIMIT)

    assert len(survivors) == 1


def test_limit_generic_candidates_empty_input() -> None:
    assert _limit_generic_candidates((), limit=3) == ()


@pytest.mark.parametrize(
    "options",
    [
        {"schedule_interval_minutes": 0},
        {"schedule_stagger_seconds": -1},
        {"stale_run_minutes": 0},
        {"max_concurrency": 0},
        {"max_concurrency": 5},
        {"claim_deadline_seconds": 0},
        {"claim_deadline_seconds": -1},
    ],
)
def test_orchestrator_rejects_unsafe_limits(options: dict) -> None:
    with pytest.raises(ValueError):
        CollectionOrchestrator(
            MagicMock(), CollectionAdapter(), ai_manager=_StubAIManager(), **options
        )


def test_orchestrator_accepts_valid_claim_deadline() -> None:
    orchestrator = CollectionOrchestrator(
        MagicMock(),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        claim_deadline_seconds=120.0,
    )
    assert orchestrator is not None
