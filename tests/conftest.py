"""Edge dedicado à suíte de testes (TASK-109, fechamento parte 3).

`BrowserSession` (`app/collection/browser.py`) só conecta via
`connect_over_cdp()` -- nunca lança processo (nunca `chromium.launch()`).
Este fixture de sessão sobe/derruba o Edge que ela conecta, reaproveitando
o `EdgeCdpSupervisor` de produção sem duplicar lifecycle, numa porta/
perfil exclusivos da suíte (`EDGE_SESSION_CDP_URL`/
`EDGE_SESSION_PROFILE_DIR`, definidos em `app.collection.browser` --
nunca a mesma porta/perfil de um Edge real de dev/produção, então rodar
a suíte nunca disputa nem interfere com um `collection_worker` real
rodando na mesma máquina ao mesmo tempo).

`ensure_started()`/`close_via_cdp()` (não `lease()`/`stop()`) de
propósito: setup e teardown deste fixture rodam cada um no seu próprio
`asyncio.run()` -- loops de evento diferentes. `lease()` ligaria
monitor/timer (`asyncio.Task`, presos ao loop que os criou); `stop()`
tentaria esperar (`process.wait()`) o `asyncio.subprocess.Process`
lançado no loop de setup a partir do loop de teardown -- ambos quebram
com "attached to a different loop". `ensure_started()` nunca cria essas
tasks e `close_via_cdp()` fecha o Edge só via comando CDP
(`Browser.close`), sem tocar no handle do subprocesso -- seguro entre
dois `asyncio.run()` diferentes; o processo real termina mesmo assim, só
não é formalmente esperado pelo Python (aceitável em teste, nunca usado
pelo `collection_worker` real)."""

import asyncio

import pytest

from app.collection.browser import EDGE_SESSION_CDP_URL, EDGE_SESSION_PROFILE_DIR
from app.collection.providers.edge_cdp_supervisor import EdgeCdpSupervisor


@pytest.fixture(scope="session", autouse=True)
def _dedicated_test_edge():
    supervisor = EdgeCdpSupervisor(
        EDGE_SESSION_CDP_URL,
        profile_dir=EDGE_SESSION_PROFILE_DIR,
    )
    asyncio.run(supervisor.ensure_started())
    yield
    asyncio.run(supervisor.close_via_cdp())
