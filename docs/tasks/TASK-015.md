# TASK-015 — Criar observações de preço

Status: Concluída em 2026-08-02

## Objetivo

Planejar e executar, quando solicitada, a etapa “Criar observações de preço”.

## Escopo

Executar somente o objetivo desta tarefa, conforme AGENTS.md, CLAUDE.md e a documentação em docs/.

Cada observação deve registrar preço do item, moeda, frete opcional, total exato
e fulfillment opcional, preservando a oferta — e portanto o vendedor — observada.

## Ordem e dependências

Executar após a TASK-026, pois cada observação exige uma coleta persistida por
`collection_run_id`.

## Critério de aceite

Escopo concluído, documentado e verificado conforme os critérios da tarefa.

