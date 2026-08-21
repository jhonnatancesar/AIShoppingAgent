# TASK-010 — Definir modelo de dados

Status: Concluída

## Objetivo

Planejar e executar, quando solicitada, a etapa “Definir modelo de dados”.

## Escopo

Executar somente o objetivo desta tarefa, conforme AGENTS.md, CLAUDE.md e a documentação em docs/.

## Ordem e dependências

Executar após a TASK-018, para que o modelo persistente de missões incorpore os estados e transições definidos.

## Critério de aceite

Esquema relacional do MVP definido com entidades, tipos PostgreSQL, relações, chaves, restrições, índices mínimos e regras de consistência e histórico, incorporando o ciclo de vida de missões sem antecipar migrações ou persistência.

## Correção de escopo — fontes selecionadas

O contrato foi ampliado em 2026-08-02 para representar `mission_sources`, tipo
de fonte, vendedores de marketplace, ofertas por vendedor, frete, total e
fulfillment históricos. A correção preserva o histórico e está detalhada em
`docs/database/schema.md` e `docs/architecture/providers.md`.
