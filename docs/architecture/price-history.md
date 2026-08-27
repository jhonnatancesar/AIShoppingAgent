# Histórico de preços

A TASK-017 disponibiliza consultas internas e somente leitura sobre as observações
imutáveis de preço. Por si só, ela não cria endpoints HTTP, relatórios, gráficos,
alertas ou comparações -- esses recursos são construídos por cima dela em TASKs
posteriores (TASK-038, TASK-098), sem alterar suas consultas nem seu contrato.

## Consultas (TASK-017)

- `list_price_history` lista as observações de uma oferta, com filtros opcionais
  por intervalo inclusivo e disponibilidade.
- `get_latest_price_observation` retorna a observação mais recente ou `None`.
- A listagem usa `limit` de 1 a 100, `offset` não negativo e informa a contagem
  total do conjunto filtrado.
- A ordem é sempre `observed_at DESC, id ASC`, inclusive quando observações têm o
  mesmo horário.

Horários de filtro devem conter fuso. As consultas não modificam, deduplicam nem
descartam observações e preservam valores monetários como `Decimal` no modelo.

A TASK-038 usa o mesmo histórico imutável em um recorte próprio da missão:
somente coletas bem-sucedidas e fontes selecionadas, mantendo identificáveis a
observação corrente, a anterior comparável e o menor total comparável. Esse uso
é somente leitura e não altera as consultas públicas deste módulo.

## Gráfico e métricas de mercado por Product (TASK-098)

A TASK-098 adiciona a primeira exposição HTTP de histórico de preço, ancorada em
`Offer` mas agregada por `Product` (mesma identidade global entre lojas da
TASK-097) -- `GET /api/v1/offers/{offer_id}/price-history?period=<periodo>` em
`backend/app/webapp/offers_router.py`, implementada em
`backend/app/offers/query.py` (`get_offer_price_history_for_user` e as funções
auxiliares `resolve_period_range`, `_fetch_daily_low_points`,
`_resolve_current_amount`, `_resolve_reference_currency`, `_compute_metrics`).

### Autorização

Idêntica à de todo o resto do domínio `offers`: `authorize(session, user,
Permission.MISSION_READ, resource_type="offer", resource_id=offer_id)` no
router, e `_accessible_offer_exists` (a mesma cláusula `EXISTS` correlata já
usada por `comparison_offers_statement`, TASK-103) embutida em cada consulta
SQL como filtro de posse -- nunca uma lista de `offer_id`s materializada em
Python. Nenhum `resource_type="product"` novo foi criado; o `Product` é só a
unidade de agregação interna.

### Períodos

`1d`, `7d`, `1m`, `6m`, `1a`, `all` -- resolvidos em
`resolve_period_range` sempre a partir do dia comercial em
`America/Sao_Paulo` (nunca UTC), com `1m`/`6m`/`1a` usando aritmética real de
calendário (`_subtract_calendar_months`, com clamping de dia -- ex. 31/08 menos
6 meses cai em 28 ou 29/02, conforme o ano -- nunca uma aproximação fixa de
30/180/365 dias). `all` nunca gera o predicado SQL `observed_at >= NULL`
(que eliminaria todas as linhas): o limite inferior simplesmente não é
adicionado à consulta quando o período é `all`.

### Série (linhas do gráfico)

Uma linha por `Store` com pelo menos uma `Offer` acessível do `Product`. O
ponto de cada dia comercial é o menor `PriceObservation.amount` daquele
Store naquele dia, filtrado a observações comercialmente válidas -- `condition
= 'new'`, `availability = 'available'`, mesma moeda de referência (nunca
`used`/`refurbished`/indisponível/moeda diferente) -- com desempate por
`ROW_NUMBER() OVER (PARTITION BY store_id, dia_comercial ORDER BY amount ASC,
observed_at DESC, id ASC)`. Duas `Offer`s da mesma `Store` no mesmo dia
colapsam num único ponto.

### Métricas

- **`current_amount`**: nunca o mínimo do dia. Resolve primeiro a observação
  MAIS RECENTE de cada `Offer` acessível (qualquer condição/disponibilidade),
  só depois checa se essa observação específica é comercialmente válida --
  uma `Offer` cuja última observação ficou indisponível nunca "ressuscita" um
  preço antigo válido. É o `MIN` entre as `Offer`s atualmente válidas.
- **`min_amount`/`max_amount`/`average_amount`**: calculados sobre
  `daily_market_low(dia) = MIN(menor preço de cada Store naquele dia)`, nunca
  sobre a média bruta de todos os pontos -- isso evitaria viés por dias com
  mais ou menos lojas coletadas.
- **`variation_percent`**: `(current_amount - primeiro daily_market_low do
  período) / primeiro daily_market_low × 100`, `None` sem `current_amount` ou
  sem série, e `None` (nunca divisão por zero) se a base for `0`.

### Moeda

Cadeia determinística, nunca fallback para BRL: (A) a observação mais recente
da própria `Offer` âncora, se existir; (B) senão, a observação mais recente
entre as demais `Offer`s acessíveis do `Product`; (C) senão, `currency=null`,
série vazia, métricas `null`. Moedas diferentes nunca são convertidas nem
misturadas na mesma série.

### `comparable=false`

Quando `Product.identity_key IS NULL` (identidade ainda não resolvida pela
TASK-097), a resposta é HTTP 200 com `comparable=false`,
`reason="unresolved_product_identity"`, série vazia e métricas `null` --
nunca 404.

### Índice

Nenhum índice novo foi criado. `EXPLAIN ANALYZE` contra volume sintético (~21
mil `PriceObservation`, 1 produto com 6 `Offer`s e ~1 ano de histórico, mais
150 produtos de ruído) confirmou que o índice já existente
`ix_price_observations_offer_observed (offer_id, observed_at DESC, id)` é
suficiente para os três períodos auditados (`1d`, `1a`, `all`) e para a
consulta de `current_amount`, sempre via índice, nunca `Seq Scan` completo em
`price_observations` (`tests/integration/test_offer_price_history_index_explain.py`).

### Frontend

`frontend/src/components/PriceHistoryChart.tsx` -- seletor de período,
gráfico de linhas (Recharts, via `ChartContainer` de
`frontend/src/components/ui/chart.tsx`, uma linha por loja) e grade de
métricas, integrado em `OfferDetailPage.tsx`. `frontend/src/api/offers.ts`
expõe `offersApi.priceHistory(offerId, period)`.
