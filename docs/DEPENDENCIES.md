# Dependências de Desenvolvimento

Este documento é a referência de ambiente para qualquer nova máquina. Antes de iniciar uma TASK, o agente deve lê-lo, comparar os requisitos com a máquina atual e instalar somente o que estiver ausente ou incompatível. O usuário já concedeu autorização permanente para os downloads e instalações necessários à execução e à validação real do projeto, sem dispensar confirmações obrigatórias do sistema operacional nem os cuidados de segurança e isolamento definidos em `AGENTS.md`.

## Ferramentas locais

| Ferramenta | Requisito atual | Verificação |
| --- | --- | --- |
| Git | Git for Windows 2.55.0 ou compatível | `git --version` |
| Python | Versão estável mais recente, atualmente Python 3.14.6, com `pip` | `python --version` e `python -m pip --version` |
| Docker Desktop | Versão estável mais recente; Docker Engine 29.6.2 e Docker Compose 5.3.1 validados atualmente | `docker --version` e `docker compose version` |
| cloudflared | Somente para desenvolvimento: expõe o webhook local com HTTPS público durante a validação real do canal Telegram; 2026.7.3 validado atualmente | `cloudflared --version` |

PostgreSQL não exige instalação direta na máquina: o ambiente local usa a imagem oficial `postgres:18-alpine` por meio do Docker Compose.

## Política de versão do Python

O projeto acompanha a versão estável mais recente do Python, sem permanecer fixado em uma série menor antiga. Em cada nova máquina ou nova versão estável, a compatibilidade das dependências deve ser validada com `python -m pip check` e com os testes aplicáveis antes do uso. A versão informada na tabela é a mais recente efetivamente validada pelo projeto e deve ser atualizada após cada validação.

O Python usado deve ser a instalação oficial da máquina, identificada pelo caminho do executável e por assinatura digital válida da Python Software Foundation. Runtimes internos do Codex, plugins, caches ou outras ferramentas hospedeiras não pertencem ao ambiente do projeto e não podem receber dependências nem executar suas validações. Alertas do antivírus devem interromper a execução; objetos detectados não são restaurados nem adicionados a exceções sem investigação e autorização explícita.

## Dependências da aplicação

A fonte de verdade para dependências Python é `backend/requirements.txt`:

- `fastapi>=0.115,<1.0`
- `google-genai>=2.16,<3.0`
- `httpx>=0.28,<1.0`
- `playwright>=1.62,<2.0`
- `SQLAlchemy>=2.0,<2.1`
- `alembic>=1.18,<2.0`
- `psycopg[binary]>=3.3,<4.0`
- `pydantic-settings>=2.0,<3.0`
- `uvicorn[standard]>=0.30,<1.0`

`google-genai` 2.16.0 é o SDK oficial validado para o perfil USER. A chamada ao
Gemini exige `AISHOPPING_GEMINI_API_KEY`; o modelo padrão configurável é
`gemini-3.6-flash`. A chave fica apenas no ambiente ou `.env` ignorado pelo Git.

`httpx` 0.28.1 (TASK-059) é usado por `GroqProvider` para chamar a API
compatível com OpenAI do Groq (`/openai/v1/chat/completions`), fallback
opcional do perfil ADMIN/DEV entre o Gemini premium e o Gemini gratuito.
Exige `AISHOPPING_GROQ_API_KEY`; o modelo padrão configurável é
`AISHOPPING_GROQ_MODEL` (`llama-3.3-70b-versatile`). Sem a chave, o
`AdminDevAIProviderManager` mantém o comportamento de dois níveis (Gemini
premium → Gemini gratuito) já validado nas TASKs 029–031. A chave fica
apenas em `backend/.env`, nunca versionada.

O webhook do Telegram (TASK-034) não usa SDK — chama a Bot API diretamente com
`urllib` da biblioteca padrão. Exige `AISHOPPING_TELEGRAM_BOT_TOKEN` (criado via
`@BotFather` no Telegram, usado somente por
`backend/scripts/register_telegram_webhook.py`) e
`AISHOPPING_TELEGRAM_WEBHOOK_SECRET` (valor aleatório local, gerado com
`secrets.token_urlsafe`, usado para autenticar as requisições recebidas). Ambos
ficam apenas em `backend/.env`, nunca versionados.

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

- Copiar `.env.example` para `.env`, definir uma senha local para o PostgreSQL e nunca versionar esse arquivo.
- Copiar `backend/.env.example` para `backend/.env` quando for necessário configurar valores locais.
- Nunca versionar `.env`, `backend/.env`, credenciais, ambientes virtuais, cache ou imagens Docker.
- Atualizar este documento e o arquivo de requisitos correspondente quando uma TASK introduzir uma dependência nova ou alterar uma versão suportada.
