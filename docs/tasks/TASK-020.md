# TASK-020 — Criar critérios de missão

Status: Concluída em 2026-08-02

## Objetivo

Criar os critérios persistentes e editáveis usados por uma missão de compra.

## Escopo

- Modelo `MissionCriteria`, único por missão e protegido por `RESTRICT`.
- Busca obrigatória e preço-alvo monetário opcional com moeda ISO 4217.
- Restrições de coerência, migration reversível, testes e documentação.
- Sem filtros especulativos, JSONB, recorrência, transições, agenda ou API.

## Critério de aceite

Contrato persistente consistente, relação um-para-zero-ou-um protegida, valor e
moeda coerentes, migration linear e reversível e testes aprovados sem antecipar
responsabilidades das próximas tarefas.

