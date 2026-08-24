"""Infraestrutura de navegador só para teste (TASK-109, fechamento).

Nenhum Store Provider real usa mais isto -- a coleta de produção navega
exclusivamente via `EdgeCdpTransport`/`connect_over_cdp()` contra o Edge
supervisionado (`app/collection/providers/edge_cdp_supervisor.py`). O que
resta aqui existe só para dar a testes locais um `Page` real, sem rede
(`page.set_content(html)`), para exercitar parsing de HTML.

TASK-109 (fechamento, parte 3): `BrowserSession` nunca chama
`chromium.launch()` -- mesma arquitetura de produção, só
`connect_over_cdp()` contra um Microsoft Edge real (`EdgeCdpTransport`,
reaproveitado sem duplicar lifecycle). O processo do Edge em si (start/
stop) é responsabilidade de `tests/conftest.py` (fixture de sessão da
suíte, usa o mesmo `EdgeCdpSupervisor` de produção, porta/perfil
dedicados e distintos de qualquer Edge real de dev/produção) -- nunca
desta classe, que só conecta. `EDGE_SESSION_CDP_URL`/
`EDGE_SESSION_PROFILE_DIR` abaixo são o contrato entre os dois lados.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Page

# TASK-109 (fechamento, parte 3): porta/perfil exclusivos da suíte de
# testes -- nunca a mesma porta/perfil de um Edge real de
# dev/produção (`AISHOPPING_EDGE_CDP_URL`, default 9223), para que rodar
# a suíte nunca dispute nem interfira com um `collection_worker` real
# rodando ao mesmo tempo na mesma máquina. `tests/conftest.py` importa
# as duas constantes para saber onde/como subir o Edge dedicado.
EDGE_SESSION_CDP_URL = "http://127.0.0.1:9333"
EDGE_SESSION_PROFILE_DIR = Path(tempfile.gettempdir()) / "aishoppingagent-test-edge-profile"


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    """Opções seguras e independentes de uma fonte específica.

    `headless` não é mais lida por `BrowserSession` (TASK-109, fechamento
    parte 3 -- conecta a um Edge já em execução, nunca lança processo),
    mas continua aqui: `worker.py`/`validate_store_providers.py`
    constroem `BrowserSettings` para os Store Providers reais (timeouts/
    locale), sem relação nenhuma com `BrowserSession`."""

    headless: bool = True
    action_timeout_ms: int = 10_000
    navigation_timeout_ms: int = 30_000
    locale: str = "pt-BR"

    def __post_init__(self) -> None:
        if self.action_timeout_ms <= 0:
            raise ValueError("action_timeout_ms must be positive")
        if self.navigation_timeout_ms <= 0:
            raise ValueError("navigation_timeout_ms must be positive")
        if not self.locale.strip():
            raise ValueError("locale must not be blank")


class BrowserSession:
    """Página de teste via CDP contra o Edge dedicado da suíte -- só
    conecta, nunca lança processo nenhum (ver docstring do módulo)."""

    def __init__(self, settings: BrowserSettings | None = None) -> None:
        self.settings = settings or BrowserSettings()
        self._transport_cm = None
        self._page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        if self._transport_cm is not None:
            raise RuntimeError("browser session is already open")

        # Import adiado (TASK-109, fechamento parte 3): `browser.py` é
        # importado por `app/collection/__init__.py` antes do pacote
        # `providers` terminar de carregar (`base.py` importa
        # `BrowserSettings` daqui) -- importar `EdgeCdpTransport` no topo
        # do módulo criaria import circular. Nunca executado até alguém
        # de fato abrir uma `BrowserSession`, quando todos os módulos já
        # terminaram de carregar.
        from app.collection.providers.edge_cdp_transport import EdgeCdpTransport

        transport = EdgeCdpTransport(
            EDGE_SESSION_CDP_URL,
            connect_timeout_ms=5_000,
            navigation_timeout_ms=self.settings.navigation_timeout_ms,
            document_timeout_ms=self.settings.action_timeout_ms,
        )
        self._transport_cm = transport.open_blank_page()
        try:
            self._page = await self._transport_cm.__aenter__()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def new_page(self) -> Page:
        """Devolve a página da sessão ativa -- uma só por sessão."""
        if self._page is None:
            raise RuntimeError("browser session is not open")
        return self._page

    async def close(self) -> None:
        """Encerra a conexão CDP, inclusive após inicialização parcial."""
        transport_cm, self._transport_cm = self._transport_cm, None
        self._page = None
        if transport_cm is not None:
            await transport_cm.__aexit__(None, None, None)
