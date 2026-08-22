# TASK-092 — Área USER: gerenciamento de missões pela web (item 2 da V1.2)

Status: **Implementada, validada e aprovada pelo usuário (2026-08-22).**

## Origem

Item 2 da ordem de execução da V1.2 (`docs/internal/v1.2-scope.md`),
pré-requisito atendido pela TASK-091 (fundação da aplicação web). Leva
para `/app` as operações de missão que hoje existem principalmente pelo
Telegram: criar, listar, ver detalhes, editar, pausar, retomar, cancelar,
ver status. O Telegram continua existindo; a web é uma segunda forma de
controlar as **mesmas** missões, reaproveitando o domínio já existente
(`app.missions`) — nunca um segundo sistema de missões.

## Objetivo

Endpoints `/api/v1/missions*` autenticados por `WebSession`
(`require_web_session`, TASK-091) que chamam diretamente o serviço de
missões assíncrono já existente (`app.missions.service`/`app.missions.query`),
mais as telas em `/app` (React/TypeScript) que os consomem — sem
duplicar nenhuma regra de negócio já implementada para o Telegram.

## Levantamento (feito antes desta TASK, para evitar decisões às cegas)

- Camada de serviço já é agnóstica de canal: `create_mission_from_criteria_async`,
  `transition_mission_async` (pausar/retomar/cancelar/ativar/completar/
  expirar, via `MissionCommand`, com concorrência otimista por
  `expected_state_version`), `edit_mission_criteria` (só em missão
  `PAUSED`) — nenhuma tem lógica específica de Telegram.
- Criação **não exige IA**: o caminho estruturado
  (`search_query`/`model`/`target_amount`/`target_currency`/`source_codes`)
  já é exatamente o que `_execute_create_mission` do Telegram chama depois
  de interpretar o texto livre — um formulário web preenche os mesmos
  campos diretamente, sem `IntentInterpreter`.
- Máquina de estados (`MissionStatus`/`MissionCommand`) já é centralizada
  em `service.TRANSITIONS`; toda transição fica registrada em
  `MissionTransition` (auditoria/histórico já pronta para exibir na UI).
- Permissões (`MISSION_CREATE`/`MISSION_READ`/`MISSION_TRANSITION`/
  `MISSION_EDIT`) já cobrem o papel `USER`; posse de missão sempre via
  `mission.user_id == user.id` + `deny_resource_unavailable` (nunca
  distingue 404 de "não é sua" na resposta).
- Não existe hoje dependência de sessão assíncrona genérica para routers
  da webapp (`webapp/router.py` usa `Session` síncrona); as funções de
  missão são só assíncronas. Precisa de uma dependência nova
  (`get_async_session`-equivalente, não amarrada ao Telegram) — decisão
  técnica, não architectural, resolvida durante a implementação.
- Não existe hoje uma consulta única "detalhe da missão" (missão +
  critério + fontes + agenda); será uma função nova em `app.missions.query`
  (mesma camada, não um sistema novo).

## Decisões de preflight (usuário, 2026-08-22)

1. **Cancelamento:** exige diálogo de confirmação no cliente (React)
   antes do `POST .../cancel` — só UX, nenhuma mudança de backend/domínio.
   Pausar/retomar continuam diretos (reversíveis, sem confirmação).
2. **Listagem padrão:** ativas + pausadas por padrão (equivalente a
   `list_visible_missions_for_user`), com filtro de status na tela
   cobrindo pelo menos: ativas, pausadas, canceladas, concluídas,
   expiradas, todas. `GET /api/v1/missions?status=...` implementa isso
   como filtro único (`active|paused|cancelled|completed|expired|all`;
   ausente = padrão ativas+pausadas).

## Escopo

- `POST /api/v1/missions` — criar (campos estruturados, sem IA).
- `GET /api/v1/missions` — listar (com filtro de status, conforme
  preflight).
- `GET /api/v1/missions/{mission_id}` — detalhe (missão + critério +
  fontes + histórico de transições).
- `PATCH /api/v1/missions/{mission_id}` — editar critério (só em
  `PAUSED`, mesma regra do Telegram).
- `POST /api/v1/missions/{mission_id}/pause` — pausar.
- `POST /api/v1/missions/{mission_id}/resume` — retomar.
- `POST /api/v1/missions/{mission_id}/cancel` — cancelar.
- Telas React/TypeScript em `/app`: lista, formulário de criação, detalhe
  com ações (pausar/retomar/cancelar/editar).
- Todos os endpoints autenticados por `require_web_session` (sessão +
  CSRF automáticos, TASK-091); autorização por `Permission.MISSION_*`
  já existente.

## Fora de escopo

- Qualquer mudança no Telegram, no `IntentInterpreter`, na coleta ou na
  máquina de estados de missão — esta TASK só adiciona uma segunda
  interface sobre o domínio já existente.
- Gráficos/histórico de preço, comparação entre lojas, avaliações (itens
  posteriores da V1.2).
- Painel DEV/ADMIN (`/admin`) — fora desta TASK.
- `user_roles`, múltiplos papéis, RBAC avançado (V2, `DEC-073`).

