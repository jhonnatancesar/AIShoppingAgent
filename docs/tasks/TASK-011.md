# TASK-011 — Configurar migrações

Status: Concluída

## Objetivo

Planejar e executar, quando solicitada, a etapa “Configurar migrações”.

## Escopo

Executar somente o objetivo desta tarefa, conforme AGENTS.md, CLAUDE.md e a documentação em docs/.

## Critério de aceite

SQLAlchemy, Psycopg e Alembic configurados com credenciais tipadas, metadata compartilhada, fábrica de sessões, baseline vazia e fluxo documentado. Upgrade, downgrade e novo upgrade validados em PostgreSQL 18 sem antecipar tabelas de domínio.

