# Configuração (`.env`)

Esta página é a fonte única de verdade para as variáveis não sensíveis lidas
pelo `compose.yaml`. Secrets (senha do banco, chaves de IA, token do bot)
nunca vão aqui — veja [Secrets](secrets.md).

`.env` **não é versionado** (confirmado em `.gitignore`) e deve ser criado
diretamente em cada máquina/servidor a partir do exemplo:

```powershell
Copy-Item .env.example .env
```

Todas as variáveis abaixo foram conferidas diretamente em `compose.yaml` —
nenhuma inventada.

## Aplicação e ambiente

| Variável | Finalidade | Obrigatória | Recomendação de produção |
| --- | --- | --- | --- |
| `AISHOPPING_APP_NAME` | Nome da aplicação nos logs | Opcional | Manter o padrão (`AIShoppingAgent`) |
| `AISHOPPING_ENVIRONMENT` | Modo de execução | **Sim** | **`production`** — só nesse modo os secrets `*_FILE` são exigidos e valores diretos são rejeitados |
| `AISHOPPING_DEBUG` | Liga modo debug | Opcional | `false` |
| `AISHOPPING_LOG_LEVEL` | Nível de log | Opcional | `INFO`; `WARNING`/`ERROR` só se o volume de log for um problema |
| `AISHOPPING_AUTH_PUBLIC_BASE_URL` | Origem pública usada pelos links de senha/recuperação e de oferta enviados no Telegram | **Sim** | A URL HTTPS pública e estável do servidor (ver [Windows Server](windows-server.md), seção Telegram) |

## Rede e portas (host)

| Variável | Finalidade | Padrão | Recomendação |
| --- | --- | --- | --- |
| `API_BIND_ADDRESS` | Interface onde a API é publicada no host | `127.0.0.1` | Nunca `0.0.0.0` |
| `API_PORT` | Porta publicada da API | `8000` | Manter, salvo conflito |
| `POSTGRES_BIND_ADDRESS` | Interface do PostgreSQL no host | `127.0.0.1` | Nunca expor publicamente |
| `POSTGRES_PORT` | Porta publicada do PostgreSQL | `5432` | Manter, salvo conflito |
| `PROMETHEUS_BIND_ADDRESS` | Interface do Prometheus | `127.0.0.1` | Manter |
| `PROMETHEUS_PORT` | Porta do Prometheus | `9090` | Manter |
| `JAEGER_BIND_ADDRESS` | Interface do Jaeger | `127.0.0.1` | Manter |
| `JAEGER_UI_PORT` | Porta da UI do Jaeger | `16686` | Manter |
| `OTEL_HEALTH_PORT` | Porta de health do OpenTelemetry Collector | `13133` | Manter |

## PostgreSQL (não sensível)

| Variável | Finalidade | Padrão |
| --- | --- | --- |
| `POSTGRES_DB` | Nome do banco | `aishoppingagent` |
| `POSTGRES_USER` | Usuário do papel PostgreSQL | `aishoppingagent` |
| `AISHOPPING_SECRETS_DIR` | Caminho (não secreto) de onde o Compose lê os arquivos de secret | `./.secrets` |

A senha (`POSTGRES_PASSWORD`) não vai no `.env` — vai em
`.secrets/postgres_password` ([Secrets](secrets.md)).

## Observabilidade

| Variável | Finalidade | Padrão |
| --- | --- | --- |
| `AISHOPPING_OBSERVABILITY_ENABLED` | Liga métricas Prometheus/traces OTLP | `true` |
| `AISHOPPING_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Endpoint interno do Collector | `http://otel-collector:4318/v1/traces` (nome do serviço no Compose — não altere) |
| `AISHOPPING_TRACE_SAMPLE_RATIO` | Proporção de traces amostrados (0–1) | `1.0` |
| `AISHOPPING_READINESS_TIMEOUT_SECONDS` | Timeout da consulta de `/ready` ao banco | `1.0` |
| `AISHOPPING_WORKER_METRICS_PORT` | Porta interna de métricas dos workers | `9464` (não publicada no host) |

## Limites e resiliência

