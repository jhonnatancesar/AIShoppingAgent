# Dependências de Desenvolvimento

Este documento é a referência de ambiente para qualquer nova máquina. Antes de iniciar uma TASK, o agente deve lê-lo, comparar os requisitos com a máquina atual e instalar somente o que estiver ausente ou incompatível. O usuário já concedeu autorização permanente para os downloads e instalações necessários à execução e à validação real do projeto, sem dispensar confirmações obrigatórias do sistema operacional nem os cuidados de segurança e isolamento definidos em `AGENTS.md`.

## Ferramentas locais

| Ferramenta | Requisito atual | Verificação |
| --- | --- | --- |
| Git | Git for Windows 2.55.0 ou compatível | `git --version` |
| Python | Versão estável mais recente, atualmente Python 3.14.6, com `pip` | `python --version` e `python -m pip --version` |
| Docker Desktop | Versão estável mais recente; Docker Engine 29.6.2 e Docker Compose 5.3.1 validados atualmente | `docker --version` e `docker compose version` |
| cloudflared | Somente para desenvolvimento: expõe o webhook local com HTTPS público durante a validação real do canal Telegram; 2026.7.3 validado atualmente | `cloudflared --version` |
| Gitleaks | 8.29.1, instalado pelo script do projeto após SHA-256 fixado | `python scripts/install_gitleaks.py` |

PostgreSQL não exige instalação direta na máquina: o ambiente local usa a imagem oficial `postgres:18-alpine` por meio do Docker Compose.

A suíte permanente da TASK-052 usa PostgreSQL `18.4-alpine` fixado pelo digest
`sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15`.
A atualização desse patch/digest deve ser deliberada, revisada e validada; o
runner não aceita uma tag flutuante.

No Ubuntu Server, o procedimento operacional usa Docker Engine e o plugin
Compose instalados pelos canais oficiais. As portas administrativas ficam no
loopback e o runbook completo, incluindo backup/restauração manual, está em
`docs/OPERATIONS.md`.

Na instalação oficial por usuário do Docker Desktop no Windows, o CLI pode
ficar em
`%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`. Esse diretório precisa
estar no `PATH` do usuário; a máquina atual foi corrigida e validada assim na
TASK-046. Um terminal já aberto pode precisar ser reiniciado para herdar a
alteração.

## Política de versão do Python

O projeto acompanha a versão estável mais recente do Python, sem permanecer fixado em uma série menor antiga. Em cada nova máquina ou nova versão estável, a compatibilidade das dependências deve ser validada com `python -m pip check` e com os testes aplicáveis antes do uso. A versão informada na tabela é a mais recente efetivamente validada pelo projeto e deve ser atualizada após cada validação.

O Python usado deve ser a instalação oficial da máquina, identificada pelo caminho do executável e por assinatura digital válida da Python Software Foundation. Runtimes internos do Codex, plugins, caches ou outras ferramentas hospedeiras não pertencem ao ambiente do projeto e não podem receber dependências nem executar suas validações. Alertas do antivírus devem interromper a execução; objetos detectados não são restaurados nem adicionados a exceções sem investigação e autorização explícita.

## Dependências da aplicação

A fonte de verdade para dependências Python é `backend/requirements.txt`:

- `fastapi>=0.115,<1.0`
- `argon2-cffi>=25.1,<26.0`
- `google-genai>=2.16,<3.0`
- `httpx>=0.28,<1.0`
- `opentelemetry-api>=1.44,<2.0`
- `opentelemetry-exporter-otlp-proto-http>=1.44,<2.0`
- `opentelemetry-instrumentation-fastapi>=0.65b0,<1.0`
- `opentelemetry-sdk>=1.44,<2.0`
- `playwright>=1.62,<2.0`
- `prometheus-client>=0.23,<1.0`
- `SQLAlchemy>=2.0,<2.1`
- `alembic>=1.18,<2.0`
- `psycopg[binary]>=3.3,<4.0`
- `pydantic-settings>=2.0,<3.0`
- `uvicorn[standard]>=0.30,<1.0`

`google-genai` 2.16.0 é o SDK oficial validado para os perfis USER e
ADMIN/DEV. A chamada ao Gemini exige uma chave por perfil —
`AISHOPPING_GEMINI_API_KEY_USER` para o perfil USER e
`AISHOPPING_GEMINI_API_KEY_ADMIN_DEV` para a cascata ADMIN/DEV (TASK-059),
mantendo as cotas gratuitas completamente separadas entre usuários reais e
validação/uso administrativo. O modelo padrão configurável é
`gemini-3.6-flash`. Em desenvolvimento fora do Docker, as chaves podem ficar
no ambiente ou `.env` ignorado. No Compose e em produção, ficam somente em
arquivos montados por `/run/secrets`, conforme `docs/SECRETS.md`.

`httpx` 0.28.1 (TASK-059) é usado por `GroqProvider` para chamar a API
compatível com OpenAI do Groq (`/openai/v1/chat/completions`), fallback
opcional do perfil ADMIN/DEV entre o Gemini premium e o Gemini gratuito.
Exige `AISHOPPING_GROQ_API_KEY`; o modelo padrão configurável é
`AISHOPPING_GROQ_MODEL` (`llama-3.3-70b-versatile`). Sem a chave, o
`AdminDevAIProviderManager` mantém o comportamento de dois níveis (Gemini
premium → Gemini gratuito) já validado nas TASKs 029–031. A execução local
fora do Docker pode usar `backend/.env`; Compose/produção usam secret file.

