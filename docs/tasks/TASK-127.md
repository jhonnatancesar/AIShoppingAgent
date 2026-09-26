# TASK-127 — Exibir preço histórico (interno + de referência) para todos + botão para buscar sob demanda

Status: **Implementada e testada (2026-09-25)** — backend, frontend e
testes prontos, ainda não commitada. Validação visual no navegador e em
PROD pendentes (ver "Validação" e "Ação no deploy").

## Contexto real (mecanismo já existia, nunca era exposto)

O F1 (`historical_bootstrap`) já fazia praticamente o que foi pedido — a
lacuna real é que ele só rodava automaticamente dentro do fluxo de
coleta, e nada do que ele produz chegava a aparecer para o usuário:

- **Preço histórico interno** ("registrado no GG"):
  `get_internal_historical_best` (`backend/app/alerts/internal_
  history.py`) — `min(PriceObservation.amount)` só condição
  `NEW`/disponível/mesma moeda, PRODUCT-GLOBAL.
- **Preço histórico de referência** (externo):
  `get_external_price_reference_evidence` (`backend/app/historical_
  bootstrap/service.py`) — já devolve o MENOR preço entre todas as
  `ExternalPriceReference` coletadas para o produto.
- **As duas buscas pedidas já existiam** em `_collect_candidates`: uma
  específica no `hardwarebarato.com` (categorias `gpu/cpu/motherboard/
  psu/ram`) e uma genérica — "tanto pelo hardware barato quanto na
  internet em si". IA (`_interpret_ambiguous`) só como último recurso,
  com instrução anti-alucinação.

## Correção de premissa (confirmada no código)

O pedido presumia "o preço histórico só vai vir pra produtos de
missões novas". Na verdade o F1 roda para qualquer oferta `MATCH` de
missão **ativamente coletando** — a limitação real: produto de missão
pausada/encerrada nunca dispara, e o fluxo automático **pula** a busca
externa quando o histórico interno já é suficiente (30 dias + 2 lojas).
O botão resolve as duas lacunas.

## Nomenclatura

"Menor registrado no GG" (interno) e "Histórico de referência"
(externo) — mesmos rótulos do preview aprovado pelo usuário no briefing
de 2026-09-25.

## Decisões do usuário

