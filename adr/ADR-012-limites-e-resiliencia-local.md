# ADR-012 — Limites persistentes e resiliência local

## Status

Aceita.

## Contexto

A V1 já recebe webhooks, chama IA/lojas/Telegram e consome eventos, mas não
possuía política uniforme contra replay, abuso ou cascatas de falha. Adicionar
Redis, broker ou coordenação distribuída ampliaria o MVP.

## Decisão

Usar PostgreSQL para fatos que precisam sobreviver a reinício: recibos de
`update_id`, janela de rate limit e histórico append-only de retry/dead letter.
Usar primitivas locais por processo para timeout, retry de operações seguras e
circuit breaker por dependência/operação. Operações potencialmente não
idempotentes não recebem retry cego. `events` não ganha estado mutável.

## Consequências

Replay e cota são consistentes entre processos, e terminais de evento são
protegidos pelo banco. Circuit breakers não compartilham estado entre réplicas;
essa limitação é aceita na V1. O desenho continua compatível com Docker Compose
e Ubuntu Server headless e não introduz infraestrutura nova.
