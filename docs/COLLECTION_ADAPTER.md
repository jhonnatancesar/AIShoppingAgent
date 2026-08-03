# Adaptador de coleta

A camada `app.collection` separa a orquestração de missões dos mecanismos usados
para consultar cada fonte. Ela não conhece navegador, seletores, banco de dados nem
regras de normalização.

## Contrato

- `CollectionRequest` identifica a missão, a fonte selecionada, a busca e o instante
  em que a coleta foi pedida.
- `CollectionProvider` é a porta assíncrona implementada por cada Store Provider.
- `RawCollectedOffer` transporta os valores exatamente como coletados, inclusive
  vendedor, frete e fulfillment necessários para marketplaces.
- `CollectionResult` representa um lote de uma única fonte e exige uma linha do tempo
  UTC consistente.
- `CollectionAdapter` registra providers sem duplicidade e encaminha cada pedido
  somente ao provider da fonte solicitada.

Campos brutos não são interpretados pelo adaptador. A TASK-025 adicionou
`PriceNormalizer` depois dessa fronteira, produzindo valores exatos sem alterar
`RawCollectedOffer`; execução e persistência do lote pertencem à TASK-026.

## Evolução prevista

A TASK-024 criou a infraestrutura base descrita em `docs/PLAYWRIGHT.md`. A TASK-055
implementou, sobre esta porta, providers próprios para Pichau, Terabyte, Amazon e
Kabum. Mercado Livre, Shopee e AliExpress permanecem indisponíveis na V1.
