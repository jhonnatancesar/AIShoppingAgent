# TASK-125 — Gráfico de histórico de preço nunca mostra o preço com cupom aplicado

Status: **Registrada (2026-09-21), escopo definido, implementação NÃO
iniciada.** Separada da TASK-124 por decisão explícita do usuário — a
mesma investigação (caso 9800X3D/Kabum/`CPUPROMO`) revelou dois
problemas distintos: TASK-124 é o pipeline de alerta nunca considerar
um cupom novo quando o preço de tabela não muda; esta TASK é o gráfico
de histórico de preço nunca refletir o preço com desconto de cupom, em
nenhuma circunstância — mesmo depois da TASK-124 corrigida.

## Contexto (confirmado no código)

A série usada pelo gráfico de histórico
(`PriceHistoryPoint`/`PriceHistorySeries`/`PriceHistoryMetrics`,
`backend/app/offers/query.py:593-627`, montada por
`build_offer_price_history`/`_fetch_daily_low_points` e exposta em
`GET /api/v1/offers/{offer_id}/price-history`,
`backend/app/webapp/offers_router.py:565-606`) lê exclusivamente
`PriceObservation.amount` — o preço de tabela bruto coletado. Nenhum
ponto da série é ajustado por cupom.

O cálculo de cupom (`best_applicable_coupon`,
`app/coupons/pricing.py`) só acontece **transiente**, no momento da
leitura de uma oferta específica (`get_user_offer`,
`offers_router.py:615-647`) ou na avaliação de alerta — nunca é
persistido como um ponto próprio de série histórica. Ou seja: mesmo
que a TASK-124 já garanta que o alerta dispare corretamente quando um
cupom baixa o preço efetivo, o gráfico de histórico daquele mesmo
produto continua mostrando só a linha do preço de tabela — o menor
preço "de verdade" (com cupom) nunca aparece visualmente, mesmo depois
do alerta ter sido enviado.

## Objetivo

Fazer o preço efetivo (com o melhor cupom aplicável do momento)
aparecer no histórico de preço exibido ao usuário — pelo menos no
ponto mais recente da série (o cupom vigente agora), idealmente também
retroativo quando fizer sentido (a decidir na implementação: cupom é
um dado com vigência própria, nem sempre é possível reconstruir
retroativamente qual cupom estava ativo em cada dia passado sem ter
sido registrado na época).

## Escopo (a definir na implementação — perguntas em aberto)

- Se o ajuste de cupom entra na série (`PriceHistoryPoint`, um ponto
  por dia/loja) ou só nas métricas agregadas (`PriceHistoryMetrics.
  current_amount`, mostrando "preço atual com cupom" separado do
  histórico bruto).
- Se cupons antigos/expirados devem ser reconstruídos retroativamente
  (provavelmente não é possível de forma confiável sem uma tabela de
  auditoria de vigência de cupom por dia) ou se o ajuste vale só para
  o ponto mais recente/atual.
- Onde no frontend (`frontend/`) o preço com cupom deveria aparecer
  junto ao gráfico (badge/anotação no ponto atual, linha separada,
  etc.) — decisão de produto, não só de dado.

## Fora de escopo

TASK-124 (pipeline de alerta) — já corrigida separadamente. Cálculo do
cupom em si (`app/coupons/pricing.py`) — já correto e determinístico,
não precisa mudar.

## Validação futura

Confirmar visualmente (webapp real) que, para um produto com cupom
ativo no momento, o gráfico/card de histórico mostra o preço com
desconto de alguma forma — não só o preço de tabela igual ao que
apareceria sem nenhum cupom cadastrado.
