# TASK-126 — Ver TODOS os itens pesquisados (incluindo os descartados), de todos os usuários, direto na tela de Ofertas

Status: **Concluída (2026-09-24)** — backend, frontend e testes
prontos, ainda não commitada.

**Três revisões de desenho no mesmo dia, cada uma corrigida pelo
usuário antes da anterior estar certa** — registradas aqui para nunca
repetir os mesmos erros:

1. Primeira tentativa: tela DEV separada, escolha de missão por ID
   colado manualmente. Rejeitada — "eu não quero ter que procurar pelo
   ID... e mostre os mesmos cards que aparece para os usuários".
2. Segunda tentativa: aba dentro da própria `MissionDetailPage`
   (mission_id já vem da URL, sem precisar colar nada). Rejeitada
   também — "não quero nada dentro de missão, quero uma aba a parte de
   tudo".
3. **Desenho final** (autorizado): nada de tela nova nem escolha de
   missão — o próprio usuário explicou o modelo mental certo: "o gg
   cria o card com o que foi feito, esse card é exibido apenas para o
   dono da missão, como eu sou o dono do site quero que tudo de todos
   os usuários apareçam pra mim". Ou seja, não é uma feature de
   missão — é a tela **"Ofertas"** (`/app/offers`, já existente, já
   cheia de `OfferCard`) ganhando um filtro DEV-only, desligado por
   padrão: "pode por tudo dentro de ofertas mas deixa com filtro
   desligado pra mostrar as de outros users, aí se eu ativar ele
   aparece todas as pesquisas de todos os usuários".

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

## Padrão de gating DEV já existente (referência, não usado no fim)

A área "minhas pesquisas" tem o padrão DEV-only mais próximo já
existente no código (`Permission.DEV_PANEL_ACCESS`,
`backend/app/authorization/policy.py:39-43`; dependência
`require_dev_web_session`, `backend/app/webapp/dependency.py:94-108`;
uso real em `GET /history/all`, `search_router.py:265-278`) — mas o
desenho final **não usa essa dependência**: como o filtro fica dentro
do MESMO endpoint que USER já chama (`GET /api/v1/offers`), a checagem
de `Permission.DEV_PANEL_ACCESS` é feita inline com `authorize()`
(mesmo padrão que `Permission.MISSION_READ` já usava ali), só quando
`all_users=true` é pedido — sem precisar de uma dependência FastAPI
separada nem de uma rota nova.

## Objetivo (final)

`GET /api/v1/offers` (a mesma listagem que a tela "Ofertas" já usa)
ganha um parâmetro `all_users: bool = False`. Desligado (padrão):
comportamento idêntico a sempre, zero mudança para qualquer usuário.
Ligado: exige `Permission.DEV_PANEL_ACCESS` (403 `dev_access_denied`
se não for DEV) e troca a fonte de dado de `list_user_offers` (posse +
`ACCESSIBLE_RELEVANCE`) para `list_all_offers_dev` (qualquer usuário,
qualquer classificação — `NO_MATCH` incluso). Mesmos `OfferSummaryOut`/
`OfferCard` nos dois casos — a tela nem precisa saber qual fonte foi
usada.

## Implementação

- **Uma linha por Offer, não por vínculo de missão**: uma oferta pode
  ter `MissionOfferRelevance` de mais de uma missão (às vezes com
  classificações diferentes) — em vez de multiplicar linhas por
  vínculo, a consulta DEV mostra a classificação do vínculo MAIS
  RECENTE (`classified_at` desc), mesmo padrão de "última observação
  vence" já usado para preço na mesma consulta. Mantém a lista com uma
  linha por Offer, igual à tela normal.
- **Campo novo, nunca inventado no modo normal**:
  `OfferSummaryOut.classification` (`match`/`possible_match`/
  `no_match`/`null`) — `null` sempre que `all_users` não foi pedido
  (nenhum vazamento de dado DEV-only pelo caminho comum).
- Cupom, imagem, rating, filtros (busca/loja/condição/disponibilidade/
  ordenação) e paginação continuam funcionando IDÊNTICOS nos dois
  modos — é a mesma função `_as_summary`/o mesmo bloco de cupom, só a
  origem dos itens muda.

### Backend

- `backend/app/offers/query.py`: `AllOfferSummaryDev` (dataclass,
  estende `UserOfferSummary` + `classification`), `_any_relevance_
  exists()` (contraparte deliberadamente mais permissiva de
  `_accessible_offer_exists` — qualquer vínculo, qualquer classificação,
  qualquer usuário), `_all_offers_statement_dev` (mesmos filtros/
  ordenação de `user_offers_statement`, duplicados de propósito — nunca
  reaproveitados com uma flag para não arriscar essa exceção vazar pro
  caminho USER por engano) e `list_all_offers_dev`.
