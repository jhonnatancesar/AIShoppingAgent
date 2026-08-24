# Playwright: Edge/CDP em produção, Chromium só em teste

A TASK-024 adicionou a infraestrutura de navegador original (Chromium
gerenciado, `BrowserSession`). A TASK-109 (fechamento da migração de
browser, 2026-08) trocou o transporte de produção: hoje **nenhum Store
Provider abre Chromium gerenciado**. Todos os seis (Amazon, Kabum, Magalu,
Mercado Livre, Pichau, Terabyte) navegam exclusivamente via
`Playwright.chromium.connect_over_cdp()` contra um Microsoft Edge normal,
real, supervisionado -- nunca via `Playwright.chromium.launch()`.

## Dois papéis distintos para Playwright

- **Produção (`app.collection.providers`)**: só `connect_over_cdp()`. Sem
  `cdp_transport` configurado, cada provider falha explícito
  (`EdgeCdpTransportError`, tratado pelo retry/circuit-breaker normal) --
  nunca abre Chromium como fallback silencioso. Ver
  [Runtime Windows do collection_worker](windows-collection-worker.md)
  para a arquitetura completa do transporte.
- **Testes (`app.collection.browser.BrowserSession`)**: continua existindo,
  intocada, só como infraestrutura de teste hermética -- obter um `Page`
  real e local (`page.set_content(html)`, sem rede) para exercitar
  `.extract()`/parsing de HTML estático em `tests/test_store_providers.py`
  e `tests/test_playwright_browser.py`. Nenhum destes testes representa
  comportamento de coleta real; nenhum navega para uma URL externa.

## Componentes (`app.collection.browser`, uso de teste)

- `BrowserSettings` define execução headless, timeouts positivos e locale.
- `BrowserSession` inicia Playwright e Chromium de forma assíncrona.
- Contexto, navegador e processo Playwright são encerrados mesmo quando a
  abertura falha ou o bloco assíncrono termina com erro.

## Instalação local para rodar a suíte de testes

O pacote `playwright` continua em `backend/requirements.txt` (produção
importa tipos de `playwright.async_api`, mesmo sem nunca lançar um
browser). O binário do Chromium só é necessário para rodar a suíte de
testes local/CI -- **não faz parte da imagem Docker de produção** (o
`Dockerfile` não instala mais `playwright install chromium`; nenhum
serviço Docker restante -- `api`, `telegram_notifier`, `ops_controller` --
abre navegador):

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m playwright install chromium
```

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
