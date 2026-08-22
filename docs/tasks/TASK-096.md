# TASK-096 — Avaliações por oferta/loja na página USER e no Telegram

Status: **Concluída, aprovada e publicada em `origin/main` em 2026-08-22
(`fd5a6f9`).**

## Origem e objetivo

Item 6 da V1.2 (`DEC-078`), após a página rica da TASK-095. Coletar e
apresentar `rating_average` e `review_count` quando a própria loja os declarar,
sempre vinculados à `Offer`/`Store`. Não existe nota global do produto.

## Modelo

`Offer` mantém somente o snapshot atual completo da origem:
`rating_average`, `review_count` e `rating_observed_at`, todos nulos ou todos
preenchidos. A migration `20260822_0003` adiciona os campos e constraints de
faixa/completude. Não há tabela de reviews nem texto individual de avaliação.

Ausência ou parsing ambíguo não apaga o último snapshot válido e não inventa
valores. Contagem abreviada visual (por exemplo, `2,2 mil`) não é expandida;
somente uma contagem exata explícita é aceita.

## Coleta extensível e custo de navegação

- Cards das quatro lojas podem preencher o contrato comum
  `RawCollectedOffer.raw_rating_average/raw_review_count`.
- Quando a avaliação só aparece no detalhe, o provider usa
  `resolve_offer_rating`, baseado em `AggregateRating` estruturado.
- Vendedor, condição, parcelamento e avaliação são combinados por
  `enrich_offer_details`: no máximo uma abertura por oferta, nunca uma rodada
  de páginas para cada campo.
- A Terabyte continua sem página individual enquanto vigorar o bloqueio
  Cloudflare documentado; avaliação nela vem do card, sem navegação nova.
- Uma loja futura implementa apenas seu card e, se necessário, os hooks de
  detalhe; orquestração, persistência, página e Telegram permanecem comuns.

## Apresentação

- `/app/offers/{offer_id}` mostra “Avaliações na {loja}”, nota, contagem e
  instante observado; ausência é explícita.
- Alertas de preço e pré-listas Telegram mostram uma linha curta de avaliação
  quando o snapshot completo existe.
- Nenhuma ordenação comercial, relevância ou decisão de IA usa avaliações.

## Fora de escopo

Texto de reviews, usuários/autores, distribuição de estrelas, resposta da
loja, gráficos/histórico de avaliação, agregação entre lojas e IA.

## Validação

- Inspeção pública controlada confirmou no card Amazon nota e contagem exata
  separada em atributos acessíveis; Pichau não expôs nota no card observado.
- 128 testes backend focados aprovados, cobrindo o contrato dos quatro cards,
  `AggregateRating`, abertura única, Terabyte sem página individual,
  normalização/persistência, endpoint USER e Telegram.
- Teste React focado, frontend lint/build, Ruff e `git diff --check` aprovados.
- Runner oficial aprovou migration e teste focado no PostgreSQL 18.4
  descartável, head `20260822_0003`.

Sem produção ou deploy nesta implementação.
