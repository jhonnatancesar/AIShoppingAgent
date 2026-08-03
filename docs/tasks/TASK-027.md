# TASK-027 — Criar alertas de preço

Status: Concluída em 2026-08-02

## Objetivo

Avaliar observações históricas e produzir candidatos tipados de alerta de preço.

## Escopo

- Detectar queda de preço na mesma oferta e moeda.
- Detectar a entrada no total-alvo de uma missão ativa sem repetição contínua.
- Ignorar disponibilidade não confirmada e comparações entre moedas distintas.
- Não persistir, publicar, consumir ou notificar eventos.

## Ordem e dependências

Executar após a TASK-042, para que os alertas usem o catálogo de eventos definido.

## Critério de aceite

Avaliação determinística produz somente eventos válidos do catálogo da TASK-042,
preserva `Decimal` e rejeita entidades ou sequências históricas inconsistentes.
