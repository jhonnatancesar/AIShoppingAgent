# TASK-103 — Comparação entre lojas por produto/variante

Status: **Implementada no DEV, aguardando revisão.**

## Objetivo

Comparar, na página de uma Offer, ofertas autorizadas do mesmo produto e
variante global resolvidos pela TASK-097. A comparação nunca usa título,
missão ou aproximação como identidade.

## Contrato

- `GET /api/v1/offers/{offer_id}/comparison`, com WebSession e
  `MISSION_READ`;
- a Offer âncora e cada Offer retornada precisam estar ligadas a missões do
  USER como `MATCH`/`POSSIBLE_MATCH`;
- `Product.identity_key IS NULL` retorna `comparable=false`, sem união;
- mesma variante significa o mesmo `Product.id`; nenhuma IA participa;
- usa somente a última `PriceObservation` de cada Offer e avaliação própria da
  origem; ausência continua `NULL`/oculta;
- até 5 ofertas por loja, deterministicamente por novo, vendedor próprio,
  disponibilidade, total/preço e UUID.

## Web

A página `/app/offers/{offer_id}` agrupa as opções por loja e mostra total,
preço à vista, frete, condição, disponibilidade, parcelamento e avaliação
quando reais, além dos links de detalhe e loja.

## Fora de escopo

Histórico/gráficos (TASK-098), cupons, IA, nova coleta, nova loja e comparação
de família/categoria ou variantes diferentes. Nenhuma migration é necessária.
