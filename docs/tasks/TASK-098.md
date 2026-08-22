# TASK-098 — Histórico e gráficos de preço por produto/variante

Status: **Formalizada e reservada; não implementar antes da conclusão da TASK-097.**

## Dependência obrigatória

Depende da identidade global determinística de produto/variante entregue pela
TASK-097. `mission_id`, semelhança textual ou IA nunca podem definir quais
ofertas entram no mesmo histórico.

Somente uma identidade `SPECIFIC_PRODUCT` completa pode alimentar gráficos
entre lojas. `PRODUCT_FAMILY` ainda sem escolha e `GENERIC_CATEGORY` nunca
geram uma série agregando produtos diferentes; após uma família escolher uma
variante específica, o gráfico usa exclusivamente essa identidade.

## Escopo exclusivo

- gráficos de linha nos períodos 1 dia, 7 dias, 1 mês, 6 meses, 1 ano e tudo;
- gráfico por loja para o produto/variante selecionado;
- gráfico “Todas as lojas”, com uma linha por loja e somente ofertas ligadas à
  mesma identidade completa da TASK-097;
- quando houver várias ofertas da mesma variante na mesma loja, representar o
  ponto temporal pelo menor preço válido e disponível, com moeda compatível e
  desempate determinístico documentado;
- métricas de preço atual, mínimo, máximo, média, menor histórico registrado e
  variação percentual;
- cálculos determinísticos no PostgreSQL/backend, sem IA;
- autorização USER no backend a partir das missões/variantes pertencentes ao
  usuário, nunca apenas na interface.

## Fora de escopo

Criar ou corrigir identidade de produto, resolver pedidos genéricos, selecionar
variantes, agregar produtos diferentes, prever preços, coletar histórico
externo, reviews, cupons ou comparação baseada em IA.
