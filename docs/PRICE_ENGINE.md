# Motor de Preços

O motor de preços normaliza resultados brutos por `PriceNormalizer`. A regra
fundamental é preservar a oferta coletada inteira como evidência, incluindo
horário, origem, vendedor, fulfillment e disponibilidade quando houver.

## Contrato monetário

- Valores usam `Decimal`; `float` é proibido.
- `amount` representa somente o preço do item.
- `shipping_amount` é zero para frete grátis e nulo quando o frete não foi
  informado ou depende de cálculo posterior.
- `total_amount` é a soma exata do item com o frete conhecido; quando o frete é
  desconhecido, equivale ao item sem afirmar que a entrega é gratuita.
- `currency` usa três letras ISO 4217 e deve ser consistente com o símbolo
  encontrado no preço e no frete.
- BRL aceita os formatos reais observados nas fontes selecionadas, como
  `4.999,90` e `9,324.42`; separadores ambíguos são rejeitados.

Disponibilidade usa o vocabulário `available`, `unavailable` e `unknown`. A
ausência de texto explícito resulta em `unknown`, sem inferência otimista.

Erros de valor ausente, formato ambíguo, precisão superior a quatro casas,
limite de `numeric(19,4)` ou conflito de moeda geram
`CollectionNormalizationError` e não produzem observação parcial.

Persistência pertence à TASK-026 e observações históricas à TASK-015.
Comparações, alertas e agregações derivam do histórico; não devem destruí-lo.

Esta semântica de persistência (`total_amount` = item + frete conhecido) não
define a base dos alertas de monitoramento da V1: o avaliador de alertas
(`docs/PRICE_ALERTS.md`, `DEC-045`) compara sempre `amount`, nunca
`total_amount`, para nunca misturar bases diferentes entre observações.
`app.purchase` (custo final/compra) é quem efetivamente usa a semântica de
`total_amount` descrita aqui, exigindo frete conhecido para afirmar um total.
