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

No Compose, Prometheus fica em `:9090`, Jaeger em `:16686` e o health endpoint
do Collector é publicado somente em `127.0.0.1:13133`. As imagens são
versionadas em `compose.yaml` e compatíveis com execução headless no Ubuntu
Server.

## Privacidade e cardinalidade

Métricas aceitam apenas método fechado, rota normalizada, classe de status,
worker e outcome fechado. `event_type`, caso usado, passa pela allowlist atual;
valores desconhecidos viram `other`. IDs, URL, texto livre e mensagens de
exceção nunca viram labels.

Traces HTTP omitem URL, query, headers e payload. Spans PostgreSQL expõem
somente sistema e operação fechada; statement, bind parameters, literais, DSN,
credenciais e resultados não são exportados. Logs não usam o access log bruto
do Uvicorn e nunca devem incluir token, header sensível, payload ou query
string completa.

## Regras Prometheus

`observability/alert-rules.yml` detecta indisponibilidade da API/worker, taxa
de erros HTTP, latência e falhas do worker. Sem Alertmanager ou receiver, essas
regras somente ficam `inactive`, `pending` ou `firing`; elas não enviam e-mail,
Telegram, Slack ou qualquer notificação externa.

## Configuração

- `AISHOPPING_OBSERVABILITY_ENABLED`;
- `AISHOPPING_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`;
- `AISHOPPING_TRACE_SAMPLE_RATIO`;
- `AISHOPPING_READINESS_TIMEOUT_SECONDS`;
- `AISHOPPING_WORKER_METRICS_PORT`.

Em execução local fora do Compose, a observabilidade fica desativada por
padrão. O Compose a habilita e fornece os endereços internos.
