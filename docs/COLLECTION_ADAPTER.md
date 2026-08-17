# Adaptador de coleta

A camada `app.collection` separa a orquestração de missões dos mecanismos usados
para consultar cada fonte. Ela não conhece navegador, seletores, banco de dados nem
regras de normalização.

## Contrato

- `CollectionRequest` identifica a missão, a fonte selecionada, a busca e o instante
  em que a coleta foi pedida.
- `CollectionProvider` é a porta assíncrona implementada por cada Store Provider.
- `RawCollectedOffer` transporta os valores exatamente como coletados, inclusive
  vendedor, frete e fulfillment necessários para marketplaces. A TASK-077
  acrescenta `seller_kind` e `fulfillment_kind`, sem substituir evidência bruta.
- `CollectionResult` representa um lote de uma única fonte e exige uma linha do tempo
  UTC consistente.
- `CollectionAdapter` registra providers sem duplicidade e encaminha cada pedido
  somente ao provider da fonte solicitada.

Campos brutos não são interpretados pelo adaptador. A TASK-025 adicionou
`PriceNormalizer` depois dessa fronteira, produzindo valores exatos sem alterar
`RawCollectedOffer`. A TASK-062 conecta o adaptador às agendas, normaliza e
persiste cada fonte atomicamente, sem ensinar banco ou regras ao adaptador.

A extensão opcional `enrich_marketplace_parties` consulta páginas individuais
somente para Amazon/KaBuM! e somente depois da seleção dos candidatos finais.
Ela preserva quantidade e ordem, roda sequencialmente, sem retry, no máximo em
três candidatos; 401/403/429 interrompe o lote. Providers sem a extensão
continuam com o contrato original e campos `NULL`.

## Evolução prevista

A TASK-024 criou a infraestrutura base descrita em `docs/PLAYWRIGHT.md`. A TASK-055
implementou, sobre esta porta, providers próprios para Pichau, Terabyte, Amazon e
Kabum. Mercado Livre, Shopee e AliExpress permanecem indisponíveis na V1.
