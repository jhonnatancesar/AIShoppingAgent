"""Resolução de identidade de produto contra lojas reais (TASK-083).

Infraestrutura interna do `collection_worker` -- resolve um `model` cru
(ex.: "9800X3D") contra Kabum e, se inconclusivo, Amazon, usando instâncias
de provider DEDICADAS (nunca as da coleta normal de missões): volume
pequeno, sem fallback de disponibilidade, timeout curto, namespace de
circuit breaker separado. Nunca persiste nada, nunca depende de
`Session`/`AsyncSession`, nunca chama IA -- só reaproveita `provider.collect`
(TASK-055) e o matcher determinístico compartilhado (TASK-075/083).

Nunca importado por `app.intent` -- o `IntentInterpreter` não conhece
Playwright; a integração com o pipeline de coleta é responsabilidade da
TASK-083 SUBETAPA 4, ainda não implementada.
"""

import asyncio
import logging
from collections.abc import Sequence
from uuid import uuid4

from app.collection.browser import BrowserSettings
from app.collection.contracts import (
    CollectionProvider,
    CollectionRequest,
    ResolvedProductIdentity,
)
from app.collection.model_matching import model_search_pattern, title_matches_model
from app.collection.providers.edge_cdp_supervisor import EdgeCdpSupervisor
from app.collection.providers.edge_cdp_transport import EdgeCdpTransport
from app.collection.providers.stores import AmazonProvider, KabumProvider
from app.core.resilience import RetryPolicy
from app.database.time import utc_now

logger = logging.getLogger("app.collection.identity_resolution")

# TASK-083: orçamento pequeno e defensivo -- esta resolução roda no
# collection_worker, nunca no caminho síncrono do webhook, mas ainda assim
# não pode travar indefinidamente nem competir por tempo com a coleta
# normal. `max_attempts=1` na RetryPolicy é deliberado: o provider já
# teria retry interno (TASK-079) para falha transiente de navegação, mas
# aqui preferimos avançar pro próximo provider a esperar um retry -- a
# própria cadeia Kabum->Amazon já cumpre esse papel.
_MAX_CANDIDATES = 5
_NAVIGATION_TIMEOUT_MS = 6_000
_ACTION_TIMEOUT_MS = 5_000
_PER_PROVIDER_TIMEOUT_SECONDS = 8.0
_CIRCUIT_NAMESPACE = "identity"
# TASK-109: timeout de conexão CDP dedicado -- Edge já supervisionado e
# em loopback, não precisa do mesmo orçamento de `_NAVIGATION_TIMEOUT_MS`.
_CDP_CONNECT_TIMEOUT_MS = 3_000


def _build_default_providers(
    edge_cdp_url: str | None = None,
    edge_supervisor: EdgeCdpSupervisor | None = None,
) -> tuple[CollectionProvider, ...]:
    """Kabum (primário) -> Amazon (secundário), nunca Pichau/Terabyte
    (headed/Xvfb, custo de execução maior que o orçamento desta
    resolução comporta -- ver auditoria da TASK-083).

    TASK-109: quando `edge_cdp_url` é fornecido, os dois ganham seu
    próprio `EdgeCdpTransport` -- mesmo Edge/CDP compartilhado da coleta
    normal (reutilizar a mesma instância supervisionada é seguro, CDP
    aceita múltiplas conexões simultâneas), mas com timeout/instância
    isolados desta resolução, nunca a `EdgeCdpTransport` da coleta
    normal em si. `edge_supervisor` (o mesmo objeto do worker) é só pra
    que cada resolução pegue sua própria lease do lifecycle sob demanda
    -- não muda orçamento/isolamento nenhum. Sem `edge_cdp_url`, o
    comportamento é exatamente o mesmo de antes (Playwright gerenciado)."""
    settings = BrowserSettings(
        navigation_timeout_ms=_NAVIGATION_TIMEOUT_MS,
        action_timeout_ms=_ACTION_TIMEOUT_MS,
    )
    retry_policy = RetryPolicy(max_attempts=1)
    kwargs: dict[str, object] = {
        "max_offers": _MAX_CANDIDATES,
        "retry_policy": retry_policy,
        "availability_fallback_max_candidates": 0,
        "circuit_namespace": _CIRCUIT_NAMESPACE,
    }
    if edge_cdp_url is not None:
        kwargs["cdp_transport"] = EdgeCdpTransport(
            edge_cdp_url,
            connect_timeout_ms=_CDP_CONNECT_TIMEOUT_MS,
            navigation_timeout_ms=_NAVIGATION_TIMEOUT_MS,
            document_timeout_ms=_ACTION_TIMEOUT_MS,
            supervisor=edge_supervisor,
        )
    return (
        KabumProvider(settings, **kwargs),
        AmazonProvider(settings, **kwargs),
    )


