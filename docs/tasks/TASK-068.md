# TASK-068 — Pré-lista de preços encontrados, sem IA

Status: **Concluída em 2026-08-10**, desenho aprovado explicitamente pelo
usuário (incluindo revisão do desenho inicial), implementada e validada
com pipeline oficial e teste de integração real (PostgreSQL).

Dependência: nenhuma. Quarta TASK da `v1.0.2` (`docs/V1_0_2.md`, item 5);
independente de TASK-065/066/067 (já concluídas).

## Contexto

`DEC-058` registrou que hoje o usuário só recebe alerta quando o preço
cai ou atinge o alvo (`app/alerts/evaluator.py`) — nenhuma mensagem
confirma que a missão está rodando nem mostra o que já foi encontrado.
`docs/V1_0_2.md` item 5 deixou o gatilho exato, o template e a lógica de
seleção **não decididos**, para esta TASK definir.

## Auditoria (2026-08-10)

- `Mission` não tinha nenhum campo indicando "primeira rodada de coleta
  concluída".
- `MissionSource` (uma linha por loja selecionada) é "quantas lojas essa
  missão busca"; `CollectionRun` é uma tentativa `(mission, loja)`, com
  `status` `RUNNING`/`SUCCEEDED`/`FAILED`.
- Cada `CollectionRun` bem-sucedido já publica `COLLECTION_COMPLETED_V1`
  (por loja) e, para cada oferta `MATCH` (TASK-063), chama
  `evaluate_price_alerts` — que só dispara em queda ou alvo atingido,
  nunca no primeiro preço encontrado.
- Não existia nenhuma query para "preço mais recente por loja de uma
  missão" — foi escrita nesta TASK.
- Toda mensagem proativa do Telegram passa pelo mesmo pipeline:
  `publish_event` (mesma transação da escrita de domínio) → tabela
  `events` (append-only) → `telegram_notifier` (worker separado) →
  `claim_unconsumed_events` → `_prepare_notification`/`_render_*` →
  `send_message` → `record_consumption_attempt`. Reaproveitado
  integralmente — nenhum mecanismo de notificação novo foi criado.
- Templates são sempre string fixa no código, nunca gerados por IA
  (`_render_alert`, `backend/app/telegram/notifications.py`, é o modelo
  seguido).

## Desenho aprovado (2026-08-10)

