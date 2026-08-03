# TASK-025 — Normalizar preço e moeda

Status: Concluída em 2026-08-02

## Objetivo

Planejar e executar, quando solicitada, a etapa “Normalizar preço e moeda”.

## Escopo

Executar somente o objetivo desta tarefa, conforme AGENTS.md, CLAUDE.md e a documentação em docs/.

A normalização deve separar preço do item e frete, calcular o total exato na
mesma moeda e preservar vendedor e fulfillment quando a fonte for marketplace.

## Critério de aceite

Escopo concluído, documentado e verificado conforme os critérios da tarefa.

## Entrega

- Normalização exata com `Decimal`, limitada a `numeric(19,4)`.
- Separação entre item, frete e total, distinguindo frete grátis de desconhecido.
- Validação de moeda declarada, símbolo monetário e moeda do frete.
- Preservação integral da oferta bruta, vendedor, fulfillment e evidência.
- Disponibilidade normalizada como `available`, `unavailable` ou `unknown`.
- Validação com fixtures e com as quatro fontes reais em container Linux/Xvfb.

