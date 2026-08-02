# Playwright base

A TASK-024 adiciona a infraestrutura de navegador usada futuramente pelos Store
Providers. A implementação fica em `app.collection.browser` e não contém seletores,
URLs ou regras específicas de marketplace.

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

A base não acessa fontes reais, não contorna proteções, não normaliza resultados e
não persiste coletas. Pichau, Terabyte, Amazon e Kabum serão implementados e validados
individualmente na TASK-055. Normalização e persistência permanecem nas TASKs 025 e
026.