O usuário revisou a proposta inicial ("1 preço por loja, mostrando
todas") e pediu uma versão diferente: comparar as ofertas encontradas e
mostrar só as **2 mais baratas**, com um texto que deixa claro que a
busca continua; e um mecanismo de **correção** para quando uma loja
lenta/que falhou encontra depois algo mais barato que o já enviado.
Duas perguntas de desambiguação foram feitas e respondidas antes de
implementar:

### 1. Gatilho: primeira rodada completa

`Mission.prelist_sent: bool` (default `false`). Depois que cada
`CollectionRun` termina — sucesso **ou** falha — dentro de
`_persist_success`/`_record_failure`/`recover_stale_runs`
(`backend/app/collection/orchestration.py`), verifica-se: toda
`MissionSource` da missão já tem pelo menos um `CollectionRun` terminal?
Se sim, `prelist_sent` ainda `false` e a missão `ACTIVE`: avalia a
pré-lista e marca `prelist_sent = true` de qualquer forma (mesmo sem
nada relevante para mostrar), para nunca reavaliar a mesma rodada de
novo. Uma tentativa falha conta como terminal — a pré-lista nunca fica
esperando para sempre por uma loja bloqueada.

### 2. Seleção: 1 candidata por loja, depois as 2 mais baratas — por `amount`, sem frete

Para cada loja com pelo menos uma oferta `MATCH` (classificação já
calculada pela TASK-063 — reuso, nenhuma IA nova), pega-se a
`PriceObservation` mais recente daquela loja como candidata. Entre as
candidatas (no máximo 1 por loja), ordena-se por `PriceObservation.amount`
crescente e mostram-se as 2 mais baratas — nunca 2 ofertas da mesma
loja.

**Correção pós-implementação (2026-08-10):** a primeira versão desta
TASK ranqueava por `total_amount` (preço + frete). O usuário revisou
essa escolha antes da publicação: hoje o frete ainda não é
conhecido/comparável de forma confiável entre as 4 lojas, então usar
`total_amount` correria o risco de comparar "preço + frete estimado ou
ausente" de forma inconsistente entre ofertas. A base de comparação
passou a ser **sempre `PriceObservation.amount`** (preço anunciado do
produto, sem frete) — nunca estimado, nunca tratado como zero, nunca
usado para bloquear a pré-lista. A mensagem final deixa explícito que o
valor não inclui frete (item 4). Isso é **parecido, mas não igual**, ao
que `evaluate_price_alerts` (`docs/PRICE_ALERTS.md`, `DEC-045`) já faz —
também compara só `amount`, mas para a *mesma* oferta ao longo do
tempo — a pré-lista ranqueia *ofertas diferentes* de lojas diferentes
num único instante; os dois usam `amount` pelo mesmo motivo de fundo
(frete não é uma base confiável de comparação nesta V1), sem conflito
com a regra da TASK-027.

Se nenhuma loja tiver oferta `MATCH` ainda, nenhuma mensagem é enviada
(evita "pré-lista vazia"), mas `prelist_sent` vira `true` do mesmo jeito.

### 3. Correção (errata) — no máximo uma, por missão

`Mission.prelist_lowest_amount`/`prelist_lowest_currency` guardam a base
(a mais barata das até 2 ofertas enviadas, por `amount`).
`Mission.prelist_errata_sent: bool` (default `false`) garante que a
correção nunca é enviada mais de uma vez. Depois que a pré-lista
original já foi enviada, cada novo `CollectionRun` bem-sucedido verifica
se apareceu uma oferta `MATCH` com `amount` estritamente menor que a
base (em qualquer loja, não só na que falhou no round 1 — decisão
explícita do usuário: mais simples de implementar e cobre o mesmo
cenário descrito). Se a base original for `None` (rodada completou sem
nada relevante), a primeira oferta `MATCH` que aparecer depois conta
como "achado", não como "correção" — o template distingue os dois
casos.

### 4. Entrega: dois `EventType` + consumer dedicado

- `EventType.MISSION_PRELIST_READY_V1` — payload autocontido (não só
  `mission_id`): `first_offer_id`/`first_observation_id`/`first_amount`/
  `first_currency` obrigatórios, `second_*` opcionais (todos presentes
  ou todos ausentes), com validação de que a primeira é sempre a mais
  barata e as duas ofertas são distintas. A mensagem final inclui a
  frase fixa "⚠️ Valores sem frete. O frete será calculado/consultado na
  loja." — não gerada por IA, string fixa no código.
- `EventType.MISSION_PRELIST_ERRATA_V1` — payload com `offer_id`,
  `observation_id`, `current_amount`/`currency`, e
  `previous_lowest_amount` (`None` só no caso de "achado" acima), com
  validação de que o valor atual é estritamente menor que o anterior
  quando este existe. A mensagem de correção também inclui a mesma frase
  fixa sobre frete.
- Payload autocontido (não só `mission_id` + reconsulta no envio) por
  decisão de implementação: o formatador não precisa refazer a mesma
  lógica de seleção no momento do envio, e o conteúdo da mensagem reflete
  exatamente o que foi decidido no instante da publicação, não um estado
  potencialmente já mudado por uma coleta mais recente.
- Consumer dedicado `TELEGRAM_PRELIST_CONSUMER = "telegram_prelist_v1"`
  (mesmo padrão de `TELEGRAM_AUTH_NOTIFICATION_CONSUMER`), processado
  pelo mesmo `telegram_notifier` (`backend/app/telegram/worker.py`), com
  suas próprias funções `_render_prelist_ready`/`_render_prelist_errata`.
  **Não** reaproveita `telegram_price_alerts_v1` e **não** consulta
  `notify_price_decreases`/`notify_target_reached` (TASK-037) — a
  pré-lista não é um alerta de queda/alvo.

## Implementação (2026-08-10)

- **Migration** `20260810_0001_add_mission_prelist_fields.py`: adiciona
  `prelist_sent`, `prelist_errata_sent`, `prelist_lowest_amount`,
  `prelist_lowest_currency` a `missions`, com `CheckConstraint`s
  espelhando o padrão já usado em `mission_criteria` (par
  valor/moeda, moeda ISO 4217, valor não negativo) mais uma nova
  (`prelist_errata_sent` só pode ser `true` se `prelist_sent` também
  for).
- **`backend/app/missions/models.py`**: os 4 campos novos em `Mission`.
- **`backend/app/events/catalog.py`**: `MissionPrelistReadyPayload`,
  `MissionPrelistErrataPayload`, registrados em `EVENT_CATALOG` como
  `AggregateType.MISSION`.
- **`backend/app/collection/orchestration.py`**: `_evaluate_mission_prelist`
  (dispatcher), `_mission_prelist_round_complete`,
  `_latest_match_observations_by_store`, `_maybe_publish_prelist_ready`,
  `_maybe_publish_prelist_errata` — chamadas ao final de
  `_persist_success`, `_record_failure` e `recover_stale_runs` (as três
  formas de um `CollectionRun` chegar a um estado terminal).
- **`backend/app/telegram/notifications.py`**: `TELEGRAM_PRELIST_CONSUMER`,
  `process_telegram_prelist_notifications`, `_prepare_prelist_notification`
  (nunca chama `notification_is_enabled`), `_render_prelist_ready`,
  `_render_prelist_errata`, `_render_prelist_block`, `_load_offer_context`,
  `_PRELIST_SHIPPING_DISCLAIMER` ("⚠️ Valores sem frete. O frete será
  calculado/consultado na loja.") anexada em ambas as mensagens.
- **`backend/app/telegram/worker.py`**: terceiro `_process_batch` no loop
  de poll, combinado com os outros dois.
- **Nenhuma alteração** em `app/alerts/evaluator.py`,
  `app/collection/relevance.py` (semântica MATCH/POSSIBLE_MATCH/NO_MATCH
  da TASK-063), `telegram/preferences.py` ou no consumer
  `telegram_price_alerts_v1`.

## Validação (2026-08-10, revalidado após a correção de ranqueamento)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, **771 testes (90,49% cobertura)**, migration head
  `20260810_0001`, **16 testes de integração PostgreSQL reais** — todos
  aprovados (`Pipeline local aprovado.`).
- **Testes novos de contrato** (`tests/test_event_catalog.py`): validam
  a ordenação "mais barata primeiro" (por `amount`), ofertas distintas,
  campos `second_*` completos-ou-ausentes, e a comparação estrita da
  errata (incluindo o caso `previous_lowest_amount=None`).
- **Testes novos de decisão** (`tests/test_collection_orchestration.py`):
  `_evaluate_mission_prelist` não avalia missão inativa nem os dois
  fluxos na mesma chamada; `_maybe_publish_prelist_ready` escolhe as 2
  mais baratas por `amount` entre 3 candidatas de lojas diferentes e
  espera a rodada completar; `_maybe_publish_prelist_errata` só publica
  quando encontra algo estritamente mais barato por `amount`.
- **Testes novos de mensagem** (`tests/test_telegram_notifications.py`):
  1 bloco vs. 2 blocos (mais barata primeiro), preferências de
  queda/alvo desativadas não bloqueiam a pré-lista, texto de "correção"
  vs. "primeira oferta encontrada", falha fechada quando o destinatário
  está ausente/inativo, e presença do aviso "sem frete" nas três
  variantes de mensagem.
- **Teste de integração real** (PostgreSQL, `tests/integration/
  test_collection_orchestration.py::test_prelist_ready_fires_once_then_errata_corrects_a_cheaper_late_offer`):
  fluxo completo com o `CollectionOrchestrator` real —
  1. rodada 1: uma loja sucede (R$ 1.900,00), a outra leva bloqueio
     confirmado (403) → pré-lista dispara com 1 oferta só (a que
     sucedeu), `prelist_sent=true`, `prelist_errata_sent=false`;
  2. rodada 2: a loja que tinha falhado agora sucede mais barata
     (R$ 1.500,00) → uma única correção é publicada, `previous_lowest_amount`
     bate com o valor original;
  3. rodada 3: a mesma loja encontra um preço ainda mais barato
     (R$ 1.000,00) → nenhuma segunda correção é publicada (continua 1).
  Também atualizado um teste pré-existente da TASK-062 que verificava o
  conjunto exato de eventos de uma missão, para incluir
  `mission.prelist_ready.v1`.
- **Novo teste de integração real dedicado à correção**
  (`test_prelist_ranks_by_product_amount_ignoring_shipping`): duas lojas
  com `amount`/frete desenhados para que `amount` e `total_amount`
  discordem sobre qual oferta é mais barata (pichau: R$ 1.000,00 + frete
  R$ 500,00 = total R$ 1.500,00; kabum: R$ 1.100,00 + frete grátis =
  total R$ 1.100,00) — confirma que a pré-lista escolhe pichau (mais
  barata em `amount`, mesmo sendo mais cara em `total_amount`), provando
  que a implementação usa a base correta, não por coincidência.
- **Sem chamada de IA nova**: confirmado por auditoria de código — toda a
  seleção reaproveita `MissionOfferRelevance.classification` já calculada
  pela TASK-063; nenhum `ai_manager.generate` é invocado por este fluxo.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1`; nenhuma tag `v1.0.2` criada.

## Escopo confirmado

1. Migration `Mission.prelist_sent`/`prelist_errata_sent`/
   `prelist_lowest_amount`/`prelist_lowest_currency`.
2. `backend/app/events/catalog.py`: dois `EventType`s novos, payloads
   autocontidos e validados.
3. `backend/app/collection/orchestration.py`: gatilho avaliado nos três
   pontos onde um `CollectionRun` fica terminal.
4. Nova query de "última `PriceObservation` `MATCH` por loja de uma
   missão" (`_latest_match_observations_by_store`).
5. `backend/app/telegram/notifications.py`: consumer dedicado, sem
   preferências de queda/alvo.
6. `backend/app/telegram/worker.py`: terceiro consumer no loop.
7. `evaluate_price_alerts`, preferências de queda/alvo e a semântica
   MATCH/POSSIBLE_MATCH/NO_MATCH da TASK-063 permanecem intocadas.
8. Produção intocada; nenhuma tag `v1.0.2`; TASK-069 não iniciada
   automaticamente.

## Fora do escopo desta TASK

- Preferência configurável para desativar a pré-lista.
- Qualquer IA nova (a pré-lista só reaproveita a classificação `MATCH`
  já calculada durante a coleta).
- Comparação de menor preço histórico (item 11 da V1.2, com IA por cima
  — fase 2 desta mesma ideia).

## Encerramento

Concluída em 2026-08-10. A pré-lista informativa sem IA dispara uma
única vez por missão, após a primeira rodada completa de coleta, com as
2 ofertas mais baratas encontradas (a mais barata primeiro, sempre por
`PriceObservation.amount` — preço do produto, **nunca** `total_amount`,
já que o frete não é confiável/comparável entre lojas nesta V1); uma
única correção pode ser enviada depois se uma loja mais lenta encontrar
algo mais barato. A mensagem sempre deixa explícito que o valor não
inclui frete. Nenhuma IA nova envolvida; alertas de queda/alvo
(TASK-027/037) intocados. Produção da `v1.0.1` intocada; TASK-069 não
iniciada.