| Variável | Finalidade | Padrão |
| --- | --- | --- |
| `AISHOPPING_MAX_REQUEST_BODY_BYTES` | Limite de corpo HTTP aceito | `65536` |
| `AISHOPPING_TELEGRAM_RATE_LIMIT_PER_MINUTE` | Limite de mensagens processadas por minuto | `20` |
| `AISHOPPING_EXTERNAL_HTTP_TIMEOUT_SECONDS` | Timeout de chamadas HTTP/API externas (IA, Telegram) | `10` |
| `AISHOPPING_BROWSER_NAVIGATION_TIMEOUT_SECONDS` | Timeout de navegação do Playwright — só lido pelo `collection_worker` | `45` |
| `AISHOPPING_SAFE_RETRY_MAX_ATTEMPTS` | Tentativas máximas em operações seguras | `3` |
| `AISHOPPING_RETRY_BASE_DELAY_SECONDS` | Atraso inicial de retry | `0.25` |
| `AISHOPPING_RETRY_MAX_DELAY_SECONDS` | Atraso máximo de retry | `5` |
| `AISHOPPING_RETRY_AFTER_CAP_SECONDS` | Teto para `Retry-After` de terceiros | `30` |
| `AISHOPPING_CIRCUIT_FAILURE_THRESHOLD` | Falhas até abrir o circuit breaker | `5` |
| `AISHOPPING_CIRCUIT_OPEN_SECONDS` | Tempo de circuito aberto | `30` |
| `AISHOPPING_EVENT_CONSUMER_MAX_ATTEMPTS` | Tentativas do consumidor de eventos | `5` |
| `AISHOPPING_EVENT_RETRY_BASE_SECONDS` | Atraso inicial do consumidor de eventos | `60` |
| `AISHOPPING_EVENT_RETRY_CAP_SECONDS` | Teto de atraso do consumidor de eventos | `900` |
| `AISHOPPING_WORKER_FAILURE_BACKOFF_SECONDS` | Atraso após falha de worker | `5` |

## Coleta (`collection_worker`)

Lidas de verdade por `api` e `collection_worker`; não aparecem em
`.env.example` — adicione ao `.env` só se quiser mudar o padrão:

| Variável | Finalidade | Default efetivo |
| --- | --- | --- |
| `AISHOPPING_COLLECTION_POLL_SECONDS` | Intervalo de polling do worker | `15` |
| `AISHOPPING_COLLECTION_BATCH_SIZE` | Missões por lote | `25` |
| `AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES` | Intervalo entre coletas da mesma missão | `30` |
| `AISHOPPING_COLLECTION_SCHEDULE_STAGGER_SECONDS` | Dispersão aleatória do primeiro agendamento | `300` |
| `AISHOPPING_COLLECTION_STALE_RUN_MINUTES` | Tempo até uma execução travada ser considerada obsoleta | `10` |
| `AISHOPPING_COLLECTION_MAX_CONCURRENCY` | Missões coletadas em paralelo (Chromium simultâneos) | `2` |

!!! warning "Intervalo e concorrência de coleta dependem da RAM do servidor"
    Mudar `AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES` sozinho **não
    migra agendas já persistidas** em `mission_schedules`. Veja o
    procedimento completo de upgrade de capacidade no
    [Runbook de operação](../operations/runbook.md) antes de alterar esses
    dois valores em produção.

## César Core (IA e Web Search)

Integração obrigatória com o gateway privado César Core (repositório próprio,
`cesar-core`) — passo a passo completo em
[Instalação → César Core](cesar-core.md); funcionalidade em
[Arquitetura → Integração com César Core](../architecture/cesar-core-integration.md).

| Variável | Finalidade | Padrão |
| --- | --- | --- |
| `AISHOPPING_CESAR_CORE_BASE_URL` | Endpoint HTTP loopback do Core | `http://127.0.0.1:8100` |
| `AISHOPPING_CESAR_CORE_SERVICE` | Identifica o chamador (`backend`/`collection_worker`) perante o Core | `backend` |
| `AISHOPPING_CESAR_CORE_SERVICE_CLASS` | Classe de serviço solicitada (`economy`/`standard`/`quality`) | `economy` |
| `AISHOPPING_CESAR_CORE_MAX_TOKENS` | Teto de tokens da geração de IA | `1024` |
| `AISHOPPING_CESAR_CORE_TIMEOUT_SECONDS` | Timeout da chamada de IA | `90` |
| `AISHOPPING_CESAR_CORE_SEARCH_TIMEOUT_SECONDS` | Timeout da chamada de Search | `30` |

A credencial (`AISHOPPING_CESAR_CORE_API_KEY_FILE`) é secret, não vai nesta
página — ver [Secrets](secrets.md). Quota efetiva (limite) não é configurada
aqui: mora no Control Plane administrativo do próprio César Core (ADR 0018
do repositório `cesar-core`).

## Chaves não usadas pelo código

O GG Oferta não lê chaves de providers AI concretos. Essas conexões pertencem
ao OmniRoute abaixo do César Core. Chaves antigas de Gemini, Groq, OpenRouter,
OpenAI, Anthropic, Grok ou equivalentes não devem ser configuradas no GG.
