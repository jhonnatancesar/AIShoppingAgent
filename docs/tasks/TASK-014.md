# TASK-014 — Criar ofertas

Status: Concluída em 2026-08-02

## Objetivo

Criar a entidade persistente de ofertas e a origem mínima necessária para sua
integridade referencial.

## Escopo

- Modelo `Store` para normalizar varejista ou marketplace, sem implementar provider.
- Modelo `Seller` para vendedores de marketplace, protegido por `RESTRICT` e coerência de fonte.
- Modelo `Offer` associado a produto e fonte, com vendedor opcional da mesma fonte.
- Identidade da oferta protegida por ID externo opcional e URL canônica.
- Migration Alembic reversível, índices, restrições e metadata compartilhada.
- Testes e documentação, sem preço, disponibilidade, API ou coleta.

## Critério de aceite

Modelos e migrations consistentes, relações e identidades de varejo e marketplace
protegidas, testes aprovados e limites documentados sem antecipar coleta.

Corrigida pela revisão `20260802_0009` após a inclusão da Amazon com vendedores
terceiros no escopo da V1.

