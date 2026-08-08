# TASK-045 — Adicionar observabilidade

Status: Concluída

## Objetivo

Adicionar observabilidade operacional real para API e worker, compatível com
Docker e Ubuntu Server headless, sem tornar a infraestrutura de observabilidade
uma dependência funcional da aplicação.

## Arquitetura aprovada

- traces: aplicação/worker → OTLP/HTTP → OpenTelemetry Collector → Jaeger;
- métricas: endpoints Prometheus da API e do worker → scrape direto pelo
  Prometheus;
- logs: JSON em `stdout`, correlacionados com o contexto ativo;
- métricas não são exportadas por OTLP, evitando duplicidade;
- Collector, Prometheus e Jaeger não condicionam a prontidão da API.

## Logs e correlação

- aceitar `X-Request-ID` somente após verificar limite pequeno de tamanho e UUID
  válido; caso contrário, gerar UUID interno;
- devolver o UUID efetivo na resposta;
- incluir `request_id`, `trace_id` e `span_id` nos logs correlacionados;
- `request_id` é apenas correlação observacional, nunca identidade,
  autenticação, autorização, chave de domínio ou idempotência;
- nunca registrar token, header sensível, payload/texto do usuário, query string
  completa, credencial ou dado de autenticação.

## Métricas

- API: total, latência, in-flight, status e falhas;
- worker: disponibilidade, lotes, duração e resultados agregados;
- `/metrics` da API fica fora do OpenAPI;
- worker expõe `/metrics` apenas na rede interna do Compose;
- labels limitadas a conjuntos fechados: método, rota normalizada, status,
  worker e outcome;
- `event_type` só pode virar label por allowlist explícita dos eventos atualmente
  suportados; desconhecidos usam `other` ou não são rotulados;
- IDs, URL, consulta/produto livre e exception message nunca são labels.

## Tracing

- instrumentar FastAPI, PostgreSQL/SQLAlchemy e lotes relevantes do worker;
- excluir explicitamente `/metrics` e `/health` do tracing automático;
- manter `/ready` instrumentado;
- PostgreSQL expõe somente `db.system` e operação estrutural; statement só seria
  admissível com placeholders e sem valores, mas a implementação deve omiti-lo
  se não puder garantir isso;
- nunca exportar binds, literais do usuário, credenciais, DSN, resultados ou
  dados-canário;
- falha de exportação não bloqueia trabalho funcional.

## Saúde

- `/health`: liveness do processo, sem PostgreSQL;
- `/ready`: consulta mínima real ao PostgreSQL, timeout curto e `503` em falha;
- `/ready` não depende de Collector, Prometheus ou Jaeger;
- componentes de observabilidade possuem healthchecks independentes.

## Regras Prometheus

Detectar indisponibilidade de API/worker, taxa de erros HTTP, latência e falhas
do worker. As regras são apenas carregadas, avaliadas e exibidas como
`inactive`, `pending` ou `firing`.

Não incluir Alertmanager, receiver ou envio externo por e-mail, Telegram, Slack
ou qualquer canal. Esta TASK entrega detecção/estado, não notificação externa.

## Fora do escopo

- Grafana, Alertmanager e notificação externa;
- autenticação, autorização, rate limiting, retry ou circuit breaker;
- revisão de privacidade, runbook operacional completo e suíte E2E permanente;
- migrations ou novas tabelas.

## Validação obrigatória

- PostgreSQL, API, worker, Collector, Prometheus e Jaeger reais no Compose;
- `/health=200` com PostgreSQL indisponível; `/ready=503` e recuperação para
  `200` após restaurar o banco;
- scrape e tráfego real nas métricas;
- trace real API → PostgreSQL no Jaeger;
- correlação de IDs nos logs;
- dados-canário ausentes de métricas, traces, logs e caminho PostgreSQL;
- `/metrics` e `/health` sem traces; fluxo funcional e `/ready` com traces;
- ao menos uma regra Prometheus em `firing`, seguida de recuperação;
- pipeline completo, revisão, documentação e workflow Git oficial.

## Resultado

- API e worker expõem métricas Prometheus sem OTLP; traces seguem por
  OTLP/HTTP para Collector e Jaeger.
- Logs JSON correlacionam apenas o UUID efetivo e o contexto de trace; o access
  log bruto do Uvicorn fica desativado para não registrar query strings.
- Tracing HTTP usa rota normalizada, exclui `/metrics` e `/health` e mantém
  `/ready`; spans SQL omitem integralmente statement, binds, DSN e resultados.
- Compose inclui imagens versionadas de Collector, Prometheus e Jaeger, regras
  de alerta sem Alertmanager e healthchecks independentes quando suportados
  pela imagem.
- Validação real confirmou PostgreSQL indisponível (`health=200`,
  `ready=503`), recuperação, scrape dos três targets, trace API → PostgreSQL,
  canários ausentes de logs/traces, exclusões de tracing e alerta do worker em
  `firing` seguido de `inactive`.
- Pipeline final: 477 testes, 92,23% de cobertura, Ruff e Compose aprovados.
