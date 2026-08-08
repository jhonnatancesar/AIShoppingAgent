# ADR-008 — Separar métricas, traces e disponibilidade

## Status

Aceita em 2026-08-08 pela TASK-045.

## Contexto

O MVP precisa diagnosticar API, PostgreSQL e worker em Ubuntu Server headless
sem duplicar métricas, vazar dados do usuário ou tornar ferramentas de
observabilidade dependências funcionais.

## Decisão

Prometheus coleta métricas diretamente dos endpoints da API e do worker.
Traces seguem por OTLP/HTTP ao OpenTelemetry Collector e depois ao Jaeger.
Logs continuam JSON em `stdout` e recebem IDs do contexto ativo. `/health`
mede somente liveness; `/ready` mede PostgreSQL com timeout. Labels usam
catálogos limitados e spans SQL omitem statement e valores integralmente.

Regras Prometheus entregam apenas detecção/estado. Notificação externa exigiria
Alertmanager/receiver e permanece fora da TASK-045.

## Consequências

- indisponibilidade de Collector, Prometheus ou Jaeger não derruba a API;
- não há duplicidade de métricas por OTLP;
- diagnóstico preserva correlação sem IDs de alta cardinalidade em labels;
- adicionar novo `event_type` como label exige atualizar a allowlist;
- Grafana, Alertmanager, retenção externa e notificações ficam para escopo
  futuro explicitamente aprovado.