O webhook do Telegram (TASK-034) não usa SDK — chama a Bot API diretamente com
`urllib` da biblioteca padrão. Exige `AISHOPPING_TELEGRAM_BOT_TOKEN` (criado via
`@BotFather` no Telegram, usado por `backend/scripts/register_telegram_webhook.py`
e por `backend/scripts/register_telegram_commands.py`, que registra os
  nove comandos no menu, incluindo `/senha`, `/entrar`, `/sair` e `/recuperar`
  (TASK-061)) e
`AISHOPPING_TELEGRAM_WEBHOOK_SECRET` (valor aleatório local, gerado com
`secrets.token_urlsafe`, usado para autenticar as requisições recebidas). A
TASK-046 compara esse segredo em tempo constante antes de confiar na identidade
privada da pessoa. Em Compose/produção, ambos são arquivos concedidos somente
aos serviços autorizados; `backend/.env` permanece apenas para desenvolvimento
Python fora do Docker.

`argon2-cffi` 25.1.0 implementa Argon2id para a TASK-061 e foi validado no
Python 3.14.6 oficial da máquina e na imagem Linux `python:3.14-slim`.
`AISHOPPING_AUTH_PUBLIC_BASE_URL` define a origem do formulário e precisa ser
HTTPS fora de localhost.

A TASK-036 reutiliza o mesmo token no processo `app.telegram.worker`. O
intervalo e o tamanho do lote são configuráveis por
`AISHOPPING_TELEGRAM_NOTIFICATION_POLL_SECONDS` (padrão 5) e
`AISHOPPING_TELEGRAM_NOTIFICATION_BATCH_SIZE` (padrão 50). No Docker Compose,
API, `telegram_notifier` e `collection_worker` não carregam `backend/.env`:
recebem somente os secret files necessários. O notifier recebe senha do banco
e token do bot; o coletor recebe somente a senha do banco, sem token Telegram,
chaves de IA ou segredo do webhook. Poll, lote, agenda, TTL de run e
concorrência da coleta usam as variáveis `AISHOPPING_COLLECTION_*` documentadas
no Compose.

A TASK-045 validou OpenTelemetry 1.44.0/instrumentation 0.65b0 e
`prometheus-client` 0.26.0. O Compose usa imagens oficiais explicitamente
versionadas: OpenTelemetry Collector Contrib 0.157.0, Prometheus 3.12.0 e
Jaeger 2.20.0. Métricas são coletadas por scrape direto; somente traces usam
OTLP/HTTP. Nenhuma credencial adicional é necessária no ambiente local.

A TASK-049 não adicionou dependências. Retry, jitter, parsing de `Retry-After`,
circuit breaker e limite ASGI usam a biblioteca padrão; persistência reutiliza
SQLAlchemy/PostgreSQL e Playwright/HTTP já declarados. Os limites operacionais
estão em `docs/RESILIENCE.md` e nas variáveis dos arquivos `.env.example`.

As versões validadas na TASK-011 foram SQLAlchemy 2.0.51, Alembic 1.18.5 e Psycopg 3.3.4. O extra binário do Psycopg evita exigir uma instalação separada de `libpq` e possui suporte validado a Python 3.14 e PostgreSQL 18.

Playwright 1.62.0 e Chromium 151.0.7922.34 foram validados na TASK-024. O pacote
Python e o navegador são instalações distintas; após instalar os requisitos, execute
`python -m playwright install chromium`. No Docker, o build instala Chromium e suas
dependências Linux automaticamente.

Para comparar e instalar em uma nova máquina, após autorização:

```powershell
python -m pip install -r backend/requirements.txt
python -m pip check
```

## Dependências de desenvolvimento

A fonte de verdade para ferramentas usadas somente no desenvolvimento é `backend/requirements-dev.txt`. Esse arquivo inclui as dependências da aplicação e acrescenta:

- `ruff>=0.16,<0.17`
- `pytest>=9.1,<10.0`
- `pytest-cov>=7.1,<8.0`

Gitleaks não é pacote Python. `scripts/install_gitleaks.py` fixa a versão
8.29.1, confere o SHA-256 oficial para Windows/Linux x64 e instala o binário
em `.tools/`, ignorado. O pipeline não usa `latest`.

Após autorização para instalação, validar lint e formatação a partir da raiz:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m playwright install chromium
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Após preparar as dependências, o comando recomendado para executar todas as verificações é `scripts\check.cmd`, conforme `docs/LOCAL_PIPELINE.md`.

## Configuração local

- Copiar `.env.example` para `.env`; o Compose usa esse arquivo somente para
  configuração não sensível.
- Executar `python -m backend.scripts.manage_secrets init` em instalação nova,
  ou `migrate` para copiar valores locais antigos sem imprimi-los; depois usar
  `check`.
- Copiar `backend/.env.example` para `backend/.env` somente quando for
  necessário executar Python local fora do Docker.
- Nunca versionar `.env`, `.secrets`, credenciais, chaves/certificados privados,
  ambientes virtuais, cache, ferramentas baixadas ou imagens Docker.
- Atualizar este documento e o arquivo de requisitos correspondente quando uma TASK introduzir uma dependência nova ou alterar uma versão suportada.
