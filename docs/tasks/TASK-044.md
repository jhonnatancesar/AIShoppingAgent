# TASK-044 — Implementar consumo de eventos

Status: Concluída em 2026-08-08

## Objetivo

Implementar consumo durável e concorrente dos eventos publicados pela
TASK-043, preservando rastreabilidade e permitindo retry após falha.

## Escopo

- Criar o histórico append-only `event_consumption_attempts`.
- Reivindicar eventos ainda não concluídos pelo consumidor em ordem
  determinística, com `FOR UPDATE SKIP LOCKED`.
- Registrar tentativas `succeeded` ou `failed`, com código estável de falha.
- Garantir semântica at-least-once por consumidor e retry ilimitado após
  falha.
- Manter o controle de `commit` e `rollback` no chamador, sem antecipar
  worker, scheduler, Telegram, dead-letter queue, backoff ou exactly-once.

## Critério de aceite

- Dois consumidores concorrentes não reivindicam o mesmo evento enquanto o
  lock transacional está aberto.
- Uma falha deixa o evento elegível para nova tentativa; um sucesso o encerra
  apenas para aquele `consumer_name`.
- Tentativas são imutáveis no PostgreSQL e nunca removem o evento original.
- Migração é reversível e mantém uma única cabeça Alembic.
- Suíte automatizada e validação real em PostgreSQL aprovadas.

## Implementação

- Modelo `EventConsumptionAttempt` e enum `ConsumptionOutcome` em
  `app.events.models`.
- Serviços `claim_unconsumed_events` e `record_consumption_attempt` em
  `app.events.consumption`.
- Revisão Alembic `20260808_0004`, com FK `RESTRICT`, índice parcial de
  sucessos e trigger contra `UPDATE`/`DELETE`.
- Contrato operacional detalhado em `docs/EVENT_CONSUMPTION.md`.

## Validação

- 394 testes automatizados aprovados; cobertura total de 95,46%.
- PostgreSQL real descartável: concorrência com `SKIP LOCKED`, falha seguida
  de retry e sucesso, independência entre consumidores e rejeição real de
  `UPDATE`/`DELETE` aprovadas.
- Ciclo real `upgrade head` → `downgrade -1` → `upgrade head` aprovado; enum
  removido no downgrade e nenhuma estrutura temporária permaneceu.

## Próxima tarefa no fluxo

TASK-036 — Criar notificações Telegram.

