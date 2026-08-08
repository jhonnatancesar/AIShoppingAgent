# TASK-043 — Implementar publicação de eventos

Status: Concluída

## Objetivo

Persistir de forma durável e append-only, na tabela `events`
(`docs/DATABASE.md`), qualquer evento validado pelo catálogo fechado da
TASK-042 (`app.events.catalog`). "Publicar" nesta TASK significa
exclusivamente validar e registrar o evento — a própria tabela é o log de
publicação. Não inclui message broker, worker, consumidor, notificação de
qualquer canal, nem qualquer parte da TASK-044.

## Contexto

TASK-036 ("Criar notificações Telegram") é a próxima tarefa executável pela
ordem do `docs/ROADMAP.md`, mas o preflight revelou que ela depende de um
pipeline real de eventos persistidos/publicados (`docs/PRICE_ALERTS.md`:
"Persistência e publicação pertencem à TASK-043, consumo à TASK-044 e
notificação Telegram à TASK-036") e de um `chat_id` persistido, nenhum dos
dois existente. O usuário confirmou implementar TASK-043 e TASK-044 antes
de retomar a TASK-036.

A exploração do código mostrou que **o único produtor real de candidatos de
evento hoje é `app/alerts/evaluator.py`** (`evaluate_price_alerts`,
TASK-027) — mas essa função não tinha nenhum chamador em produção, porque
não existe ainda nenhum serviço que insira `PriceObservation` de verdade
(só o modelo e consultas de leitura existem). A detecção dos outros cinco
tipos de evento do catálogo (`mission.status_changed.v1`,
`collection.completed/failed.v1`, `offer.availability_changed.v1`) também
não tem nenhum ponto de integração hoje — nenhuma TASK atribui essa
detecção ainda.

## Escopo

- `backend/migrations/versions/20260808_0003_create_events.py`: cria
  `events` exatamente conforme `docs/DATABASE.md` (colunas, dois índices
  compostos, CHECK constraints de texto/JSONB) e um trigger `BEFORE UPDATE
  OR DELETE` que rejeita qualquer alteração ou remoção, mesmo padrão de
  `mission_transitions`/`audit_entries`.
- `backend/app/events/models.py` (novo): modelo `Event`, registrado em
  `backend/app/database/model_registry.py`.
- `backend/app/events/service.py` (novo): `publish_event(session, *,
  event_type, aggregate_type, aggregate_id, payload, occurred_at,
  mission_id=None) -> Event` — valida `occurred_at` como consciente de fuso
  (mesmo idioma já usado em `missions/service.py`,
  `collection/contracts.py` etc.), reaproveita `validate_event_payload` do
  catálogo (TASK-042) para checar tipo e agregado, serializa o payload
  (dataclass frozen) em JSON seguro sem perda de precisão (`UUID → str`,
  `Decimal → str`, `Enum → .value`) e persiste. `EventPublicationError`
  cobre as duas falhas de validação próprias do serviço; erros de catálogo
  desconhecido/payload incompatível continuam propagando
  `EventCatalogError` sem serem mascarados.
- `recorded_at` é gerado **somente** pelo PostgreSQL
  (`server_default=func.now()`), nunca pela aplicação — `occurred_at` é o
  instante informado pelo produtor do fato, `recorded_at` é o instante real
  de gravação. O SQLAlchemy 2.x recupera esse valor via `RETURNING`
  automaticamente após `session.flush()`.
- Deliberadamente sem importar `app.alerts` em `app.events` — o serviço é
  genérico; quem tiver um `PriceAlertCandidate` desempacota seus campos na
  chamada.

## Fora de escopo

- Detecção de `mission.status_changed.v1`, `collection.completed/failed.v1`
  ou `offer.availability_changed.v1` — nenhuma TASK atribui essa detecção
  ainda; é uma lacuna real registrada aqui, não desta TASK.
- Qualquer worker, scheduler ou consumidor — pertence à TASK-044.
- Inserir `PriceObservation` de verdade num fluxo de coleta orquestrado —
  não existe hoje e não é o objetivo desta TASK.
- Notificação Telegram — TASK-036.

## Critério de aceite

`publish_event` valida e persiste, de forma durável e append-only, um
evento real do catálogo contra PostgreSQL real, com `recorded_at` gerado
pelo servidor e o trigger append-only rejeitando `UPDATE`/`DELETE`.
`scripts\check.cmd` completo aprovado.

## Resultado da validação real (2026-08-08)

PostgreSQL real via Docker Compose, migração `20260808_0003` aplicada
(`alembic upgrade head`). Validação executada dentro de uma transação
revertida ao final (sem resíduo no banco de desenvolvimento):

- `Product`/`Offer`/`CollectionRun`/`User`/`Mission`/`MissionCriteria`/
  `PriceObservation` reais persistidos, reaproveitando a loja `pichau` já
  semeada.
- `evaluate_price_alerts` (TASK-027) real produziu 2 candidatos
  (`price.decreased.v1` e `price.target_reached.v1`) a partir de uma queda
  de preço real cruzando o alvo da missão.
- `publish_event` persistiu os dois candidatos reais; `recorded_at` veio
  populado pelo servidor via `RETURNING` logo após `session.flush()`, sem
  nenhuma atribuição do lado da aplicação.
- As duas linhas foram lidas de volta na mesma transação, com o payload
  serializado corretamente (`Decimal` preservado como string exata, `UUID`
  como string).
- Uma tentativa real de `UPDATE` e uma de `DELETE` diretas em `events`
  foram ambas rejeitadas pelo trigger `trg_events_append_only`
  (`OperationalError`, mensagem contendo "append-only").
- `scripts\check.cmd` completo aprovado: 376 testes, 95,33% de cobertura,
  grafo de migrações com único head (`20260808_0003`), Docker Compose
  válido.
