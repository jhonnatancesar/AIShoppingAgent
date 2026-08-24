# Playwright: sempre `connect_over_cdp()`, nunca `.launch()`

A TASK-024 adicionou a infraestrutura de navegador original (Chromium
gerenciado, `BrowserSession`, via `chromium.launch()`). A TASK-109
(fechamento da migração de browser, 2026-08) trocou a arquitetura em
**toda** parte do projeto: hoje **nada chama `.launch()` de nenhum
tipo** -- nem em produção, nem na suíte de testes. Playwright só conecta
(`connect_over_cdp()`) a um Microsoft Edge já em execução; zero binário
de Chromium é baixado, instalado ou executado neste projeto, e zero
processo de navegador é lançado pela própria chamada Playwright que o
usa (lançar o processo é sempre responsabilidade de `EdgeCdpSupervisor`,
separado de quem conecta).

## Dois papéis, uma única arquitetura

- **Produção (`app.collection.providers`)**: todos os seis Store
  Providers (Amazon, Kabum, Magalu, Mercado Livre, Pichau, Terabyte)
  navegam exclusivamente via `Playwright.chromium.connect_over_cdp()`
  contra um Edge normal, real, supervisionado
  (`EdgeCdpSupervisor`/`EdgeCdpTransport`). Sem `cdp_transport`
  configurado, cada provider falha explícito (`EdgeCdpTransportError`,
  tratado pelo retry/circuit-breaker normal) -- nunca abre um navegador
  como fallback silencioso. Ver
  [Runtime Windows do collection_worker](windows-collection-worker.md).
- **Testes (`app.collection.browser.BrowserSession`)**: continua
  existindo, só como infraestrutura de teste hermética -- obter um `Page`
  real e local (`page.set_content(html)`, sem rede) para exercitar
  `.extract()`/parsing de HTML estático em `tests/test_store_providers.py`
  e `tests/test_playwright_browser.py`. Nenhum destes testes representa
  coleta real; nenhum navega para uma URL externa. Desde o fechamento da
  TASK-109, `BrowserSession` **conecta** via `EdgeCdpTransport.open_blank_page()`
  (a mesma classe de produção, reaproveitada sem duplicar lifecycle) a um
  Edge dedicado da suíte -- porta/perfil exclusivos
  (`EDGE_SESSION_CDP_URL`/`EDGE_SESSION_PROFILE_DIR`, definidos em
  `app.collection.browser`), nunca a mesma porta/perfil de um Edge real
  de dev/produção. `tests/conftest.py` sobe esse Edge uma vez por sessão
  de teste (`EdgeCdpSupervisor.ensure_started()`) e derruba no fim
  (`close_via_cdp()`) -- `BrowserSession` em si só conecta, nunca lança
  processo nenhum.

## Componentes (`app.collection.browser`, uso de teste)

- `BrowserSettings` define timeouts positivos e locale (`headless`
  também existe, mas não é lida por `BrowserSession` -- só por
  `PlaywrightStoreProvider`/scripts que ainda constroem o dataclass).
- `BrowserSession` conecta via CDP e devolve uma página -- nunca lança
  processo.
- A conexão CDP é encerrada mesmo quando a abertura falha ou o bloco
  assíncrono termina com erro; o processo do Edge em si é gerenciado
  pelo fixture de sessão (`tests/conftest.py`), não por `BrowserSession`.

## Instalação local

O pacote `playwright` continua em `backend/requirements.txt` (produção e
teste importam tipos de `playwright.async_api` e falam CDP através dele).
**Não existe `python -m playwright install chromium` em nenhum passo
deste projeto** -- nenhum binário de navegador é baixado. A única
dependência externa é o Microsoft Edge já instalado no sistema
operacional (Windows), necessário para: (1) o `collection_worker`
real e (2) rodar a suíte de testes local/CI completa.

```powershell
python -m pip install -r backend/requirements-dev.txt
```

Sem Edge instalado, o fixture de sessão (`tests/conftest.py`) falha ao
tentar subir o Edge dedicado da suíte -- mensagem clara
(`EdgeExecutableNotFoundError`/`EdgeCdpSupervisorError`), nunca um
download silencioso de outro navegador.

## Limites

A base de teste não contorna proteções, não normaliza resultados e não
persiste coletas -- só HTML estático local.

## Edge/CDP: transporte único de todos os Store Providers

Quando `AISHOPPING_EDGE_CDP_URL` está configurada (sempre, no
collection_worker nativo Windows), o `EdgeCdpSupervisor` mantém um Edge
normal dedicado, sob demanda (TASK-109: lease/idle-timeout, ver
[Runtime Windows](windows-collection-worker.md)). A URL aceita
exclusivamente HTTP loopback (`127.0.0.1`, `localhost` ou `::1`) com porta
explícita -- `0.0.0.0`, IP de rede, credenciais e porta pública falham no
startup. O nome antigo, `AISHOPPING_MAGALU_CDP_URL`, continua aceito por
compatibilidade.

Sem `AISHOPPING_EDGE_CDP_URL` configurada, cada provider falha rápido e
isolado (`EdgeCdpTransportError`) -- não existe mais nenhum fallback para
Chromium gerenciado ou Xvfb (o `collection_worker` não roda mais em
Docker/Linux; o guia Ubuntu/Xvfb ficou histórico, ver
`docs/tasks/TASK-109.md`).

Exemplo de validação manual local (Windows, com um Edge dedicado já
supervisionado ou iniciado à parte):

```powershell
python -m scripts.validate_store_providers magalu `
  --query "Samsung Galaxy S24 Ultra" `
  --edge-cdp-url http://127.0.0.1:9223
```

Não usar perfil pessoal, stealth, alteração de fingerprint, CAPTCHA
solver, proxy ou cópia de cookies.