## Critérios de aceite

1. Criar, listar, ver detalhe, editar (só `PAUSED`), pausar, retomar e
   cancelar missão funcionam pela web, chamando só o serviço já existente
   (nenhuma regra de negócio duplicada).
2. Toda mutação exige `WebSession` válida + CSRF (herdado de
   `require_web_session`, testado com tentativa direta sem cookie/CSRF).
3. Um usuário nunca vê nem edita missão de outro (testado com tentativa
   direta de acesso, não só ausência de link na interface).
4. Pipeline oficial completo aprovado; validação real contra PostgreSQL e
   navegador real (não só testes unitários/mock).

## Resultado (2026-08-22)

Todos os 4 critérios de aceite validados.

### Backend

- `backend/app/webapp/missions_router.py` (novo): `POST/GET /api/v1/missions`,
  `GET/PATCH /api/v1/missions/{id}`, `POST .../pause|resume|cancel` -- só
  chamam `app.missions.service`/`app.missions.query`; nenhuma regra de
  negócio nova. Todos autenticados por `Depends(require_web_session)`
  (TASK-091/DEC-074): sessão + CSRF automáticos em métodos mutáveis.
  Posse de missão via `session.get` + `mission.user_id == user.id` +
  `deny_resource_unavailable`, mesmo padrão do Telegram -- nunca distingue
  "não existe" de "não é sua".
- `app.missions.query` ganhou 3 funções novas (mesma camada, não um
  sistema novo): `list_missions_for_user_by_status`/
  `count_missions_for_user_by_status` (filtro por status + paginação
  `limit`/`offset`, envelope `items/limit/offset/total` conforme
  `docs/development/api-conventions.md`) e `get_mission_detail_for_user`
  (composição missão + critério + fontes + agenda + histórico, já que
  `app.missions.models` não declara `relationship()` nenhum).
- `app.database.dependency` ganhou `get_web_async_session` -- reaproveita
  o mesmo engine assíncrono de processo do webhook Telegram
  (`get_telegram_async_engine`), mas comita automaticamente no sucesso
  (como `get_session`): os endpoints web não têm `await` de I/O externo
  no meio da transação, então não precisam do controle manual de fase que
  o Telegram precisa.
- **Correção lateral encontrada na validação:** `create_mission_from_criteria(_async)`
  registrava a transição inicial `draft→active` sempre com
  `actor_type="telegram"` hardcoded, mesmo quando chamada pela web --
  auditoria incorreta. Corrigido com um parâmetro `actor_type: str =
  "telegram"` (default preserva todo chamador existente); o endpoint web
  passa `actor_type="web"`. Confirmado no container real: transição
  inicial de uma missão criada pela web agora mostra `"actor_type":"web"`.
- Nenhuma migration nova -- confirmado (`scripts/check.ps1`, grafo de
  migrações inalterado em `20260821_0001`).

### Frontend

- `frontend/src/api/missions.ts` (cliente tipado dos 7 endpoints),
  `frontend/src/pages/missions/` (`MissionsListPage`, `MissionCreatePage`,
  `MissionDetailPage` com formulário de edição embutido quando `paused`).
  Cancelamento pede confirmação no cliente (`window.confirm`, decisão de
  preflight); pausar/retomar diretos.
  `frontend/src/api/client.ts` ganhou `api.patch`.

### Testes

- `tests/test_mission_query.py`: +8 testes para as 3 funções novas de
  `app.missions.query` (mock de `AsyncSession`).
- `tests/test_webapp_missions_router.py` (novo, 19 testes): contrato
  HTTP completo -- 401 sem sessão, 403 sem CSRF, 403 de posse (mission
  inexistente e de outro usuário tratados igual), 422 de validação,
  409 de conflito de versão/condição de transição, 500 mapeado sem
  vazar detalhe interno (`MissionCreationError`).
- `tests/integration/test_webapp_missions.py` (novo, 5 testes): as 3
  funções novas de `query.py` contra PostgreSQL real, incluindo prova de
  isolamento entre usuários e de que uma missão criada pelo mesmo caminho
  que o endpoint usa (`create_mission_from_criteria_async`) aparece
  imediatamente na listagem padrão.

### Pipeline e validação real

`scripts/check.ps1`: 1397 testes unitários + cobertura 90,41%, grafo de
migrações inalterado, Docker Compose config aprovado, 62 testes de
integração PostgreSQL (55 já existentes + 5 novos, mais 8 de
`test_mission_query.py` que já contavam no total de unitários).
`ruff check .` limpo. `git diff --check` sem erro. `npm run lint`/`npm
run build` (com `tsc -b`) aprovados.

