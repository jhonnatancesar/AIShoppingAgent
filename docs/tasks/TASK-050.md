# TASK-050 — Revisar privacidade

Status: Concluída

## Objetivo

Revisar o tratamento técnico de dados da V1, impedir vazamentos de PII,
limitar retenção operacional e permitir desidentificação segura sem quebrar
históricos imutáveis.

## Escopo aprovado

- inventariar dados, finalidades, armazenamento e terceiros em
  `docs/PRIVACY.md`;
- criar `/privacidade` como resposta fixa, sem IA e disponível antes da sessão;
- remover Telegram IDs e campos pessoais de logs;
- registrar somente classe segura de exceção, nunca mensagem/traceback bruto;
- limitar logs Docker a 5 arquivos de 10 MB por container;
- limitar Prometheus a 15 dias ou 2 GB, o que ocorrer primeiro;
- usar Jaeger volátil com no máximo 10.000 traces e 512 MB;
- tornar action tokens elegíveis à limpeza após 24h e sessões após 30 dias,
  por operação manual e sem scheduler;
- implementar desidentificação local, transacional e idempotente;
- remover identificadores diretos, perfil, preferências, credenciais, sessões,
  tokens, `pending_intent` e textos mutáveis;
- preservar UUID interno, FKs e históricos append-only;
- recusar toda a operação se PII aparecer em estrutura imutável.

## Semântica obrigatória

- o mecanismo é desidentificação/pseudonimização operacional, não garantia de
  anonimização irreversível;
- o audit entry da operação usa apenas metadata fixa e sanitizada;
- snapshots de compra, eventos, auditoria, evidências e transições são
  inspecionados antes da mutação;
- triggers append-only nunca são removidos, desabilitados ou contornados;
- retenções de telemetria são defaults operacionais, não prazos jurídicos;
- nenhuma alegação de certificação ou conformidade integral com a LGPD.

## Fora de escopo

Portal LGPD, workflow jurídico, consentimento complexo, anonimização matemática,
scheduler, infraestrutura externa, exclusão pública pelo bot/API e alteração de
históricos append-only.

## Validação obrigatória

PostgreSQL 18 real com rollback sintético, conflito append-only, limpeza e
desidentificação; Docker Linux com API, worker, Collector, Prometheus e Jaeger;
retenções e memória em runtime; canário ausente de logs/métricas/traces;
`/privacidade` sem IA; Bot API real; pipeline completo.

## Resultado

- `app.privacy` implementa aviso, limpeza e desidentificação controlada;
- PostgreSQL real confirmou atomicidade, limpeza, pseudonimização e recusa antes
  de mutação quando há texto livre em histórico imutável;
- a conta e os dados do proprietário não foram modificados durante a validação;
- Compose confirmou `15d/2GiB`, `10m × 5`, Jaeger com 10.000 traces/512 MB e
  consumo real dentro do teto;
- canário permaneceu ausente de logs, métricas e traces;
- menu e mensagem fixa de `/privacidade` foram validados na Bot API real.
- pipeline completo aprovado em Python 3.14.6 com 601 testes e 90,61% de
  cobertura, Ruff, Alembic, Gitleaks e Docker Compose válidos.

Próxima tarefa executável: TASK-051.

