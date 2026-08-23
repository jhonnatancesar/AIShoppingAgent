# TASK-107 — Cotas e capacidade por usuário

Status: **Concluída e publicada em `origin/main` (`ac34725`/`faa939c`/`83a9572`/`d61951a`).**

## Implementação (2026-08-22)

- Novo módulo `app/quotas/` (`models.py`: `SearchReceipt`;
  `service.py`: `QuotaLimits`/`QuotaUsage`/`resolve_quota_limits`/
  `get_quota_usage(_async)`/`check_mission_activation_quota(_async)`/
  `check_and_reserve_search_quota_async`/`QuotaExceededError`).
- Integrado em `transition_mission`/`transition_mission_async`
  (`ACTIVATE`/`RESUME`) — cobre também `create_mission_from_criteria`
  via ativação automática, sem precisar duplicar a checagem lá. Edição de
  lojas de missão `ACTIVE` **não existe** no código real
  (`edit_mission_criteria` só edita missão `PAUSED`) — a suposição
  original do documento estava errada; `PAUSE → editar → RESUME` já cobre
  o caso via a checagem no `RESUME`.
- `search_router.py`: reserva `max_daily_searches` antes de executar a
  pesquisa, 429 estruturado se excedido.
- `GET /api/v1/account/quota` (novo, `account_router.py`): uso/limite/
  aviso de proximidade dos três limites, mais `daily_searches_reset_at`.
- `PATCH /api/v1/admin/users/{id}` (`admin_router.py`): três campos de
  override tri-state (ausente = não mexe; `null` = limpa; valor = define),
  auditado como o resto do endpoint.
- Migration `20260822_0010`: 3 colunas nullable em `users` + tabela
  `search_receipts`.
- Testes: `tests/test_quotas_service.py` (novo, unitário) +
  `test_mission_transitions.py`/`test_mission_creation.py`/
  `test_mission_service_async.py` atualizados para a nova sequência de
  checagem. 120 testes diretamente afetados passando; suíte de
  integração (Postgres real) revisada por volume de missões/fontes por
  usuário nos fixtures, não executada nesta rodada (sem Postgres
  disponível na sessão). Achado à parte, não relacionado a esta TASK:
  `test_create_mission_rejects_unknown_source_code` está desatualizado
  desde a TASK-104A (usa `"magalu"`, hoje uma loja válida) — sinalizado
  como task separada, não corrigido aqui.

## Frontend (2026-08-22)

- `QuotaSummaryCard`/`QuotaUsageRow` (`components/QuotaSummary.tsx`):
  missões ativas, lojas monitoradas e pesquisas hoje, sempre com
  uso/limite; aviso visual quando `near_limit` (já calculado pelo
  backend). Exibido na Minha Conta via novo `accountApi.getQuota()`.
- `QuotaExceededNotice`/`quotaDetailsFromError`
  (`components/QuotaExceededNotice.tsx`): traduz `ApiError.details`
  (kind/limit/current/actions) em mensagem real + botões de ação
  (reduzir lojas, pausar, cancelar, gerenciar missões) — nunca erro
  genérico. Integrado em criação de missão, `resume` (detalhe da
  missão) e pesquisa.
- Pesquisa (`ProductSearchPage.tsx`): mostra uso diário sempre visível;
  ao bater a cota, mensagem clara ("Você já fez X/Y pesquisas hoje"),
  sem botão de ação (só esperar a renovação).
- ADMIN (`AdminHome.tsx`): edição tri-state dos três overrides por
  usuário, reaproveitando o `PATCH` já existente.
- Teste novo `tests/quota-components.mjs` (render de uso + ações da
  cota excedida). `npm run lint`/`npm run build` limpos; suíte
  scriptada existente (account/search/offer/offers) continua passando.

## Pendente

- Suíte de integração real (Postgres) não executada nesta rodada.

## Objetivo

Limitar capacidade por USER (missões ativas, lojas monitoradas, pesquisas
diárias) com UX transparente de uso/limite, sem nunca pausar ou cancelar
nada automaticamente, e preparado para planos futuros (FREE/PLUS/PRO) sem
implementá-los agora (`DEC-073`).

## Defaults iniciais (USER)

- `max_active_missions = 5`
- `max_store_slots = 18`
- `max_daily_searches = 30`

## Regra de consumo

- 1 loja monitorada por uma missão `ACTIVE` = 1 store slot. Uma missão
  `ACTIVE` monitorando 3 lojas consome 3 slots.
- `ACTIVE` consome missão (1 de `max_active_missions`) **e** slots (N de
  `max_store_slots`, N = quantidade de `MissionSource` da missão).
- `PAUSED`/`CANCELLED`/`EXPIRED`/`COMPLETED` não consomem nada — os slots
  voltam a ficar disponíveis assim que a missão sai de `ACTIVE`.
- `RESUME` (`PAUSED` → `ACTIVE`) revalida a quota antes de completar a
  transição — se não houver capacidade, o resume falha com o motivo
  explicado, missão continua `PAUSED`.
- Editar as lojas de uma missão `ACTIVE` (adicionar fonte) revalida
  `max_store_slots` antes de persistir a nova `MissionSource`.

### Base real no schema já existente

