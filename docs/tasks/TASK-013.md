# TASK-013 — Criar produtos

Status: Concluída em 2026-08-02

## Objetivo

Criar a entidade persistente de produtos canônicos sobre a infraestrutura de
migrações existente.

## Escopo

- Modelo SQLAlchemy `Product` com UUID, nome, marca e modelo opcionais e
  timestamps UTC.
- Restrições de integridade para textos obrigatórios ou informados.
- Migration Alembic reversível e registro na metadata compartilhada.
- Política conservadora de deduplicação, sem unicidade artificial por nome.
- Testes e documentação do contrato e de seus limites.

## Critério de aceite

Modelo e migration consistentes, revisão linear e reversível, metadata
compartilhada atualizada, testes aprovados e limites documentados sem antecipar
ofertas, preços, catálogo HTTP ou integrações.