class StoreProductIdentityResolver:
    """Implementação concreta de `ProductIdentityResolver` (TASK-083).

    Kabum -> Amazon, nessa ordem, parando no primeiro candidato cuja
    correspondência com `model` for forte (`title_matches_model`, TASK-075).
    Erro, timeout, ausência de resultado ou nenhum candidato conclusivo em
    um provider avança para o próximo -- nunca propaga exceção para quem
    chamou; o pior caso é devolver `None`.
    """

    def __init__(
        self,
        providers: Sequence[CollectionProvider] | None = None,
        *,
        per_provider_timeout_seconds: float = _PER_PROVIDER_TIMEOUT_SECONDS,
        edge_cdp_url: str | None = None,
        edge_supervisor: EdgeCdpSupervisor | None = None,
    ) -> None:
        if per_provider_timeout_seconds <= 0:
            raise ValueError("per_provider_timeout_seconds must be positive")
        # TASK-109: `edge_cdp_url`/`edge_supervisor` só se aplicam aos
        # providers padrão -- quem passa `providers` explicitamente (ex.:
        # testes) já controla o transporte de cada um por conta própria.
        self._providers: tuple[CollectionProvider, ...] = (
            tuple(providers)
            if providers is not None
            else _build_default_providers(edge_cdp_url, edge_supervisor)
        )
        self._per_provider_timeout_seconds = per_provider_timeout_seconds
        # TASK-083: orçamento global = soma dos orçamentos individuais --
        # único mecanismo de deadline, sem camada de retry nova por cima
        # do que o provider/a cadeia de fallback já fazem.
        self._total_budget_seconds = per_provider_timeout_seconds * max(
            len(self._providers), 1
        )

    async def resolve(self, model: str) -> ResolvedProductIdentity | None:
        try:
            return await asyncio.wait_for(
                self._resolve(model), timeout=self._total_budget_seconds
            )
        except TimeoutError:
            logger.warning(
                "identity_resolution_budget_exceeded",
                extra={"budget_seconds": self._total_budget_seconds},
            )
            return None

    async def _resolve(self, model: str) -> ResolvedProductIdentity | None:
        for provider in self._providers:
            identity = await self._try_provider(provider, model)
            if identity is not None:
                return identity
        return None

    async def _try_provider(
        self, provider: CollectionProvider, model: str
    ) -> ResolvedProductIdentity | None:
        request = CollectionRequest(
            mission_id=uuid4(),
            source_code=provider.source_code,
            search_query=model,
            requested_at=utc_now(),
        )
        try:
            result = await asyncio.wait_for(
                provider.collect(request),
                timeout=self._per_provider_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                "identity_resolution_provider_timeout",
                extra={"source_code": provider.source_code},
            )
            return None
        except Exception:
            # TASK-083: mesmo boundary de `CollectionOrchestrator._process_claim`
            # -- erro de um provider nunca pode derrubar a resolução (nem,
            # por extensão, a coleta normal que a chama). Logado para não
            # esconder bug de programação real, mas sempre tratado como
            # "este provider não resolveu".
            logger.warning(
                "identity_resolution_provider_failed",
                extra={"source_code": provider.source_code},
                exc_info=True,
            )
            return None

        for offer in result.offers:
            if title_matches_model(model, offer.title):
                return ResolvedProductIdentity(
                    model=model,
                    search_query=_truncate_at_model(offer.title, model),
                    source=provider.source_code,
                )
        return None


def _truncate_at_model(title: str, model: str) -> str:
    """TASK-083: reduz um título comercial completo até o fim do código do
    modelo encontrado -- descarta specs/número de peça depois dele, mas
    nunca adiciona nada que não estivesse no título original (nunca chama
    IA para "embelezar"). Sem posição de match determinável -- não deveria
    acontecer, já que só chega aqui depois de `title_matches_model`
    confirmar --, usa o título inteiro, só sem espaço nas pontas."""
    match = model_search_pattern(model).search(title.upper())
    if match is None:
        return title.strip()
    return title[: match.end()].strip()
