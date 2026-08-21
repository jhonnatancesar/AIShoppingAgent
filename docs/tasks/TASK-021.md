# TASK-021 — Criar transições de missão

Status: Concluída em 2026-08-02

## Objetivo

Implementar a execução persistente e atômica das transições de missão definidas na TASK-018.

## Escopo

- Histórico append-only com estado anterior, novo estado, comando, ator, motivo e instante.
- Serviço transacional com validação do estado atual, critérios, ao menos uma fonte, prazo e versão concorrente.
- Atualização atômica de `status`, `state_version`, `updated_at` e histórico.
- Migration reversível, testes e documentação, sem API, agenda, coleta ou eventos.

## Critério de aceite

Ciclo de vida executável conforme `docs/architecture/mission-system.md`, histórico imutável,
concorrência protegida e migration validada em PostgreSQL real.

