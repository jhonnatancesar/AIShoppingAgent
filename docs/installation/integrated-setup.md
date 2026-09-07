# Instalação integrada: GG Oferta + César Core/OmniRoute + Coupon Worker

Guia único para colocar os três componentes funcionando juntos. Cada um
tem seu próprio repositório, ciclo de vida e documentação detalhada —
este documento não os substitui, só amarra a ordem e os pontos de
integração entre eles. Nenhuma topologia nova foi inventada aqui: tudo
abaixo já está documentado/aprovado em cada repositório.

| Componente | Repositório | Papel |
|---|---|---|
| GG Oferta | `C:\AIShoppingAgent\AIShoppingAgent` (este repo) | Produto: API, webapp, worker de coleta, bot Telegram |
| César Core | `C:\cesar-core` | Gateway central de AI e Web Search (identidade, policy, quota, tracing) |
| OmniRoute + SearXNG | dentro do Compose do César Core | Upstream real de AI providers e Search |
| Coupon Worker | `C:\AIShoppingAgenteCupom` | Coleta autônoma de cupons (Amazon/Kabum/Magalu/Mercado Livre), grava no Postgres do GG Oferta |

**DEV vs PROD:** em DEV, GG Oferta e César Core rodam via Docker Compose
na mesma máquina; o Coupon Worker roda nativo no Windows (Playwright +
Edge real via CDP, sem Docker) independentemente do ambiente. Em PROD,
a topologia aprovada (`DEC-118` item 8, GG Oferta) é o **mesmo Windows
Server físico**, com **deployments independentes** em
`C:\App\AIShoppingAgent`, `C:\App\cesar-core` e `C:\App\omniroute`
(repositório, configuração e secrets próprios de cada um) — nunca uma
composição única dos três repositórios, nunca exposição pública do Core
ou do OmniRoute. Onde este guia não marcar "só DEV" ou "só PROD", o
passo vale para os dois.

## 1. Pré-requisitos

- Docker Desktop (Linux containers) + Compose v2 — GG Oferta e César
  Core.
- Python 3.12+ (baseline validada: 3.14.6) — execução nativa de
  qualquer um dos três componentes fora de container.
- Windows com Microsoft Edge instalado — **obrigatório** para o
  `collection_worker` do GG Oferta e para o Coupon Worker (ambos usam
  Edge real via CDP/Playwright; não rodam em Linux nem em container).
- Node.js — build do frontend do GG Oferta (`frontend/`).
- Acesso aos três repositórios Git e, para César Core, acesso de leitura
  ao package privado no GHCR (`ghcr.io/jhonnatancesar/cesar-core`).

## 2. Diretórios/repositórios

```
C:\AIShoppingAgent\AIShoppingAgent   (GG Oferta -- este repo)
C:\cesar-core                        (César Core, inclui OmniRoute/SearXNG/Redis no Compose)
C:\AIShoppingAgenteCupom             (Coupon Worker)
```

Clonar os três lado a lado (ou nos caminhos acima) simplifica os passos
seguintes, mas não é obrigatório — nenhum caminho é hardcoded entre eles.

## 3. PostgreSQL

Um único PostgreSQL real serve **dois** dos três componentes:

- **GG Oferta** é o dono do schema (migrations Alembic, ver passo 10).
- **Coupon Worker** pode gravar direto nas tabelas `coupons`/
  `stores` do GG Oferta via `psycopg` assíncrono
  (`COUPONS_POSTGRES_DSN`, opcional) — mesma instância, credencial
  própria e independente, nunca o mesmo arquivo de secret.

Suba o Postgres do GG Oferta primeiro (`docker compose up -d database`
neste repositório, ou uma instância nativa já em execução — ver
[docker.md](docker.md)/[windows-server.md](windows-server.md)).

## 4. Redis

Usado **somente pelo César Core**, para quota AI/Search compartilhada e
durável (`ADR 0018` no repositório `cesar-core`). GG Oferta e Coupon
Worker não usam Redis. Sobe junto do Compose do César Core (passo 5),
sem porta publicada no host.

