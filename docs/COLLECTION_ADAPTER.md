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

Campos brutos não devem ser interpretados nessa fronteira. Preço e moeda serão
normalizados na TASK-025; execução e persistência do lote pertencem à TASK-026.

## Evolução prevista

A TASK-024 criará a infraestrutura base de navegador. A TASK-055 implementará, sobre
esta porta, providers próprios para Pichau, Terabyte, Amazon e Kabum. Mercado Livre,
Shopee e AliExpress permanecem indisponíveis na V1.
