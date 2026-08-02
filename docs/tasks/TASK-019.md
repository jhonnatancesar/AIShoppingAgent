# TASK-019 — Criar missões

Status: Concluída em 2026-08-02

## Objetivo

Criar a entidade persistente que mantém a intenção de compra e seu estado atual.

## Escopo

- Modelo SQLAlchemy `Mission` e enum PostgreSQL `mission_status`.
- Propriedade obrigatória por usuário com `RESTRICT`.
- Estado inicial `draft`, prazo opcional e versão concorrente não negativa.
- Restrições, índices, migration reversível, testes e documentação.
- Sem critérios, transições, agenda, API, coleta, eventos ou alertas.

## Critério de aceite

Contrato persistente consistente com o ciclo de vida, enum e invariantes
protegidos, índices previstos, migration linear e reversível e testes aprovados,
sem antecipar comportamentos das tarefas seguintes.