## 5. César Core

No repositório `cesar-core`:

```sh
cp .env.example .env
# criar os arquivos locais de secret descritos na seção "Secrets e
# segurança" do README.md do cesar-core (nunca versionar)
docker compose pull   # ou: docker build -t cesar-core:local .  (dev local)
docker compose up -d
docker compose ps
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

Isso já sobe Redis, OmniRoute e SearXNG juntos (mesmo `compose.yaml`) —
não são serviços separados a iniciar à parte. Detalhe completo:
`README.md` do repositório `cesar-core`.

## 6. OmniRoute

Sobe como parte do Compose do César Core (passo 5), sem porta publicada
no host (rede interna `backend`). Depois do primeiro boot, **connections
e combos de AI precisam ser provisionados uma vez por ambiente** — ver
passos 13/14 e o runbook dedicado
`C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`.
Sem isso, `CESAR_CORE_AI_ENABLED=true` sozinho não dá acesso real a
Gemini/Groq/OpenRouter — só ao catálogo nativo gratuito.

## 7. SearXNG/Search

Também sobe junto do Compose do César Core, sem porta publicada no host.
O César Core já aponta para ele via a connection `searxng-search`
(`providerSpecificData.baseUrl=http://searxng:8080/search`), que já vem
configurada pela topologia oficial do Compose — não precisa de
provisionamento manual adicional como as connections de AI.

## 8. GG Oferta

No repositório GG Oferta (este):

```powershell
Copy-Item .env.example .env
python -m backend.scripts.manage_secrets init
python -m backend.scripts.manage_secrets check
docker compose up -d database
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
docker compose up --build
```

Detalhe completo (portas, logs, `collection_worker`/`telegram_notifier`):
[docker.md](docker.md). Configuração completa de variáveis:
[configuration.md](configuration.md). Ligação com o César Core (URL,
credencial, capabilities): [cesar-core.md](cesar-core.md).

## 9. Coupon Worker

Roda nativo no Windows, fora do Docker (Playwright + Edge real via CDP,
mesma exigência do `collection_worker` do GG Oferta). No repositório
`C:\AIShoppingAgenteCupom`:

```powershell
.\install.ps1                          # cria .venv, instala requirements, gera .env com AUTH_TOKEN aleatório
# editar .env: opcionalmente definir COUPONS_POSTGRES_DSN apontando
# para o MESMO Postgres do GG Oferta (passo 3), com credencial própria
python worker.py --once                # rodada única, valida a config antes de instalar o agendamento
.\manage_coupon_worker_task.ps1 -Action Install -TaskUser "DOMINIO\Usuario"
```

Sem `COUPONS_POSTGRES_DSN`, o worker grava só em SQLite local
(`data/worker.db`) e o GG Oferta não vê os cupons coletados — para o
consumo de cupons no GG Oferta funcionar de ponta a ponta, essa variável
precisa estar configurada. Detalhe completo: `README.md` do repositório
`AIShoppingAgent-cupom`.

## 10. Migrations

Só o **GG Oferta** tem migrations Alembic versionadas
(`backend/migrations/versions/`, head atual `20260906_0002`):

```powershell
python -m alembic -c backend/alembic.ini upgrade head
```

César Core não expõe migration de schema de aplicação da mesma forma
(Control Plane próprio, ver `README.md` do `cesar-core`). Coupon Worker
não tem migration própria — ele grava nas tabelas já existentes do GG
Oferta (`stores`, `coupons`) via `PostgresCouponStore`, então a migration
do GG Oferta precisa estar em dia **antes** de configurar
`COUPONS_POSTGRES_DSN`.

## 11. Secrets (nomes -- nunca valores neste documento)

