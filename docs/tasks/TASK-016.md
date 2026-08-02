# TASK-016 — Criar auditoria

Status: Concluída em 2026-08-02

## Objetivo

Criar a trilha persistente e imutável para ações relevantes do sistema.

## Escopo

- Modelo SQLAlchemy `AuditEntry` com ator opcional, ação, recurso, JSONB e UTC.
- FK de usuário com `RESTRICT` e índices por recurso e ator.
- Proteção append-only no PostgreSQL contra `UPDATE` e `DELETE`.
- Migration Alembic reversível, metadata compartilhada, testes e documentação.
- Sem catálogo de ações, instrumentação automática, API, autenticação ou eventos.

## Critério de aceite

Contrato persistente consistente, entradas protegidas contra mutação, migration
linear e reversível, índices e integridade validados, testes aprovados e limites
de segurança documentados.

