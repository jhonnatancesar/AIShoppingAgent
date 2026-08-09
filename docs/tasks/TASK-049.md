# TASK-049 — Adicionar limites e resiliência

Status: Concluída

## Objetivo

Impedir abuso, replay e cascatas de falhas nas fronteiras atuais da V1, com
estado mínimo no PostgreSQL e sem introduzir infraestrutura distribuída.

## Escopo aprovado

- limitar corpos HTTP a 64 KiB antes do parsing;
- deduplicar `update_id` autenticado do Telegram de forma transacional;
- limitar cada usuário a 20 updates autenticados por minuto;
- persistir recibos terminais `accepted`, `rate_limited` ou `discarded`, nunca
  um estado `processing` sobrevivente;
- manter recibo e efeitos na mesma transação e tratar somente a corrida da
  unicidade esperada;
- aplicar timeouts a toda chamada externa;
- repetir somente operações classificadas como seguras/idempotentes;
- não repetir cegamente `sendMessage` ou outra operação de efeito ambíguo;
- limitar e validar `Retry-After`, sem dormir com transação/lock aberto;
- limitar a cascata de IA globalmente, incluindo fallback;
- circuit breaker local por processo e por operação/dependência: Telegram,
  cada provider/modelo de IA e cada Store Provider;
- backoff de consumo persistido como `failed + next_retry_at`;
- encerrar em `dead_lettered` erros permanentes ou tentativas esgotadas;
- derivar elegibilidade exclusivamente do histórico append-only;
- impedir dois terminais concorrentes por consumidor/evento;
- manter o worker vivo com rollback e backoff após falha inesperada;
- emitir métricas de resiliência com labels de catálogo fechado.

## Semântica obrigatória

- replay concluído converge para `204` sem cota, aviso, IA ou domínio;
- rate limit persiste terminalmente, responde `204` e não executa domínio/IA;
- falha inesperada desfaz recibo e efeitos, permitindo reentrega;
- timeout ambíguo de operação não idempotente não gera retry imediato;
- `succeeded`, `skipped` e `dead_lettered` são terminais;
- `failed` só volta ao claim após `next_retry_at` e abaixo do limite;
- nenhuma espera de backoff mantém transação PostgreSQL aberta;
- `events` permanece imutável; toda evolução ocorre em tentativas append-only.

## Fora de escopo

Redis, Celery, Kafka, RabbitMQ, WAF, Kubernetes, autoscaling, alta
disponibilidade, Alertmanager, scheduler de coleta, backup/disaster recovery,
privacidade geral, runbook completo e suítes permanentes das TASKs 050–053.

## Validação obrigatória

Testes automatizados e validação real com PostgreSQL 18, Docker, API, worker,
Prometheus, Jaeger, Telegram e Store Providers. Cobrir migration reversível,
concorrência/replay, rate limit após restart, payload excessivo, timeouts,
falhas transitórias/permanentes, circuitos independentes, backoff sem lock,
dead letter terminal, privacidade da telemetria e pipeline completo.

## Resultado

- migration `20260808_0009` reversível com recibos append-only,
  `next_retry_at` e terminal `dead_lettered`;
- concorrência PostgreSQL real confirmou um único efeito por `update_id` e um
  único terminal por consumidor/evento;
- API/worker/Prometheus/Jaeger, Telegram Bot API e Store Providers foram
  validados no Docker Linux isolado, incluindo restart e falha/recuperação;
- timeout ambíguo de `sendMessage` não recebe retry cego; Store/AI providers
  mantêm circuitos independentes;
- pipeline completo aprovado em Python 3.14.6 com 590 testes e 91,38% de
  cobertura.

Próxima tarefa executável: TASK-050.