| Componente | Secret | Onde vive |
|---|---|---|
| GG Oferta | credenciais de banco, Telegram bot token, chave Gemini/Groq/OpenRouter legadas (ver nota), etc. | `.secrets/` do GG Oferta, ver [secrets.md](secrets.md) |
| GG Oferta → César Core | `cesar-core-client-dev` (Bearer do consumidor) | `.secrets\cesar-core-client-dev` (GG Oferta) e correspondente em `.secrets/ggoferta-core-client-dev` (César Core) |
| César Core → OmniRoute | `ggoferta-ai`, `ggoferta-search`, `ggoferta-fetch` | `.secrets/` do César Core |
| César Core | `searxng` (segredo interno do SearXNG) | `.secrets/searxng` do César Core |
| OmniRoute | senha administrativa, 4 chaves de provider AI (`gemini_user_api_key`, `gemini_admin_dev_api_key`, `groq_admin_dev_api_key`, `openrouter_admin_dev_api_key`) | ver `omniroute-ai-provider-provisioning.md` no `cesar-core` |
| Coupon Worker | `AUTH_TOKEN` (endpoint de controle) | `.env` do Coupon Worker |

Nota: os providers AI legados (Gemini/Groq/OpenRouter) direto no GG
Oferta foram removidos na FASE E — hoje toda AI passa pelo César Core;
as chaves reais desses providers vivem exclusivamente nas connections do
OmniRoute (linha acima), nunca mais no GG Oferta.

## 12. `.env`

Cada componente tem o seu, nunca compartilhado:

- GG Oferta: `.env` (Compose) / `backend/.env` (execução nativa) --
  [configuration.md](configuration.md).
- César Core: `.env` na raiz do repositório -- seção "Configuração" do
  seu `README.md`.
- Coupon Worker: `.env` gerado por `install.ps1` -- `AUTH_TOKEN` e
  `COUPONS_POSTGRES_DSN` (opcional).

## 13. Provider connections (César Core / OmniRoute)

Provisionamento único por ambiente (não versionado, não automático):
4 connections reais de AI (Gemini USER, Gemini ADMIN/DEV, Groq ADMIN/DEV,
OpenRouter ADMIN/DEV — duas chaves Gemini distintas, uma por perfil).
Passo a passo determinístico, com nomes de secret e corpo de requisição
completos: `C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`
§§1–2.

## 14. Combos USER/ADMIN_DEV

Também parte do mesmo provisionamento único (§3 do runbook citado
acima): `user-cascade` (Gemini USER → `oc/mimo-v2.5-free`) e
`admin-dev-cascade` (Gemini ADMIN/DEV → Groq → OpenRouter →
`oc/mimo-v2.5-free`). O César Core resolve `ai_profile=user`/
`admin_dev` para o combo certo via `CESAR_CORE_AI_USER_MODEL`/
`CESAR_CORE_AI_ADMIN_DEV_MODEL` (passo 12, `.env` do César Core) —
ver runbook §4.

## 15. Flags

GG Oferta (todas em `backend/app/core/config.py`, default `False` —
comportamento equivalente ao anterior enquanto desligadas):

| Flag | Controla |
|---|---|
| `historical_bootstrap_enabled` | Bootstrap histórico de preços (F1) |
| `market_research_external_reference_enabled` | Referência externa de preço na avaliação de mercado (F3) |
| `coupons_enabled` | Cálculo/aplicação de cupom na coleta e na Web |

César Core: `CESAR_CORE_AI_ENABLED` / `CESAR_CORE_SEARCH_ENABLED`
(também default `False` — sem eles, `/v1/ai/generate`/`/v1/search`
respondem "não configurado" mesmo com tudo mais no ar).

## 16. Portas/URLs (DEV)

| Serviço | Endereço |
|---|---|
| GG Oferta API | `http://localhost:8000` |
| GG Oferta PostgreSQL | `localhost:5432` |
| GG Oferta Prometheus | `http://localhost:9090` |
| GG Oferta Jaeger | `http://localhost:16686` |
| César Core | `http://127.0.0.1:8100` (único do stack Core publicado no host) |
| César Core `/admin` (Control Plane) | `http://127.0.0.1:8100/admin` |
| Redis, OmniRoute, SearXNG | sem porta no host — só na rede interna do Compose do César Core |
| Coupon Worker (control server) | porta local definida em `config.json` (`AUTH_TOKEN` obrigatório) |

