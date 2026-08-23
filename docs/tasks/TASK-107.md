# TASK-107 — Cotas e capacidade por usuário

Status: **Formalizada (planejamento); aguardando aprovação. Nenhum código escrito.**

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
