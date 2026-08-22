# TASK-095 — Página rica de oferta na área USER

Status: **Implementada em DEV e aguardando revisão.**

## Origem

Item 5 da V1.2 (`DEC-077`), imediatamente depois da pré-lista global da
TASK-094. O domínio ainda não consolida várias ofertas num único produto real:
uma `Product` é criada ao descobrir uma nova `Offer`. Por isso esta primeira
página rica é deliberadamente centrada na oferta persistida e usa a rota
`/app/offers/{offer_id}`.

## Acesso

- Exige `WebSession` e `Permission.MISSION_READ` no backend.
- A oferta só é carregada quando existe
  `MissionOfferRelevance → Mission.user_id` para o usuário autenticado.
- Apenas `MATCH` e `POSSIBLE_MATCH` autorizam; `NO_MATCH`, oferta inexistente
  e oferta ligada apenas a outro usuário recebem a mesma negação fail-closed.
- A missão expõe links apenas para suas ofertas relevantes da coleta mais
  recente de cada loja.

## Dados e contrato

`GET /api/v1/offers/{offer_id}` retorna um schema Pydantic explícito, sem
`raw_evidence`, IDs operacionais de coleta ou outros detalhes internos. A
resposta reutiliza `Offer`, `Product`, `Store`, `Seller`, a
`PriceObservation` mais recente (`observed_at DESC, id DESC`) e somente os
`OfferInstallmentOption` dessa observação.

A página apresenta, quando existirem: imagem, título, loja, vendedor,
classificação de vendedor/entrega, condição, disponibilidade, preço à vista,
frete, total, parcelamento, atualização, última visualização e link original.
Ausências são exibidas explicitamente, nunca inventadas.

## Implementação

- Query USER em `app.offers.query`, com ownership no próprio SQL.
- Router específico `app.webapp.offers_router` sob `/api/v1/offers`.
- Links no detalhe web da missão e rota React `/app/offers/:offerId`.
- View React separada do carregamento para teste de render focado via Vite SSR,
  sem adicionar dependência de testes ao frontend.

## Fora de escopo

Reviews, gráficos/histórico detalhado, comparação entre lojas, cupons, admin,
IA, nova coleta, nova entidade de produto, migration e tabela nova.

## Validação

- Cinco testes backend focados aprovam USER com oferta relevante própria, outro
  USER, `NO_MATCH`, seleção da observação mais recente e links na missão.
- O teste React focado renderiza título, vendedor, preço, parcelamento e link.
- Ruff nos Python alterados, frontend lint/build e `git diff --check` aprovados.

Sem suíte completa, Docker, produção, deploy, commit ou push nesta rodada.
