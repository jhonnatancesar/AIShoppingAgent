# Playwright base

A TASK-024 adicionou a infraestrutura de navegador usada pelos Store Providers
implementados na TASK-055. A base fica em `app.collection.browser`; seletores,
URLs e regras específicas permanecem isolados em `app.collection.providers`.

## Componentes

- `BrowserSettings` define execução headless, timeouts positivos e locale.
- `BrowserSession` inicia Playwright e Chromium de forma assíncrona.
- Cada sessão cria um contexto isolado, bloqueia downloads por padrão e oferece
  páginas somente enquanto o contexto estiver ativo.
- Contexto, navegador e processo Playwright são encerrados mesmo quando a abertura
  falha ou o bloco assíncrono termina com erro.

## Instalação local

Use exclusivamente o Python oficial da máquina:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m playwright install chromium
```

O segundo comando instala somente o navegador gerenciado pelo Playwright. A imagem
Docker executa `playwright install --with-deps chromium` durante o build para incluir
também as bibliotecas necessárias no Linux.

O smoke test determinístico pode ser executado com `scripts/playwright_smoke.py`,
mantendo `backend` no caminho de importação. Ele usa somente HTML local e não acessa
fontes externas.

## Limites

A base não contorna proteções, não normaliza resultados e não persiste coletas.
Pichau, Terabyte, Amazon e Kabum foram implementados na TASK-055. Normalização e
persistência permanecem nas TASKs 025 e 026.

## Ubuntu Server sem interface gráfica

A imagem inicia `Xvfb` no display virtual `:99` antes da API. Isso permite abrir
Chromium headed sem desktop ou monitor no host. O display pode ser alterado com
`AISHOPPING_XVFB_DISPLAY`.

Depois de construir a imagem, valide cada origem dentro do Linux real:

```bash
docker compose run --rm api python -m scripts.validate_store_providers amazon
docker compose run --rm api python -m scripts.validate_store_providers kabum
docker compose run --rm api python -m scripts.validate_store_providers pichau
docker compose run --rm api python -m scripts.validate_store_providers terabyte
```

Pichau e Terabyte usam headed por padrão no validador; Amazon e Kabum usam
headless. `--headed` e `--headless` permitem diagnóstico explícito. Bloqueio ou
mudança de markup produz erro e não autoriza stealth, CAPTCHA solver ou evasão.

## Edge/CDP: transporte compartilhado por Magalu, Mercado Livre e Terabyte

A TASK-104A não acopla o provider ao Edge. `MagaluProvider` recebe a porta
`MagaluSearchTransport`, que apenas devolve o HTML da busca; o parser SSR,
normalização, ranking, Web e Telegram não conhecem o transporte.

Quando `AISHOPPING_EDGE_CDP_URL` é configurada, o worker inicia e mantém um
Edge normal dedicado por supervisor, aguarda o `#__NEXT_DATA__` final e se
desconecta sem encerrar o navegador. A URL aceita exclusivamente HTTP loopback
(`127.0.0.1`, `localhost` ou `::1`) com porta explícita. `0.0.0.0`, IP de rede,
credenciais e porta pública falham no startup. O worker e o Edge precisam rodar
no mesmo host/network namespace; Docker não recebe socket ou privilégio novo.
O nome antigo, `AISHOPPING_MAGALU_CDP_URL`, continua funcionando por
compatibilidade.

O mesmo Edge supervisionado, uma única variável, é reaproveitado por três
providers, cada um com seu próprio papel (TASK-104B/TASK-105): transporte
primário e único da Magalu e da Terabyte (nenhuma delas tenta Playwright
depois -- a Terabyte porque `DEC-070` comprovou bloqueio Cloudflare
persistente do Chromium gerenciado), e fallback de último recurso do Mercado
Livre, só depois do Playwright primário falhar.

Exemplo de validação local; o supervisor inicia o Edge com endereço loopback,
porta escolhida e perfil dedicado:

```powershell
python -m scripts.validate_store_providers magalu `
  --query "Samsung Galaxy S24 Ultra" `
  --edge-cdp-url http://127.0.0.1:9223
```

Nesta versão não existe fallback HTTP ou Playwright para a busca Magalu nem
para a Terabyte: sem CDP, com Edge indisponível ou após timeout, cada uma
dessas origens falha rápido e isolada. Conexão, navegação, documento e
leitura do HTML têm limites independentes. Um futuro serviço Windows/
supervisor troca somente o adapter de transporte; não refaz regras de
negócio. Não usar perfil pessoal, stealth, alteração de fingerprint, CAPTCHA
solver, proxy ou cópia de cookies.