Em PROD, `windows-server.md` e o `README.md` do `cesar-core` (seção
"Execução Docker / Compose") são a referência — a exposição pública é
diferente e não coberta por este guia de integração.

## 17. Ordem para subir os processos

1. PostgreSQL do GG Oferta (passo 3).
2. Migrations do GG Oferta (passo 10) — antes de qualquer coisa gravar no schema.
3. César Core + Redis + OmniRoute + SearXNG, juntos via Compose (passo 5).
4. Provisionar connections + combos no OmniRoute, só na primeira vez por
   ambiente (passos 13/14) — pular se já provisionado antes.
5. GG Oferta (API + `collection_worker` + `telegram_notifier`, passo 8).
6. Coupon Worker (passo 9) — pode subir a qualquer momento depois do
   Postgres/migrations; não depende do César Core.

## 18. Health/readiness

| Serviço | Vivo | Pronto (dependências reais) |
|---|---|---|
| GG Oferta API | `GET /health` | `GET /ready` |
| César Core | `GET /health` | `GET /ready`, `GET /v1/capabilities` |
| Coupon Worker | -- | endpoint de controle próprio (`config.json`), autenticado por `AUTH_TOKEN` |

## 19. Como provar que cada integração está funcionando

- **GG Oferta ↔ PostgreSQL:** `GET /ready` do GG Oferta retorna `200`.
- **GG Oferta ↔ César Core (AI):** uma chamada real via
  `AIProviderManager` deve responder com `provider_gateway="omniroute"`
  e `provider` preenchido (não mais `null` desde a correção desta
  sessão) — ver [cesar-core.md](cesar-core.md) passo 5.
- **GG Oferta ↔ César Core (Search):** equivalente, via
  `WebSearchManager`, `provider="searxng-search"` (ou `duckduckgo-free`
  no fallback zero-config).
- **César Core ↔ OmniRoute ↔ providers reais:** `POST /api/combos/test`
  no OmniRoute (autenticado com sessão administrativa), com
  `{"comboName": "user-cascade"}` e `{"comboName": "admin-dev-cascade"}`
  — cada passo deve responder `status: "ok"`. Ver
  `omniroute-ai-provider-provisioning.md` §3.
- **Coupon Worker ↔ PostgreSQL do GG Oferta:** rodar
  `python worker.py --once` com `COUPONS_POSTGRES_DSN` configurado e
  confirmar novas linhas em `coupons` (GG Oferta) para as lojas
  monitoradas.
- **Consumo de cupom ponta a ponta:** com `coupons_enabled=true` no GG
  Oferta e cupons reais já coletados, uma oferta elegível deve mostrar
  preço com cupom aplicado na Web/Telegram (`app/coupons/pricing.py`).

## 20. Como parar/reiniciar com segurança

Ordem inversa da subida, preservando dados:

1. **Coupon Worker:** `.\manage_coupon_worker_task.ps1 -Action Stop`
   (não interrompe uma coleta em andamento no meio de uma página —
   deixe terminar quando possível).
2. **GG Oferta:** `docker compose down` (nunca `-v` — isso apaga o
   volume do Postgres). `collection_worker`/`telegram_notifier` param
   junto.
3. **César Core (+ Redis/OmniRoute/SearXNG):** `docker compose down`
   no repositório `cesar-core` (sem `-v` — preserva connections/combos
   já provisionados no volume do OmniRoute e o histórico de quota no
   Redis).

Para reiniciar, repita a ordem do passo 17 a partir de onde parou — não
é necessário reprovisionar connections/combos se o volume do OmniRoute
foi preservado (só refazer os passos 13/14 se o volume foi descartado
ou é um ambiente novo).
