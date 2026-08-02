# TASK-012 — Criar usuários

Status: Concluída

## Objetivo

Planejar e executar, quando solicitada, a etapa “Criar usuários”.

## Escopo

Executar somente o objetivo desta tarefa, conforme AGENTS.md, CLAUDE.md e a documentação em docs/.

## Critério de aceite

Modelo SQLAlchemy de usuário e papéis `USER`, `ADMIN` e `DEV` registrado na metadata compartilhada, com restrições e migração reversível validadas em PostgreSQL 18, sem antecipar autenticação, autorização, canais ou API.

