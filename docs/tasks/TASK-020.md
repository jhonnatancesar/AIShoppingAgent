# TASK-020 — Criar critérios de missão

Status: Concluída em 2026-08-02

## Objetivo

Criar os critérios persistentes e editáveis usados por uma missão de compra.

## Escopo

- Modelo `MissionCriteria`, único por missão e protegido por `RESTRICT`.
- Busca obrigatória e total-alvo monetário opcional com moeda ISO 4217, considerando frete conhecido.
- Seleção de uma ou mais fontes por relações `mission_sources` com `RESTRICT`.
- Restrições de coerência, migration reversível, testes e documentação.
- Sem filtros especulativos, JSONB, recorrência, transições, agenda ou API.

## Critério de aceite

Contrato persistente consistente, critérios e fontes protegidos, valor e moeda
coerentes, migrations lineares e reversíveis e testes aprovados.

A seleção de fontes foi adicionada pela revisão corretiva `20260802_0009`.