- Missões ativas do usuário: `Mission.user_id == user.id AND Mission.status
  == MissionStatus.ACTIVE` (`MissionStatus` já existe, string enum
  minúsculo: `draft`/`active`/`paused`/`completed`/`cancelled`/`expired`,
  `backend/app/missions/models.py`).
- Store slots consumidos: contagem de `MissionSource` (`mission_sources`,
  PK composta `(mission_id, store_id)`) **apenas das missões do usuário
  com `status == ACTIVE`** — diferente da contagem já existente em
  `orchestration.py::_mission_prelist_round_complete`, que conta todas as
  `MissionSource` de uma missão sem filtrar por status (essa contagem
  serve a outro propósito e não deve ser reaproveitada diretamente aqui
  sem o filtro de `ACTIVE`).
- `max_daily_searches`: não existe hoje nenhuma infraestrutura de
  contagem no endpoint de busca (`GET /api/v1/product-search`,
  `backend/app/webapp/search_router.py` → `search_persisted_products`,
  100% read-only, sem rate-limit). O padrão mais próximo já existente no
  projeto para "contar e limitar por janela de tempo, por usuário,
  persistido, transacional" é `reserve_telegram_update`
  (`backend/app/telegram/limits.py`) — conta recibos aceitos na última
  janela (hoje 1 minuto) e persiste a decisão antes de aceitar a próxima.
  Proposta: mesmo princípio, janela de 1 dia em vez de 1 minuto,
  mecanismo de persistência exato (tabela própria de recibo de pesquisa
  vs. contador agregado por dia) fica para a implementação.

## UX obrigatória

O USER deve sempre conseguir ver, num único lugar (proposta: extensão da
área "Minha conta", `app/webapp/account_router.py`, TASK-101):

```text
Missões ativas: 3 / 5
Lojas monitoradas: 12 / 18
Pesquisas hoje: 18 / 30
```

- **Aviso antes do limite:** ao atingir um limiar de proximidade (proposta:
  80% de uso — número exato aberto pra ajuste), avisar de forma visível,
  não bloqueante.
- **Ao bater a quota:** nunca só "limite excedido". Sempre explicar o
  motivo e oferecer ações contextuais:
  - **`max_store_slots`** (ex.: "Você usa 18/18 lojas de monitoramento"):
    reduzir lojas de uma missão existente; pausar uma missão para liberar
    capacidade; cancelar uma missão e criar outra; voltar para Minhas
    missões.
  - **`max_active_missions`**: pausar uma missão; cancelar uma missão;
    gerenciar missões.
  - **`max_daily_searches`**: informar uso atual e quando a quota volta a
    ficar disponível (reset diário) — nunca criar missão/pesquisa
    escondida como contorno.
- **Nunca pausar/cancelar automaticamente** — toda liberação de capacidade
  é ação explícita do próprio usuário.

## DEV/ADMIN

Painel já tem edição por usuário (`PATCH /api/v1/admin/users/{user_id}`,
`UpdateUserRequest`, `backend/app/webapp/admin_router.py`, hoje só
`role`/`lifecycle_status`). Proposta: estender com overrides opcionais de
quota por usuário (`NULL` = usa o default do sistema), mesma disciplina
de auditoria já aplicada em toda mutação admin (`_audit(...)` antes do
commit).

## Preparação para planos futuros (sem implementar)

Consistente com `DEC-073` (planos FREE/PLUS/PRO permanecem na V2, sem
`user_roles`/multi-papel agora): os defaults ficam como constantes de
sistema (`Settings`, mesmo padrão de `Field(default=..., ge=..., le=...)`
já usado para `collection_max_concurrency`/`circuit_failure_threshold`),
com o override por usuário do painel ADMIN como único mecanismo de
diferenciação hoje. Isso já deixa o caminho aberto para um futuro sistema
de planos aplicar valores diferentes por tier, sem precisar redesenhar o
mecanismo de verificação/consumo de quota.

## Onde a verificação precisa entrar (proposta, sem implementar)

- Transição de missão para `ACTIVE` (criação direta como `ACTIVE` ou
  comando `activate`) — mesma camada que já valida transições atômicas
  (`MissionTransition`, TASK-021).
- Comando `resume` (`PAUSED` → `ACTIVE`).
- Edição de lojas de uma missão `ACTIVE` (adicionar `MissionSource`).
- Endpoint de pesquisa pela Web (`search_router.py`) para
  `max_daily_searches`.

## Validação mínima futura

- `max_active_missions`/`max_store_slots` calculados corretamente a
  partir de `Mission`/`MissionSource` reais, só contando `ACTIVE`;
- `RESUME` e edição de lojas revalidam e falham com motivo claro quando
  não há capacidade;
- `PAUSED`/`CANCELLED`/`EXPIRED`/`COMPLETED` nunca contam para quota;
- nenhuma pausa/cancelamento automático em nenhum fluxo;
- UX mostra uso/limite sempre, avisa perto do limite, explica motivo e
  oferece ações reais ao bater a quota;
- override de quota pelo DEV/ADMIN funciona e é auditado.

## Fora de escopo

Sistema de planos FREE/PLUS/PRO, `user_roles`/multi-papel, RBAC avançado
(permanecem na V2, `DEC-073`), implementação de código nesta rodada.