- `backend/app/webapp/offers_router.py`: `list_offers` ganha o param
  `all_users`; `authorize(session, user, Permission.DEV_PANEL_ACCESS)`
  inline só quando `True`; `OfferSummaryOut.classification` (default
  `None`); `_as_summary` ganha o param `classification`.

### Frontend

- `frontend/src/api/types.ts`: `OfferSummary.classification`.
- `frontend/src/api/offers.ts`: `OfferListFilters.all_users`.
- `frontend/src/pages/offers/OffersListPage.tsx`: checkbox "Ver de
  todos os usuários, incluindo descartados (DEV)", visível só quando
  `useAuth().isDev` — mesma tela, mesmo `OfferCard`, sem rota nova, sem
  item de menu novo.
- **Nada tocado** em `MissionDetailPage.tsx` nem em nenhuma rota/nav
  nova — as duas tentativas anteriores (tela separada, aba de missão)
  foram completamente revertidas antes deste desenho.

## Fora de escopo

Mudar o que USER/ADMIN já veem hoje quando `all_users` não é passado
— comportamento idêntico a antes desta TASK, provado pelos testes
abaixo. Badge/indicador visual de classificação no `OfferCard` em si —
não pedido; o card continua exatamente igual ao que USER vê, só a
lista por trás fica maior quando o filtro DEV está ligado.

## Validação

- `tests/test_webapp_offers_router.py`: 3 testes novos —
  `test_list_offers_all_users_requires_dev_role` (USER + `all_users=
  true` → 403 `dev_access_denied`, `list_all_offers_dev` nunca chamada),
  `test_list_offers_all_users_as_dev_exposes_no_match_classification`
  (DEV + `all_users=true` → usa `list_all_offers_dev`, `NO_MATCH`
  aparece no JSON), `test_list_offers_default_never_exposes_
  classification` (sem `all_users` → `classification` sempre `null`).
  Nova fixture `client_as_dev`. 27 testes no arquivo, todos passando.
- `tests/integration/test_offers_all_users_dev.py` (nova, banco real):
  `test_dev_all_users_view_includes_no_match_and_other_users_offers` —
  prova as duas coisas juntas (`NO_MATCH` de uma missão + oferta de
  OUTRO usuário) aparecendo em `list_all_offers_dev` e ausentes em
  `list_user_offers` do dono original. 39 testes passando junto com
  `test_offer_relevance_eligibility.py` +
  `test_offer_installment_options.py` + `test_offer_price_history.py`
  (mesma área, sem regressão), via `scripts/run_integration_tests.py`.
- Frontend: `npx tsc -b` limpo, `npm run lint` (oxlint) limpo.
- `ruff check`/`format`: limpos em todos os arquivos Python tocados.

## Furo encontrado e corrigido depois (2026-09-25, junto com a TASK-127)

Com o filtro "todos os usuários" ligado, clicar em "Ver detalhes" num
card de outro usuário (ou num `NO_MATCH`) dava "você não tem acesso": o
detalhe, a comparação e o gráfico continuavam usando só as consultas
`*_for_user` (dono + `ACCESSIBLE_RELEVANCE`). Achado durante o briefing
da TASK-127 (cujo botão fica justamente no detalhe) e corrigido por
decisão do usuário ("Sim, corrige junto").

- `backend/app/offers/query.py`: sentinela explícita `DEV_VIEWER`
  (nunca `None` — um `user_id` ausente por bug jamais vira acesso
  total) + `_viewer_access_clause`; núcleos privados `_get_offer_detail`/
  `_get_offer_comparison`/`_get_offer_price_history` e wrappers
  `get_offer_detail_for_dev`/`get_offer_comparison_for_dev`/
  `get_offer_price_history_for_dev`. As funções `*_for_user` mantêm a
  mesma assinatura e a mesma regra; a listagem USER
  (`user_offers_statement`) nem foi tocada.
- `backend/app/webapp/offers_router.py`: detalhe, comparação e gráfico
  tentam primeiro a consulta de dono; só se não achar e o usuário tiver
  `Permission.DEV_PANEL_ACCESS` (checagem pura por papel — nunca gera
  auditoria de negação para USER) caem na consulta DEV. Frontend não
  precisou mudar.
- Testes: `test_dev_opens_offer_outside_own_missions_user_still_denied`
  (unitário) e `test_dev_opens_other_users_no_match_offer_detail_
  comparison_and_history` (integração, banco real).
