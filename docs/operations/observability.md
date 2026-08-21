# Observabilidade

## Fluxos

- API e `telegram_notifier` expõem métricas para scrape direto do Prometheus.
- API e worker enviam somente traces por OTLP/HTTP ao OpenTelemetry Collector;
  o Collector os encaminha ao Jaeger.
- Logs permanecem como JSON em `stdout`, com correlação pelo contexto ativo.
- Collector, Prometheus e Jaeger não participam da prontidão funcional da API.

## Endpoints

| Endpoint | Semântica |
| --- | --- |
| `/health` | Liveness do processo; não consulta PostgreSQL e não gera trace. |
| `/ready` | Executa `SELECT 1` no PostgreSQL com timeout curto; retorna 503 em falha e permanece rastreável. |
| `/metrics` | Métricas Prometheus; fora do OpenAPI e do tracing. |

No Compose, Prometheus fica em `127.0.0.1:9090`, Jaeger em
`127.0.0.1:16686` e o health endpoint do Collector fica em
`127.0.0.1:13133`. As métricas do worker permanecem somente na rede interna.
Essas ferramentas não devem ser expostas diretamente à Internet; acesso remoto
usa túnel SSH ou canal administrativo equivalente conforme
`docs/operations/linux-runbook.md`. As imagens são
versionadas em `compose.yaml` e compatíveis com execução headless no Ubuntu
Server.

Prometheus preserva no máximo 15 dias ou 2 GB no volume, prevalecendo o limite
atingido primeiro. Jaeger usa memória volátil limitada a 10.000 traces e o
container possui teto de 512 MB; não existe volume de traces. São defaults
operacionais da V1, não prazos jurídicos definitivos de retenção.

## Privacidade e cardinalidade

Métricas aceitam apenas método fechado, rota normalizada, classe de status,
worker, outcome e os catálogos fechados `component`/`event` de resiliência.
`event_type`, caso usado, passa pela allowlist atual;
valores desconhecidos viram `other`. IDs, URL, texto livre e mensagens de
exceção nunca viram labels.

A allowlist fechada atual contém os dois eventos de preço e os três eventos de
autenticação da V1. A inclusão desses nomes não adiciona IDs de usuário/sessão
às métricas; tipos futuros continuam agrupados em `other` até revisão explícita.

Traces HTTP omitem URL, query, headers e payload. Spans PostgreSQL expõem
somente sistema e operação fechada; statement, bind parameters, literais, DSN,
credenciais e resultados não são exportados. Logs não usam o access log bruto
do Uvicorn e nunca devem incluir token, header sensível, payload ou query
string completa.

Mensagens e tracebacks brutos de exceção não entram nos logs. Apenas a classe
segura e códigos internos fechados são permitidos. A política consolidada está
em `docs/architecture/privacy.md`.

## Regras Prometheus

`observability/alert-rules.yml` detecta indisponibilidade da API/worker, taxa
de erros HTTP, latência, falhas do worker, dead letters e circuitos abertos.
Sem Alertmanager ou receiver, essas
regras somente ficam `inactive`, `pending` ou `firing`; elas não enviam e-mail,
Telegram, Slack ou qualquer notificação externa.

## Configuração

- `AISHOPPING_OBSERVABILITY_ENABLED`;
- `AISHOPPING_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`;
- `AISHOPPING_TRACE_SAMPLE_RATIO`;
- `AISHOPPING_READINESS_TIMEOUT_SECONDS`;
- `AISHOPPING_WORKER_METRICS_PORT`.
- `AISHOPPING_COLLECTION_POLL_SECONDS` e
  `AISHOPPING_COLLECTION_BATCH_SIZE` para o ciclo do coletor;
- `AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES`,
  `AISHOPPING_COLLECTION_STALE_RUN_MINUTES` e
  `AISHOPPING_COLLECTION_MAX_CONCURRENCY` para agenda, recuperação e limite
  fechado de quatro fontes.

Em execução local fora do Compose, a observabilidade fica desativada por
padrão. O Compose a habilita e fornece os endereços internos.
