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

## Chaves não usadas pelo código

Uma auditoria já encontrou, em ambientes locais antigos, chaves configuradas
para OpenAI, Anthropic e Grok. Nenhuma delas existe em `Settings`
(`backend/app/core/config.py`) e nenhum código as lê — não fazem parte da
cascata do `AIProviderManager`, que usa Gemini, Groq e OpenRouter. Não
configure essas três chaves em produção.
