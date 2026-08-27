# TASK-098 — Histórico e gráficos de preço por produto/variante

Status: **CONCLUÍDA NO DEV (2026-08-27)** — backend
(`GET /api/v1/offers/{offer_id}/price-history`), frontend
(`PriceHistoryChart`) e suíte de testes completos (unitários,
integração PostgreSQL real com 10 cenários dedicados, `EXPLAIN ANALYZE`
de índice contra volume sintético). Ver "Registro de implementação" no
final deste documento. Publicação em `origin/main` pendente. Sem
deploy — `PROD INTOCADA`.

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

## Registro de implementação

Detalhamento técnico completo em
[`docs/architecture/price-history.md`](../architecture/price-history.md)
(seção "Gráfico e métricas de mercado por Product"). Resumo:

- **Rota**: `GET /api/v1/offers/{offer_id}/price-history?period=<periodo>`,
  ancorada em `Offer` (autorização idêntica às demais rotas de `offers`),
  agregando internamente por `Product` -- nunca um recurso/rota `product`
  novo. `period` inválido produz 422 nativo do FastAPI (`Literal` como
  parâmetro de query), não o envelope `{"error": ...}` da API.
- **Backend**: `backend/app/offers/query.py` (`get_offer_price_history_for_user`
  e funções auxiliares) e `backend/app/webapp/offers_router.py`.
- **Frontend**: `frontend/src/components/PriceHistoryChart.tsx`, integrado em
  `OfferDetailPage.tsx`; `frontend/src/api/offers.ts`/`types.ts`.
- **Testes**: `tests/test_offer_price_history.py` (aritmética de período e
  métricas, puro), `tests/test_webapp_offers_router.py` (contrato HTTP +
  SQL compilado), `tests/integration/test_offer_price_history.py` (10
  cenários contra PostgreSQL real), `tests/integration/
  test_offer_price_history_index_explain.py` (`EXPLAIN ANALYZE`).
- **Índice**: nenhum criado -- o já existente
  `ix_price_observations_offer_observed` bastou, confirmado com `EXPLAIN
  ANALYZE` contra volume sintético representativo.
- **Correção durante a implementação**: os testes de integração
  encontraram que a resolução de moeda (item A/B/C do desenho aprovado)
  não priorizava a observação da própria `Offer` âncora antes de cair
  para outras `Offer`s do `Product` -- corrigido em
  `_resolve_reference_currency` antes de fechar a TASK; não é uma DEC
  nova, só uma correção de implementação contra o desenho já aprovado.