- **2026-09-21**: os dois campos mostram o que JÁ existe no banco, sem
  ninguém clicar ("não adianta só por o campo, vai que tem item já com
  o preço e você não trouxe"). Sem busca recente → qualquer usuário
  busca; dentro da janela de 90 dias → USER bloqueado, só DEV força,
  com aviso explícito e as opções "Pesquisar mesmo assim" / "Cancelar".
- **2026-09-25 (briefing)**:
  1. Local: bloco "Preço histórico" no detalhe da oferta, logo acima do
     gráfico de histórico já existente.
  2. Se o sistema já buscou há menos de 90 dias e **não achou nada**,
     USER também não busca de novo — só DEV força.
  3. Corrigir junto o furo da TASK-126 (DEV abrir o detalhe de qualquer
     oferta vista no filtro "todos os usuários") — ver TASK-126.md.

(O item "botão visível somente para DEV" da versão original deste
documento foi substituído pela regra acima: o botão existe para todos,
o que muda por papel é o que cada um pode fazer.)

## Decisões técnicas (apresentadas no briefing, não contestadas)

- **Busca em segundo plano**: cada leitura de página pode levar até
  180s no César Core (`cesar_core_fetch_timeout_seconds`), então o
  `POST` só **reserva** a busca (transação curta) e responde `202`; a
  busca roda depois da resposta (`BackgroundTasks`) e a tela consulta o
  estado a cada 4s até sair de `in_progress` (teto de ~5 min).
- **Perfil de IA pelo papel real**: clique de USER usa o manager/cota
  USER; ADMIN/DEV usam o de ADMIN/DEV.
- **Modo manual ignora o gate "histórico interno suficiente"** (só o
  modo automático da coleta respeita).
- **Falha é sempre re-tentável** por ação humana (ignora `retry_after`),
  mas uma falha nunca "renova" a janela: vale o `completed_at` do
  último sucesso.
- **Nunca rouba uma busca em andamento** (lease ativo), nem com `force`.
- **Sem identidade → sem busca** (fail-closed): botão desabilitado com
  aviso. Inclui o backlog da TASK-123 — pras placas-mãe, o botão só
  funciona depois do backfill rodar em PROD.
- **Busca desativada** quando `historical_bootstrap_enabled` é `false`
  ou a credencial do César Core não existe — nunca uma reserva órfã.

## Implementação

### Backend

- `backend/app/historical_bootstrap/service.py`:
  - `_run_historical_bootstrap` separado em `_claim_bootstrap` (reserva)
    + `_execute_claimed_bootstrap` (busca/persistência) — o modo `auto`
    mantém exatamente o comportamento de sempre da coleta (33 testes de
    integração existentes passando sem alteração).
  - Modos `manual`/`manual_force` no `ON CONFLICT ... WHERE` do claim —
    a regra é reavaliada de forma atômica no banco (corrida entre dois
    cliques nunca gera duas buscas).
  - `ManualSearchAvailability` + `manual_search_availability` (regra
    pura, para a tela), `get_historical_bootstrap_state`,
    `claim_manual_historical_bootstrap`, `run_claimed_historical_
    bootstrap` (nunca propaga exceção).
- `backend/app/webapp/offers_router.py`:
  - `GET /api/v1/offers/{id}/historical-price` — interno, referência
    (valor, fonte, link, data) e estado da busca (`availability`,
    `last_status`, `last_completed_at`, `next_allowed_at`).
  - `POST /api/v1/offers/{id}/historical-price/search` (`{"force":
    bool}`) — `202` com a busca reservada, ou `409` com código
    específico (`historical_price_recently_searched`,
    `historical_price_force_required`, `historical_price_search_in_
    progress`, `historical_price_no_identity`, `historical_price_search_
    disabled`). `force` só vale para DEV.
  - Mesmo acesso do detalhe: dono da oferta, ou DEV em qualquer oferta.
- `backend/app/database/dependency.py`: `get_web_async_session_factory`
  (a busca em segundo plano não pode usar a sessão do request).

### Frontend

- `frontend/src/pages/offers/HistoricalPriceSection.tsx` (novo): bloco
  "Preço histórico" com os dois valores, o botão e todos os estados
  (buscando, já pesquisado em DD/MM com data de liberação, sem
  identidade, confirmação DEV de 90 dias). Mensagens inline (sem toast —
  o detalhe também é renderizado em SSR nos testes).
- `frontend/src/pages/offers/OfferDetailPage.tsx`: bloco inserido entre
  a comparação e o gráfico.
- `frontend/src/api/offers.ts` / `types.ts`: `historicalPrice`,
  `searchHistoricalPrice`, `HistoricalPriceResponse`.

## Fora de escopo

Lógica de busca/parsing/matching do F1 em si — reaproveitada como está.
TASK-125 (cupom no gráfico) — tarefa separada.

## Validação

- `tests/test_historical_bootstrap_service.py`: regra pura do botão
  (desativado, sem identidade, nunca buscado, falha com backoff futuro,
  lease ativo/vencido, conclusão recente com/sem preço e falha após
  sucesso recente, janela vencida) — 30 testes no arquivo.
- `tests/test_webapp_offers_router.py`: 8 testes novos (valores já
  coletados aparecem sem clique; desativado sem credencial; USER sem
  preço reserva e roda em segundo plano com IA de USER; USER bloqueado
  na janela mesmo pedindo `force`; DEV precisa confirmar e depois força
  com IA de ADMIN/DEV; sem identidade recusado; corrida nunca inicia
  segunda busca; furo da TASK-126) — 35 testes no arquivo.
- `tests/integration/test_historical_bootstrap_manual.py` (novo, banco
  real): busca manual roda mesmo com histórico interno suficiente;
  bloqueio na janela até forçar; falha re-tentável ignorando backoff;
  nunca rouba lease ativo; DEV abre detalhe/comparação/gráfico de oferta
  `NO_MATCH` de outro usuário — 5 testes.
- `tests/integration/test_historical_bootstrap.py` +
  `test_market_research.py`: 33 passando, sem alteração (caminho
  automático intacto).
- Frontend: `tsc -b`, `oxlint`, `test:offer`, `test:offers`,
  `test:layout`, `test:offer-card` passando.
- **Gate de cobertura unitária (90%, nunca abaixado)**: o passo
  unitário do pipeline oficial (`pytest --ignore=tests/integration
  --ignore=tests/e2e`) estava em **89,11%** com este trabalho — parte
  pelo código novo (coberto só pela integração) e parte por buracos que
  já existiam (lote de IA da TASK-123, listagens USER de ofertas). Uma
  tentativa de medir os commits anteriores em cópias isoladas do repo
  não serviu de comparação (o Edge dedicado dos testes, porta 9333, não
  sobe para uma segunda cópia — dezenas de falhas de ambiente). Gate
  restaurado com testes unitários de verdade, sem baixar nada:
  `tests/test_offers_query_dev.py` (caminhos DEV do `offers/query.py` +
  fiação da busca manual), `tests/test_offers_query_user.py` (listagens
  USER, ofertas por missão e ranking da comparação entre lojas), lote de
  extração de IA da TASK-123 em `tests/test_product_identity_ai.py`,
  fallback DEV da comparação/gráfico em `tests/test_webapp_offers_
  router.py` e guard `require_dev_web_session` em `tests/test_webapp_
  dependency.py`. Resultado: **90,04%**, 2695 passando, 0 falhas;
  `ruff check .`/`ruff format --check .` limpos.
- **Não verificado no navegador**: o stack local do GG está parado há 2
  semanas (imagem anterior a este código, banco sem as migrations
  recentes, login DEV necessário) — registrado honestamente, não
  presumido.

## Ação no deploy

- **O container `api` passa a chamar Search e Fetch do César Core**
  (antes só usava IA). Mesma credencial e mesma URL já configuradas
  (`AISHOPPING_CESAR_CORE_API_KEY_FILE`/`BASE_URL`), mas isso nunca foi
  exercitado em PROD — validar clicando no botão numa oferta sem preço
  de referência depois do deploy.
- O botão não funciona para produtos sem identidade (backlog da
  TASK-123) até o backfill rodar em PROD.
