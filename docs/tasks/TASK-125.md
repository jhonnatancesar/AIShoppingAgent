# TASK-125 — Gráfico de histórico de preço nunca mostra o preço com cupom aplicado

Status: **Concluída (2026-09-26)**. Entra na `v1.3.26` junto com
124/126/127/128.

Validação:
- integração: 385/385 na rodada completa final (migration `20260926_0003`
  com upgrade → downgrade → upgrade → `alembic check`);
- unitária: 2769 passando, cobertura 90,22%;
- frontend: `tsc`, `oxlint`, `test:price-history` e `test:offer`
  passando.

Separada da TASK-124 por decisão explícita do usuário: a mesma
investigação (caso 9800X3D/Kabum/`CPUPROMO`) revelou dois problemas.
- TASK-124: o alerta nunca considerava um cupom novo quando o preço de
  tabela não mudava.
- Esta TASK: o gráfico de histórico nunca refletia o preço com desconto.

## Problema (confirmado no código)

A série do gráfico (`_fetch_daily_low_points`, `offers/query.py`) lia só
`PriceObservation.amount`, o preço de tabela. O melhor cupom aplicável
(`best_applicable_coupon`) já era calculado a cada coleta, na Fase B, para
o alerta, mas era **descartado** em seguida. Nunca ficava guardado, então o
gráfico não tinha de onde tirar o preço com cupom.

## Decisões do usuário (2026-09-26)

1. **Tudo na mesma linha**: nada de linha nova. O ponto do dia fica no
   **preço com cupom** quando havia cupom aplicável. Ao passar o mouse
   aparecem o preço normal, o preço com cupom e qual cupom foi usado.
   Palavras do usuário: "quero tudo na mesma linha".
2. **Só daqui pra frente**: nada é reconstruído para os dias anteriores
   ao deploy. Reconstruir pelo período em que o cupom foi visto seria
   aproximação. Dias antigos continuam só com o preço normal.

## O que foi feito

- Tabela nova `offer_coupon_price_days`, migration `20260926_0003`. Guarda
  o preço com cupom que a coleta calculou.
  - Chave: (Offer, observação de preço, dia comercial de São Paulo). A
    mesma observação pode ser reaproveitada em dias diferentes, e o cupom
    de cada dia pode mudar.
  - Várias coletas no mesmo dia ficam com o **menor** preço com cupom.
  - Guarda uma cópia do código do cupom (`''` = cupom automático).
- Fase C da coleta (`_record_coupon_price_day`): grava o cupom que a Fase
  B já calculou, sem nenhuma chamada nova.
  - O desconto é arredondado na precisão da coluna (half-up) antes de
    derivar o final. Um cupom percentual podia gerar casas demais e
    quebrar a regra `final = normal − desconto`.
  - Roda em savepoint próprio: falhar aqui nunca derruba a coleta.
- Gráfico (`GET /api/v1/offers/{id}/price-history`):
  - cada confirmação usa o preço com cupom do mesmo dia quando existe;
  - o ponto carrega `original_amount` e `coupon_code` para o tooltip;
  - mínimo, máximo e média acompanham a linha;
  - o **"Atual"** usa o cupom vigente agora, com a mesma regra do card da
    oferta, para o último ponto e o "Atual" nunca divergirem;
  - com `coupons_enabled` desligada, nada muda (preço de tabela puro).
- Frontend:
  - `PriceHistoryTooltipContent` mostra "Preço normal / Com cupom /
    Cupom";
  - os dados do gráfico ficam em `priceHistoryChartData.ts`;
  - teste novo: `npm run test:price-history`.

## Achado corrigido junto (TASK-128)

Ao documentar o deploy, apareceu um problema no backfill da TASK-123:
- o backlog selecionava "sem vínculo" com `LIMIT`, sem excluir os títulos
  já entregues à leitura de página (`awaiting_page`/`unrecognized`);
- com o worker parado durante o backfill, que é a sequência obrigatória de
  deploy, toda rodada pegaria os mesmos títulos (cache, sem IA) e nunca
  chegaria ao resto.

Corrigido:
- `unlinked_product_criteria()` exclui produtos entregues à leitura de
  página;
- o terminal `unrecognized` também guarda o produto de origem;
- o `--count-only` explica que esses títulos ficam fora da conta.

## Fora de escopo

Reconstruir o passado. Mostrar o cupom fora do gráfico, por exemplo no
bloco "Preço histórico" da TASK-127.
