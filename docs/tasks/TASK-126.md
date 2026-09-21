# TASK-126 — Nova superfície DEV-only para ver TODOS os itens pesquisados de uma missão (incluindo os descartados)

Status: **Registrada (2026-09-21), escopo definido, implementação NÃO
iniciada.**

## Correção de premissa (confirmada no código antes de escrever o escopo)

O pedido original era "mostrar todos os itens pesquisados somente para
quem for DEV" — presumindo que hoje isso já é visível para todo mundo.
**Não é bem assim**: o detalhe de missão já filtra por relevância para
QUALQUER usuário, DEV incluso —
`ACCESSIBLE_RELEVANCE = (OfferRelevance.MATCH, OfferRelevance.
POSSIBLE_MATCH)` (`backend/app/offers/query.py:44-47`, enum real em
`backend/app/collection/relevance.py:27-32`: `MATCH`, `POSSIBLE_MATCH`,
`NO_MATCH`), aplicado em `list_current_offer_links_for_mission`
(`offers/query.py:430-481`, filtro na linha 466) — usado por
`get_mission` (`backend/app/webapp/missions_router.py:666-689`) e
exposto como `MissionDetailResponse.offers`. Mesmo filtro se repete em
`list_user_offers` (`GET /api/v1/offers`) e `count_relevant_offers_
by_mission`. Nenhum desses pontos tem checagem de role — mas também
nenhum deles devolve `NO_MATCH` para ninguém, USER ou DEV.

Ou seja: **hoje não existe nenhuma tela que mostre TODOS os itens
pesquisados** (incluindo os classificados como `NO_MATCH` — os que a
coleta encontrou mas descartou por não bater com o produto pedido).
Esta TASK não é "restringir algo que hoje vaza para todo mundo" — é
**criar uma superfície nova**, DEV-only desde o início, mostrando o
conjunto completo (incluindo `NO_MATCH`) que hoje só existe no banco,
nunca exposto por nenhum endpoint.

## Padrão de gating DEV já existente (a reaproveitar)

A área "minhas pesquisas" já resolve exatamente esse tipo de
visibilidade restrita, mesmo padrão a seguir aqui:

- `Permission.DEV_PANEL_ACCESS` (`backend/app/authorization/
  policy.py:39-43`).
- Dependência `require_dev_web_session` (`backend/app/webapp/
  dependency.py:94-108`), que chama `authorize(session, user,
  Permission.DEV_PANEL_ACCESS)`.
- Uso real: `get_all_search_history` (`GET /history/all`,
  `backend/app/webapp/search_router.py:265-278`), com
  `user: User = Depends(require_dev_web_session)`.
- Frontend: `frontend/src/pages/dev/AllSearchesPage.tsx` (tela real),
  gating de rota via `RequireDev`
  (`frontend/src/components/ProtectedRoute.tsx:38-51`, usa `isDev` de
  `useAuth()`; o próprio arquivo documenta que essa é só proteção de
  UX, a proteção real fica no backend), item de menu condicionado a
  `isDev` em `frontend/src/components/navGroups.ts:20-21,34`
  (`DEV_NAV`, rota `/app/dev/pesquisas`).

## Objetivo

Novo endpoint DEV-only (mesmo padrão `require_dev_web_session`) que
devolve **todas** as ofertas coletadas numa missão/rodada, sem filtrar
por `ACCESSIBLE_RELEVANCE` — incluindo `NO_MATCH` e o motivo/evidência
da classificação, quando existir. Nova tela DEV (mesmo padrão de
`AllSearchesPage.tsx`, novo item em `DEV_NAV`) para visualizar essa
lista completa por missão.

## Escopo (a definir na implementação — perguntas em aberto)

- Granularidade: por missão (todas as rodadas) ou por
  `CollectionRun`/rodada específica.
- Quais campos exibir por item (título bruto, loja, preço, relevância,
  e por que foi classificado como `NO_MATCH` quando aplicável — se
  esse motivo já é registrado em algum lugar consultável, a confirmar
  na implementação).
- Se cabe reaproveitar `list_current_offer_links_for_mission` com um
  parâmetro que desliga o filtro (só quando DEV) ou se é mais limpo uma
  função de query nova, dedicada, para não arriscar essa flag vazar
  para o caminho USER por engano.

## Fora de escopo

Mudar o que USER/ADMIN já veem hoje no detalhe de missão — esse
comportamento já está correto (filtrado), não precisa de nenhuma
alteração.

## Validação futura

Confirmar que um usuário USER (ou ADMIN sem DEV) recebe 403/404 ao
tentar acessar o novo endpoint, e que um usuário DEV vê itens
`NO_MATCH` reais numa missão que sabidamente teve descartes (ex.: os
casos já documentados na TASK-122, Terabyte/Mercado Livre retornando
sugestões/itens irrelevantes que a camada de relevância descartou).