`docker compose build`/`up` reais (imagem reconstruída duas vezes -- uma
antes e uma depois da correção de `actor_type`), validação funcional
completa contra Postgres containerizado: login → criar missão (com
preço-alvo e lojas) → listar (padrão ativas+pausadas) → ver detalhe →
pausar → editar critério → retomar → cancelar → some da listagem padrão
→ aparece com `?status=cancelled`; conflito de versão (`409`) reproduzido
de propósito e confirmado semanticamente correto (edição não incrementa
`state_version`, só transição de ciclo de vida incrementa); isolamento
entre dois usuários reais confirmado (`403` tanto para ver quanto para
mutar a missão do outro); deep links `/app/missions`, `/app/missions/new`,
`/app/missions/{id}` servidos corretamente pela SPA (`200`, sem tocar na
whitelist -- primeiro segmento continua `app`).

### Limitações reais restantes

Nenhuma bloqueante identificada. Fica fora do escopo desta TASK (V1.2,
itens 3+): gráficos/histórico de preço, comparação entre lojas,
avaliações, painel DEV/ADMIN.

### Auditoria arquitetural (2026-08-22, segunda rodada) — `DEC-075`

O usuário revisou a entrega acima e não aprovou o commit, pedindo uma
auditoria formal de 21 pontos (concorrência, posse, contrato de listagem,
UX de conflito, agnosticismo de canal, cobertura de teste real). Resultado
completo registrado em `DEC-075` (addendum); resumo abaixo, sem apagar o
"Resultado" original.

**Dois bugs reais encontrados e corrigidos:**

1. `edit_mission_criteria` checava `expected_state_version` mas nunca
   incrementava `state_version` no sucesso — uma segunda edição concorrente
   na mesma versão não era rejeitada (perda silenciosa de escrita). Corrigido:
   `state_version` agora cobre a missão inteira (edição de critério + toda
   transição de ciclo de vida), não só o lifecycle. Prova: teste de
   integração real com dois clientes editando a partir da mesma versão —
   o segundo recebe `409`, o primeiro persiste sozinho
   (`test_lost_update_is_prevented_by_state_version`).
2. `_run_command` do router não capturava `InvalidMissionTransitionError`
   — um comando inválido para o estado atual (ex.: `resume` em missão
   `cancelled`) produzia `500` sem detalhe em vez de `409`. Corrigido.

**Decisões formalizadas (já corretas na prática, agora explícitas e testadas):**

- **Posse:** nova função `get_mission_for_user` (`app.missions.query`),
  reaproveitada pelo router web — "não existe" e "não é sua" retornam a
  mesma resposta (`403 mission_access_denied`), nunca distinguidos.
  Telegram continua com seus próprios pontos de chamada (não migrado nesta
  TASK, decisão explícita).
- **`actor_type` obrigatório** (sem default) em
  `create_mission_from_criteria(_async)` — o default anterior mascararia
  silenciosamente um chamador futuro que esquecesse de passá-lo; todo o
  resto do código-base já segue essa convenção.
- **Contrato de listagem:** `limit`/`offset` (padrão 20, máximo 100),
  ordenação estável por `updated_at DESC`, testado para os 6 filtros de
  status (`expired` alcançado via transição real `EXPIRE`, nunca seed
  direto).
- **UX de `409`:** a SPA nunca força a mudança nem ignora o conflito —
  mostra mensagem explicativa, recarrega os dados, obriga revisão antes de
  tentar de novo (`MissionDetailPage.tsx`).

**Validação real em container (pós-correções):** `docker compose build
api` + stack com Postgres descartável, migrações até `20260821_0001` (sem
migration nova), dois usuários descartáveis. Confirmado via HTTP direto:
ciclo completo criar→pausar→editar (`state_version` 2→3)→retomar→cancelar;
edição em `ACTIVE` rejeitada (`409`); `resume`/`pause` em `CANCELLED`
rejeitados com `409` (não `500`); posse indistinguível confirmada
byte-a-byte entre "inexistente" e "de outro usuário"; `409` de versão
obsoleta reproduzido de propósito; filtro padrão exclui cancelada,
`?status=cancelled`/`?status=all` incluem. Confirmado em navegador real:
login, estado vazio ("nenhuma missão encontrada para este filtro"),
redirecionamento para login quando não autenticado, lista populada, troca
de filtro pela interface. Container e volume descartáveis removidos ao
final; nenhum dado de teste ficou para trás.

**Pipeline:** `scripts/check.ps1` — 1414 testes unitários (cobertura
90,42%), grafo de migrações inalterado, Docker Compose config aprovado, 73
testes de integração PostgreSQL (62 anteriores + 11 novos desta rodada).
`ruff check .` e `git diff --check` limpos.

### Ajuste final antes do commit (2026-08-22, aprovação do usuário)

Ordenação de `list_missions_for_user_by_status` corrigida de `created_at
DESC` para `updated_at DESC, id DESC` -- pausar/retomar/editar/cancelar
devem subir a missão na lista, não só criá-la; `id DESC` desempata
`updated_at` colidido, para paginação estável por `offset`. Teste
dedicado adicionado; pipeline completo reexecutado e aprovado (1414
testes unitários/90,42%, 73 de integração PostgreSQL). Detalhe completo
em `DEC-075`.

### Conclusão

**TASK-092 aprovada pelo usuário para commit** (2026-08-22).
