# TASK-014 — Criar ofertas

Status: Concluída em 2026-08-02

## Objetivo

Criar a entidade persistente de ofertas e a origem mínima necessária para sua
integridade referencial.

## Escopo

- Modelo `Store` para normalizar a loja nacional, sem implementar provider.
- Modelo `Offer` associado obrigatoriamente a produto e loja com `RESTRICT`.
- Identidade da oferta protegida por ID externo opcional e URL canônica.
- Migration Alembic reversível, índices, restrições e metadata compartilhada.
- Testes e documentação, sem preço, disponibilidade, API ou coleta.

## Critério de aceite

Modelos e migration consistentes, relações e unicidades protegidas, revisão
linear e reversível, testes aprovados e limites documentados sem antecipar
observações de preço ou integrações.

