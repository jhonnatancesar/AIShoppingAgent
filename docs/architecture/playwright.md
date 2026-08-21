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
