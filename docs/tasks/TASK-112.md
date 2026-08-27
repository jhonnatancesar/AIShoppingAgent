# TASK-112 — Vincular missões que monitoram o mesmo item, sem duplicar coleta

Status: **Fase 1 concluída e commitada (`1dca734`) — Product Identity
Engine genérico. Fase 2 concluída e commitada (`5d05767`, 2026-08-25):
modelo `MonitoringItem`/`MissionMonitoringItem`/`MonitoringItemStore`;
vínculo/relink/desvínculo centralizados em `reconcile_mission_
monitoring_item(_async)` (`app/missions/monitoring.py`), chamado por
todo caller que pode alterar identidade relevante (criação, seleção de
variante, confirmação pós-coleta, desidentificação de conta); identidade
EFETIVA da missão resolvida com precedência (variante de `Product`
selecionada > `VariantSelectionMode.ALL` > texto), mesmo algoritmo de
`monitoring_key` para as três fontes
(`app.products.identity._build_monitoring_identity`); `monitoring_key`
agora carrega `scope` explícito (`SPECIFIC`/`FAMILY`/`GENERIC` --
`MonitoringScope`, `MONITORING_KEY_VERSION` v2) para que "variante
específica" e "qualquer variante da família" nunca colidam mesmo
descrevendo a mesma família de produto; `VariantSelectionMode.ALL`
("qualquer variante") agora também gera Shared Monitoring, escopo
`FAMILY` -- regra corrigida em 2026-08-25 (achado do usuário: a versão
anterior forçava `ANY` incondicionalmente e apagava restrição real, ex.
"iPhone 17 128GB" em modo ALL perderia o 128GB): a ÚNICA diferença de
`SPECIFIC` é que atributo bloqueante ausente não falha fechado; toda
restrição que o texto de fato especificou (variante "Pro"/"Plus"/...,
`board_brand`, `storage_gb` etc.) é preservada normalmente, `ANY` é só
para o que não foi mencionado -- `_build_monitoring_identity` usa a
MESMA resolução de variant/atributos para todo escopo, só o gate de
atributo bloqueante é exclusivo de `SPECIFIC`; `variant` nunca chega a
`None` no payload canônico (correção adicional, mesma data) -- quando
não especificado ou quando a categoria não tem conceito de variante
(CPU/GPU), vira a string canônica `"ANY"`, igual a `attributes`, nunca
ausência/`null`; `GENERIC_CATEGORY` mantém contrato fail-closed
explícito (`MonitoringScope.GENERIC` reservado, hoje inalcançável --
nenhuma `CategoryDefinition` atual permite montar identidade
determinística suficiente sem um `model`/`family` resolvido pelo
extractor); lifecycle pause/resume/cancel derivando `is_enabled` com
serialização real por banco (`SELECT ... FOR UPDATE` + reconsulta
pós-lock, ordenada por `store_id`) -- corrida de pause/cancel concorrente
corrigida e coberta por teste de concorrência real.

Fase 3A concluída e commitada localmente (`471e898`, 2026-08-26, rodada
4 de correções; `DEC-100`); publicação em `origin/main` ainda pendente.
Prova que UMA necessidade `(MonitoringItem, store)` executa UMA
coleta real e distribui o resultado por fan-out individual de Mission.
`CollectionCriteria` canônico (`app.products.identity.canonical_
collection_criteria`) nasce só de `MonitoringItem.canonical_identity` --
nunca do texto cru de nenhuma Mission vinculada. Correção de VRAM no
extrator de GPU (`_gpu`/`_gpu_vram`): "RTX 5070 Ti" (sem VRAM) e "RTX
5070 Ti 16GB" (VRAM explícita) nunca compartilham `monitoring_key` --
antes desta correção, "GB" nunca virava atributo nenhum e a restrição
explícita era apagada em silêncio; não existe hoje regra determinística
que complete VRAM a partir só do modelo (ao contrário do tier de CPU),
já que um mesmo modelo pode vender em mais de uma configuração real.

Persistência comercial (`_persist_shared_offers_and_finish`) roda
EXATAMENTE UMA VEZ por chamada -- correção de desenho da rodada 2: a
versão anterior chamava `_persist_phase_a` uma vez por Mission do
fan-out, um uso indevido do dedupe TASK-093/DEC-097 (que deduplica ENTRE
coletas no tempo, não entre beneficiários da MESMA coleta). O fan-out
(`_build_mission_phase_a_outcome`) monta o mesmo `_PhaseAOutcome` que
`_run_phase_b`/`_persist_phase_c` (TASK-079, reaproveitados sem nenhuma
alteração de comportamento) já processam -- pré-lista, `MissionOfferRelevance`,
alerta e reset de backoff por Mission continuam exatamente como no
caminho de missão única. "Previous" por Mission (DEC-048) passou a vir
de `MissionOfferRelevance.last_observation_id` (coluna nova, migration
`20260825_0003`, populada também no caminho de missão única em
`_persist_phase_c` -- pequena adição justificada, nunca lida por aquele
caminho) em vez de `CollectionRun.mission_id`, que não existe mais para
uma observação compartilhada. Migration `20260825_0003` inclui backfill
determinístico de `last_observation_id` para linhas pré-existentes
(reconstrói a partir da última `PriceObservation` via
`CollectionRun.mission_id` -- mesmo dado que `_persist_phase_a` já usava
antes desta coluna existir); coberto por teste que simula estado antigo
representativo e confirma que o primeiro ciclo novo não redispara
`PRICE_TARGET_REACHED`.

Fan-out DURÁVEL e retomável (correção da rodada 3 -- risco real: crash
entre a coleta comercial terminar e o fan-out terminar perderia
Missions para sempre). `_persist_shared_offers_and_finish` grava, na
MESMA transação que persiste `Offer`/`PriceObservation`, um registro
durável de quais ofertas fizeram parte da coleta
(`SharedCollectionOffer`, migration `20260825_0004` -- inclusive quando
a observação foi reaproveitada/redundante, que por definição não fica
presa a `collection_run_id`) e cria uma `SharedFanOutTask` (`pending`)
por Mission elegível NAQUELE momento, e só então marca a `CollectionRun`
compartilhada `SUCCEEDED` -- atômico: ou tudo commitou junto (run
`SUCCEEDED` + ofertas + tarefas já duráveis) ou nada commitou (run
continua `RUNNING`, seguro repetir o provider depois).
`resume_shared_collection_fan_out` retoma só tarefas `pending`, das runs
mais antigas para as mais novas, reconstruindo o resultado via
`SharedCollectionOffer` -- NUNCA chama o provider de novo, NUNCA
reprocessa uma Mission já `done`. `_process_pending_fan_out` é o único
código de fan-out (usado pelo caminho fresco e pela retomada -- nunca
dois jeitos diferentes). Provado com teste que processa A, simula crash
antes de B/C, retoma e confirma: provider 1x total, persistência
comercial 1x, A não repete, B/C processadas, checkpoints finais
corretos.

Consistência do próprio `SharedFanOutTask` (correção da rodada 3).
Máquina de estados: `pending -> processing -> done` (feliz) ou
`pending -> processing -> pending` (falha transitória/corrida, retry com
backoff -- `attempt_count`/`next_retry_at`/`last_error`). Claim atômico
(`_claim_fan_out_task`, `UPDATE ... WHERE status='pending' ...`, nunca
mutex em memória) garante que duas workers nunca processam a mesma
`(collection_run_id, mission_id)` ao mesmo tempo -- provado com teste de
concorrência real (2 workers/conexões, `Barrier`, A+B+C pendentes, cada
Mission processada por exatamente uma das duas). `processing` travado
além do lease (`_FAN_OUT_PROCESSING_STALE_AFTER=10min`) é recuperável
via `recover_stale_fan_out_tasks` (mesmo espírito de `recover_stale_
runs`). Idempotência dos efeitos individuais sob crash NO MEIO do
processamento (depois de `_persist_phase_c` já ter commitado, antes da
tarefa virar `done`): confirmada como propriedade JÁ existente do
desenho -- o retry encontra `MissionOfferRelevance.last_observation_id`
já apontando para a observação desta coleta, o que faz `alert_
comparison=UNCHANGED_REUSED` e pula a reavaliação de alerta -- provado
com teste dedicado (`PRICE_TARGET_REACHED` continua em exatamente 1
evento depois do retry, não 2), não só "provavelmente dedupe".

Semântica final do fan-out durável (correção da rodada 4 -- 5 pontos
concretos que a rodada 3 ainda não provava):

1. **Classificação de erro nunca assume terminal sem prova.** Exceções
   no fan-out são RETRYABLE por padrão (`_fail_fan_out_task_retryable`)
   -- só `SharedFanOutTerminalError` (levantada apenas nos dois casos
   genuinamente determinísticos de `_build_mission_phase_a_outcome`:
   Mission/critério sumiu, produto sumiu) vai direto para
   `terminal_failed` via `_fail_fan_out_task_terminal`, sem gastar
   tentativas. `attempt_count` esgotado (`_MAX_FAN_OUT_ATTEMPTS=5`)
   NUNCA mais vira `terminal_failed` sozinho -- vira `attention_
   required`: auditável (`last_error`/`attempt_count`), reprocessável
   (nada no schema impede resetar para `pending` manualmente -- provado
   com teste), só parou de tentar sozinha para não fazer retry infinito.
   `SharedFanOutStatus` ganhou os dois estados novos (`skipped`,
   `attention_required`), 6 no total. Provado com dois testes dedicados:
   erro determinístico vira `terminal_failed` já na 1ª tentativa (sem
   gastar orçamento de retry); erro genérico (infra/IA simulada via
   `_run_phase_b` envolvido) retenta com backoff e só vira `attention_
   required` depois de esgotar as tentativas reais, nunca `terminal_
   failed`.
2. **Revalidação de elegibilidade antes de qualquer efeito**
   (`_mission_still_eligible_for_fan_out`, chamada logo após o claim
   atômico da tarefa, antes de `_start_mission_fan_out_run`): confirma
   que a Mission ainda existe, está `ACTIVE`, ainda aponta para o MESMO
   `MonitoringItem` (pega reconcile/relink) e ainda tem `MissionSource`
   para aquela loja. Se não, a tarefa vira `skipped` (nunca erro, nunca
   gera alerta/notificação) via `_skip_fan_out_task`; Mission retomada
   depois é responsabilidade de uma coleta FUTURA, nunca revive um
   fan-out antigo. Provado com 3 testes dedicados (pausada, cancelada,
   religada para outro `MonitoringItem` entre a coleta e o fan-out --
   cada um com B ainda ACTIVE como controle, processando normalmente).
3. **Idempotência real de notificação**, não só do evento persistente:
   o dispatcher de Telegram já usa o outbox idempotente pré-existente
   (TASK-080, `app.events.consumption` -- `claim_unconsumed_events_
   async`/`record_consumption_attempt_async`), nunca um envio direto
   dentro do processamento. Provado usando o MESMO mecanismo real (sem
   mock dele): depois de um crash simulado no meio do processamento de
   uma Mission com alerta, seguido de retry, existe exatamente 1 `Event`
   e exatamente 1 notificação reivindicável -- depois de consumida,
   zero reivindicáveis de novo.
4. **Integridade das tabelas novas confirmada no banco** (não só lendo o
   model): `SharedFanOutTask` e `SharedCollectionOffer` já impedem
   duplicação lógica pela própria PRIMARY KEY (`(collection_run_id,
   mission_id)` e `(collection_run_id, offer_id)`) -- nenhuma mudança de
   schema necessária, confirmado com teste de integridade dedicado para
   cada tabela (`IntegrityError` numa segunda linha com a mesma chave).
5. **Recuperação automática, sem intervenção manual**:
   `resume_shared_collection_fan_out` chama `recover_stale_fan_out_
   tasks` como PRIMEIRO passo, sempre -- quem chama esta função nunca
   precisa lembrar de recuperar tarefas presas separadamente.
   `recover_stale_fan_out_tasks` continua exposta e idempotente para
   quem quiser chamar à parte também. Provado com teste dedicado que
   NUNCA chama `recover_stale_fan_out_tasks` explicitamente -- só
   `resume_shared_collection_fan_out`, e a tarefa presa é recuperada e
   processada mesmo assim.

Semântica de `CollectionRun.status == SUCCEEDED` (execução compartilhada)
documentada explicitamente: significa só "a coleta comercial terminou",
NUNCA "todas as Missions foram notificadas" -- isso é consultado via
`SharedFanOutTask` (`pending`/`processing`/`done`/`skipped`/`attention_
required`/`terminal_failed`) por `collection_run_id`.

Recuperação de run compartilhada abandonada (crash entre o claim e o
fim da coleta comercial, antes de `SUCCEEDED`): auditado -- `recover_
stale_runs` (TASK-079, já genérico, nunca precisou de mudança) não
filtra por `mission_id`, já reconhecia runs com `monitoring_item_id`
desde que a coluna existe; `_evaluate_mission_prelist(session, None,
...)` já era um no-op seguro para `mission_id=NULL`. Provado com teste:
claim abandonado -> segunda tentativa concorrente rejeitada (slot nunca
roda 2x) -> `recover_stale_runs` marca `FAILED` (nunca preso em
`RUNNING`) -> quando o próximo ciclo natural vence (`next_run_at` já
avançado pelo claim original -- recuperação não antecipa o ciclo, mesmo
comportamento já existente para missão única, nenhuma política nova de
retry inventada), uma nova execução ocorre exatamente uma vez.

Claim/lock real via `CollectionRun.monitoring_item_id` (migration
aditiva `20260825_0001`) + índice único parcial `uq_collection_runs_
running_monitoring_item_store`, mesma técnica já usada por `mission_id`
-- nunca mutex em memória; lock (`SELECT ... FOR UPDATE` em
`MonitoringItemStore`) sempre adquirido ANTES do recheck de
`is_enabled`/`next_run_at`/`next_eligible_at` e do claim, garantindo que
um segundo worker só prossiga depois que o due slot já foi avançado pelo
primeiro (provado com teste determinístico de "mesmo `now` nunca
reclama duas vezes", além do teste de concorrência real já existente).
`CollectionRun` ganhou `CHECK ck_collection_runs_ownership_xor`
(migration `20260825_0002`): `mission_id` XOR `monitoring_item_id`,
nunca os dois, nunca nenhum -- reforçado também em Python
(`start_collection_run`). `CollectionRequest` corrigido (contrato, não
mais hack): `mission_id`/`monitoring_item_id` opcionais com a mesma
regra XOR validada em `__post_init__`; nenhum caller mais reaproveita
`mission_id` para carregar um `monitoring_item_id`.

Backoff/agenda movidos para `MonitoringItemStore` (`next_run_at`/
`next_eligible_at`/`consecutive_blocks`); `MissionSource` intocado,
continua preferência/cota do usuário (TASK-107). Auditoria confirmou que
hoje TODA missão usa o mesmo intervalo global (`Settings.collection_
schedule_interval_minutes`), sem exceção por missão -- contrato para
variação futura documentado, não implementado (nada para testar contra
hoje). Erro isolado por Mission no fan-out nunca marca a coleta
compartilhada como falha nem afeta outras Missions -- reconfirmado após
os dois refactors. Testado sinteticamente com 60 Missions compartilhando
o mesmo `(MonitoringItem, store)`: checkpoint individual
(`MissionOfferRelevance` uma linha por Mission), pré-lista individual
sem duplicação entre ciclos, sem sinal de crescimento O(N²). TASK-111
(assert de 4 lojas desatualizado, achado recorrente nesta regressão)
corrigido junto.

Sem scheduler principal (`claim_due_collections`/`CollectionOrchestrator`,
intocados), sem fila justa/`fairness_owner`/TASK-108 (fase 3B) --
`collect_monitoring_item_store`/`resume_shared_collection_fan_out` são
chamadas isoladamente, caminho controlado/testável; FASE 3B decide
QUANDO/COM QUE FREQUÊNCIA chamar `resume_shared_collection_fan_out` em
produção -- ainda não integradas ao loop de produção.**

**Fase 3B concluída (2026-08-27, revisada em 6 rodadas antes do código --
ver `docs/internal/decision-log.md`). `CollectionOrchestrator` (produção)
passa a chamar `claim_due_work` (`orchestration.py`), scheduler unificado
que reserva fairness e claima os dois caminhos (antigo por Mission, novo
por MonitoringItem) na MESMA transação/ciclo. `claim_due_collections`/
`_select_due_schedules_for_batch` continuam existindo, quase sem
mudança (só ganharam o anti-join contra `MissionMonitoringItem`) --
compatibility API para quem ainda chama direto (auditado: só testes,
nenhum outro runtime real).

**Achado que corrige as seções §6/§7/§12/§14 abaixo** (escritas antes da
implementação real, nunca atualizadas): a implementação de verdade
(fase 2, `1dca734`/`5d05767`) **não criou** `MissionCriteria.
monitoring_item_id` nem `MonitoringItemSchedule`/`MonitoringItemSource`
como classes separadas -- o vínculo Mission→MonitoringItem é
exclusivamente via `MissionMonitoringItem` (`mission_id` é a própria PK),
e agenda+backoff do item vivem todos em `MonitoringItemStore` (fusão das
duas tabelas planejadas). As seções abaixo ficam como registro histórico
do raciocínio de design; para o schema real, use esta seção e o código.

**Reserva de fairness — mecanismo final** (substituiu completamente o
`fairness_owner` "calculado e creditado depois" do §12 original): dentro
de `claim_due_work`, Fase 1 reserva usuários candidatos via lock real
(`SELECT ... FOR UPDATE SKIP LOCKED` em `UserCollectionQueueState`, em
ordem ascendente de `user_id` -- nunca espera, nunca cria ciclo de
deadlock com locks de loja, que só são adquiridos DEPOIS, na Fase 2, em
ordem de `store_id`); só ENTÃO os recursos (Mission+loja / item+loja) são
efetivamente reivindicados, numa lista única ordenada por `(store_id,
due_at, kind, resource_id)` -- nenhum caminho tem prioridade estrutural
sobre o outro na mesma loja. Cooldown só é gravado
(`app.collection.fairness._commit_fairness_turn_for_owner`, `UPDATE`
simples -- o lock da Fase 1 já garante exclusão mútua, sem precisar de
CAS) para donos que tiveram >= 1 claim real; um dono reservado sem
nenhum claim real (loja em throttle, corrida perdida) não paga cooldown
algum. `UserCollectionQueueState.last_fairness_turn_id` é só rastro de
auditoria (qual ciclo creditou o avanço) -- a garantia de corretude em si
é o lock contínuo, não uma comparação de token. `CollectionRun.
fairness_owner_user_id` grava o dono na MESMA transação/savepoint do
claim compartilhado -- `NULL` sempre no caminho antigo, e também no
caminho compartilhado STANDALONE (`collect_monitoring_item_store`
chamada fora do orchestrator, sem `fairness_owner_user_id` -- nunca toca
`UserCollectionQueueState`); nunca `NULL` no caminho orquestrado.

**Seleção somente-leitura** (`_select_due_work_for_batch`): candidatos
compartilhados contam `MonitoringItemStore` ÚNICO (nunca explodido por
usuário vinculado -- achado real: um item com 100 vinculados não pode
consumir 100 posições da janela de scan), `candidate_scan_limit`
(default 1000, configurável) é só para DESCOBRIR trabalho/donos, nunca
um teto de execução -- um dono reservado tem todo o seu trabalho due
processado. `EXPLAIN ANALYZE` contra 5000 `MonitoringItemStore`
sintéticos (2% due) confirma `ix_monitoring_item_stores_due` em uso
(Bitmap Index Scan, nunca sequential scan completo), execução sub-
milissegundo -- `next_eligible_at` não entrou no índice por desenho (não
"no escuro"): a seletividade real é dominada por `next_run_at`, o filtro
extra sobre a fração já due é barato mesmo fora do índice.

**Sweep de fan-out** (`sweep_shared_collection_fan_out`, chamado no
INÍCIO de todo `run_batch`, antes de qualquer claim novo -- backlog
antigo tem prioridade): orçamento em duas dimensões, nunca "todos os
pendentes" -- `target_scan_limit` (quantos `(item, store)` distintos
considerar) e `task_budget` (quantas `SharedFanOutTask` no TOTAL),
alocado em rodadas via `per_target_task_cap` para que um alvo com
backlog grande nunca monopolize o ciclo enquanto alvos menores também
estão devidos. `recover_stale_fan_out_tasks` roda UMA vez por sweep
(nunca uma vez por alvo -- `resume_shared_collection_fan_out(...,
recover_stale=False)` internamente). Ordenação por `COALESCE(next_
retry_at, created_at)` tanto entre alvos quanto dentro de cada alvo
(achado real: a query interna de `_process_pending_fan_out` ordenava por
`mission_id`, arbitrário, antes desta fase) -- retry antigo nunca é
starvado por uma enxurrada de tarefas novas. `attempted_task_count`
(contagem real de tarefas reivindicadas, nunca a soma dos buckets de
resultado) é o contrato de orçamento.

**Política de cadência** (`app.collection.cadence`, módulo novo) --
NUNCA confundir com cooldown de fairness (decide QUEM, não QUANDO uma
necessidade específica é revisitada) nem com `StoreThrottleState`
(proteção contra rajada, não intervalo de monitoramento). Prioridade
fixa: backoff por bloqueio confirmado (DEC-046) sempre vence (fora desta
política, resolvido pelo duplo-gate já existente de `next_run_at`/
`next_eligible_at` no claim) > `PROMO_CALENDAR`/`HIGH_ACTIVITY` (30-45min,
piso absoluto de 30min reforçado em `CadenceConfig.__post_init__` --
nenhum modo, nem uma futura diferenciação de plano pago, pode baixar
disso) > `NORMAL` (45-75min, alvo ~60). Calendário promocional
(`PromotionalWindow`, tabela) é dado, não código -- ADMIN insere/remove
janelas sem NENHUMA migration nova. Atividade comercial alta é POR LOJA
(nunca por produto/item individual, nunca IA, nunca estatística
sofisticada): sinal já durável, sem schema novo para a contagem em si --
uma nova `PriceObservation` só existe quando o estado comercial mudou de
verdade (TASK-093/DEC-097), então contar linhas novas numa janela já é
contar mudanças reais. `StoreActivityState` guarda só a HISTERESE
(`high_activity_until`, evita alternar NORMAL/HIGH_ACTIVITY a cada ciclo
bem na borda do limiar) -- nunca cacheia a contagem em si. `_advance_
monitoring_item_store` passou a agendar sempre relativo a AGORA (nunca
mais "recuperar atraso em múltiplos do intervalo antigo" -- incompatível
com uma faixa que muda de ciclo para ciclo).

**Migration** `20260826_0001` (aditiva, única): `UserCollectionQueueState.
last_fairness_turn_id`, `CollectionRun.fairness_owner_user_id` (+ FK
`users.id` `ondelete=RESTRICT` -- auditado `app.privacy.service.
deidentify_account`, nunca apaga a linha `User`, só remove credenciais/
`telegram_user_id`; a FK nunca fica pendurada na prática -- + `CHECK`
`ck_collection_runs_fairness_owner_requires_shared`), índice `ix_
monitoring_item_stores_due`, tabelas novas `promotional_windows`/
`store_activity_state`.

**Organização de código**: dois módulos novos, neutros, sem dependência
circular nova -- `app/collection/fairness.py` (reserva/turno, throttle de
loja) e `app/collection/shared_claim.py` (claim compartilhado, movido de
`shared_collection.py`) -- ambos importados por `orchestration.py` E por
`shared_collection.py`, nenhum dos dois importa do outro para isto. O
único sentido restante (`CollectionOrchestrator` chamando `_execute_
claimed_shared_collection`/`sweep_shared_collection_fan_out`, que ficam
em `shared_collection.py` por serem execução/rede) usa injeção de
dependência no construtor (mesmo padrão de `identity_resolver`,
TASK-083) + `TYPE_CHECKING` para os tipos -- `orchestration.py` nunca
importa `shared_collection.py` no nível de módulo.

**Testes**: suíte de integração completa (152 testes, incluindo toda a
fase 3A/TASK-108 sem NENHUMA modificação) verde contra PostgreSQL real,
mais 15 testes novos dedicados (`tests/integration/test_unified_fair_
queue.py`, `tests/integration/test_cadence_and_high_activity.py`) --
exclusão de vinculada do legado, `claim_due_collections` sem efeito
shared, rider nunca avança, dono reservado sem claim não paga cooldown,
owner NULL só no standalone, concorrência real (2 workers, `Barrier`)
sem duplicar claim, NORMAL/PROMO/backoff/atividade alta. Suíte completa
de testes unitários (não-DB) também verde -- 2 testes pré-existentes
(`test_collection_worker.py`) ajustados para os campos novos de
`CollectionBatchResult`.

Ver `docs/internal/decision-log.md` para o registro completo de
decisões.**

## Objetivo

Quando um usuário cria uma missão para um item que já está sendo
monitorado por outra missão ativa — de outro usuário, ou do mesmo
usuário —, o sistema deve **vincular** a nova missão ao mesmo item já
monitorado, em vez de criar uma coleta independente e duplicada. A
equivalência nasce do **contexto canônico estruturado**, produzido pela
IA só para interpretar o pedido e resolvido deterministicamente por um
motor de identidade de produtos — nunca de uma segunda decisão semântica
da IA. O ciclo de vida (pausar/cancelar) continua **por missão/usuário**:
pausar/cancelar uma missão vinculada nunca apaga o item compartilhado
nem afeta as demais missões vinculadas ainda ativas.

## Contexto / motivação

Hoje cada `Mission` tem seu próprio `MissionCriteria`/`MissionSource`/
`MissionSchedule`; o agendamento de coleta é por missão. A TASK-093 já
reduz `PriceObservation` redundante no nível da `Offer`, mas isso só
evita **gravação** duplicada — não evita que duas missões independentes,
representando a mesma necessidade de monitoramento, gerem dois
agendamentos/coletas separados contra a mesma loja.

## Limite entre IA e sistema determinístico (regra mais importante — vale para todo o resto do documento)

```
USER (texto livre, "9950x3d", "ryzen 9950x3d", "RTX 5070 Ti ASUS")
  │
  ▼
IA / IntentInterpreter -- SÓ interpreta e estrutura o pedido.
  │  Nunca decide se duas missões são "a mesma coisa".
  ▼
Product Identity Engine -- SEMPRE determinístico.
  │  Normaliza, resolve aliases, aplica ontologia por categoria,
  │  decide ANY vs valor restrito.
  ▼
Estrutura canônica (category/brand/family/series/model/variant/attributes)
  │
  ▼
monitoring_key determinística (hash versionado)
  │
  ▼
Sistema compara chaves (igualdade de string/hash) -- nunca fuzzy
matching, nunca "a IA acha que é parecido".
  │
  ▼
Vincula (chaves iguais) ou cria item novo (chaves diferentes ou
identidade ainda não resolvível com confiança -- fail-closed).
```

A IA participa **só** da primeira seta. Toda seta abaixo dela é
determinística, testável com casos fixos e nunca reavaliada por IA em
tempo de comparação. Isso vale para: identidade, equivalência,
normalização, `monitoring_key`, decisão de compartilhar coleta.

## 1. Product Identity Engine

### 1.1 O que já existe (TASK-097) e o que muda

`app/products/identity.py` já tem exatamente o formato certo de motor —
**não é para ser substituído, é para ser expandido**:

- `ProductRequestKind` (`SPECIFIC_PRODUCT`/`PRODUCT_FAMILY`/
  `GENERIC_CATEGORY`) — **mantido sem nenhuma mudança**. Continua
  dirigindo a UX de seleção de variante já existente
  (`VariantSelectionMode`, `MissionProductSelection`,
  `_deterministic_product_relevance`) e o `CHECK` de forma em
  `mission_criteria` (`ck_mission_criteria_product_request_shape`). Zero
  risco para o que já funciona.
- `_ParsedFamily` (`category, brand, family, model, variant, attributes:
  tuple[tuple[str,str],...], required_attributes: frozenset[str]`) —
  **já é a representação certa**, inclusive já suporta atributo
  extensível sem coluna SQL nova (`attributes` é uma tupla livre,
  serializada dentro do hash — é assim que `storage_gb` já funciona hoje
  para iPhone, sem nenhuma migration dedicada a "capacidade de
  armazenamento"). O motor generaliza essa mesma ideia, não inventa uma
  nova.
- `_EXTRACTORS: tuple[_Extractor, ...] = (_iphone, _galaxy_s)` — **este
  é o único ponto realmente estreito hoje**: só 2 categorias registradas.
  O mecanismo de registry (tupla de funções, cada uma tenta parsear e
  devolve `None` se não reconhece) já É o "registry/extractors
  plugáveis" pedido — só faltam mais entradas na tupla. Não existe
  `if/else` gigante por categoria hoje, e o desenho abaixo preserva isso.

O que muda: (a) cada entrada do registry passa a declarar também **quais
atributos são bloqueantes** (exigem resolução ou confirmação explícita
do usuário, como `storage_gb` hoje) vs **quais têm default `ANY`**
(nunca bloqueiam, só entram na `monitoring_key` — ver §2); (b) o
registry ganha um número relevante de categorias novas (§1.3); (c) surge
uma tabela de **aliases** persistida e determinística (§3), porque
normalização por regex/case-fold (o que já existe, `normalize_for_
matching`) não resolve "Ryzen 9 9950X3D" == "9950X3D" (não é diferença
de separador/maiúscula, é a extração do código dentro de um texto maior
— trabalho do próprio extractor, não de alias) nem "ASUS" == "ASUSTeK"
(isso sim é alias de valor de atributo).

### 1.2 Estrutura conceitual (ontologia)

```python
@dataclass(frozen=True, slots=True)
class AttributeDefinition:
    name: str  # "board_brand", "storage_gb", "vram", "refresh_rate"...
    blocking: bool
    """True = mesmo comportamento de `storage_gb` hoje: se o USER não
    especificou, a missão fica PRODUCT_FAMILY (`VariantSelectionMode.
    PENDING`), precisa de resolução explícita (usuário escolhe UMA
    variante = SELECTED, ou declara "quero todas" = ALL) antes de virar
    elegível para monitoring_key. NUNCA um default silencioso.
    False = default ANY silencioso quando ausente (ex.: board_brand de
    GPU) -- não bloqueia a missão do próprio usuário, e ANY entra
    explicitamente na monitoring_key como valor."""
    normalize: str | None = None
    """Nome de uma função de normalização registrada (não closure solta
    -- precisa ser serializável/rastreável para auditoria). None usa
    normalize_for_matching (TASK-075) como default."""


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    category: str  # "cpu", "gpu", "smartphone", "monitor", ...
    extractor: _Extractor  # mesmo tipo de hoje: texto -> _ParsedFamily | None
    attributes: tuple[AttributeDefinition, ...]  # ordem fixa, parte da chave versionada
```

`brand`/`family`/`model`/`variant` continuam campos de primeira classe
(iguais a `_ParsedFamily` hoje — já cobrem o "series" do pedido quando
necessário: para CPU, `family="ryzen_9"` já é o que o pedido chamou de
"series"; o motor não força uma quinta camada obrigatória onde o
extractor da categoria não precisa dela — cada `CategoryDefinition`
decide sua própria granularidade). "constraints" do pedido mapeiam para
o **schema em si** (`AttributeDefinition.blocking` e a normalização) —
é a regra de negócio sobre o atributo, não um valor; "attributes" são os
valores resolvidos para um pedido específico.

### 1.3 Registry — categorias cobertas desde já

Sem `if/else` por categoria: `_CATEGORY_REGISTRY: tuple[CategoryDefinition,
...]`, cada entrada plugável independente. Cobertura inicial proposta
(prioriza categorias com Alta demanda de monitoramento de preço —
componentes/eletrônicos — sem se comprometer com implementar todas de
uma vez, ver §15 "ordem recomendada"):

`cpu`, `gpu`, `smartphone` (já existe: iPhone/Galaxy S viram 2 entradas
do MESMO registry, sem mudar comportamento), `tablet`, `notebook`,
`monitor`, `tv`, `ram`, `ssd`, `hdd`, `motherboard`, `psu` (fonte),
`case` (gabinete), `cooler`, `keyboard`, `mouse`, `headset`, `console`,
`controller`. Categorias fora dessa lista inicial (câmera, roteador,
impressora, eletrodoméstico, ferramenta) entram pelo mesmo mecanismo,
quando houver demanda real — o motor não precisa cobrir tudo no dia 1,
precisa ser **capaz** de crescer sem redesenho (§4).

### 1.4 Exemplos worked

```
"9950x3d"                          "ryzen 9950x3d"
  → extractor cpu reconhece            → extractor cpu reconhece
  category=cpu                         category=cpu
  brand=amd                            brand=amd
  family=ryzen_9                       family=ryzen_9
  model=9950x3d                        model=9950x3d
  attributes: {} (cpu não tem          attributes: {} (idêntico)
    atributo bloqueante nem ANY
    hoje -- registry pode crescer)
  → MESMA estrutura canônica → MESMA monitoring_key

"5070 Ti"                          "RTX 5070 Ti"                    "RTX 5070 Ti ASUS"
  category=gpu                       category=gpu                     category=gpu
  gpu_vendor=nvidia                  gpu_vendor=nvidia                gpu_vendor=nvidia
  family=geforce_rtx                 family=geforce_rtx               family=geforce_rtx
  model=5070_ti                      model=5070_ti                    model=5070_ti
  board_brand=ANY (não bloqueante,   board_brand=ANY                  board_brand=asus (valor
    USER não especificou)                                               explícito -- restrição real)
  vram=ANY (idem)                    vram=ANY                         vram=ANY
  → chave IGUAL às primeiras duas    → DIFERENTE da anterior (board_brand muda)

"iPhone 17 Pro" (sem storage)       "iPhone 17 Pro 256GB"
  category=smartphone                 category=smartphone
  brand=apple                         brand=apple
  family=iphone                       family=iphone
  model=17                            model=17
  variant=pro                         variant=pro
  storage_gb=? -- BLOQUEANTE          storage_gb=256 (resolvido)
  → PRODUCT_FAMILY, PENDING            → SPECIFIC_PRODUCT
    (UX de seleção de variante          → monitoring_key resolvida
    já existente decide, não a
    monitoring_key -- ver §5)

"monitor 27 polegadas 165hz"
  category=monitor
  brand=ANY (USER não pediu marca -- não bloqueante)
  size=27
  refresh_rate=165hz
  resolution=ANY
  panel=ANY
  → monitoring_key resolvida mesmo com 3 atributos em ANY --
    são todos não-bloqueantes por definição do schema de "monitor".
```

## 2. Representação de ANY

`ANY` é sempre um **valor explícito** dentro da estrutura canônica —
nunca ausência silenciosa de chave. Regra fixa: todo atributo declarado
no `CategoryDefinition` da categoria **sempre aparece** na estrutura
canônica resolvida, como valor concreto normalizado **ou** o literal
`"ANY"`. Dois pedidos só produzem a mesma `monitoring_key` quando todo
atributo bate exatamente (valor com valor, `ANY` com `ANY`) — nunca por
aproximação. O motor nunca inventa uma restrição que o usuário não
pediu: ausência de menção = `ANY`, nunca um valor "mais provável" ou
"mais popular" inferido.

## 3. Aliases (conhecimento persistido e determinístico)

Tabela nova, **dado, não código** — porque loja/usuário escrevem a
mesma marca/valor de formas diferentes ao longo do tempo, sem que isso
seja uma mudança de schema/categoria:

```python
class ProductIdentityAlias(Base):
    __tablename__ = "product_identity_aliases"
    __table_args__ = (
        Index(
            "uq_product_identity_aliases_scope_raw",
            "category", "attribute_name", "raw_value_normalized",
            unique=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    category: Mapped[str]
    attribute_name: Mapped[str]  # "brand", "board_brand", "model"...
    raw_value_normalized: Mapped[str]  # ex.: "ASUSTEK" (já normalize_for_matching)
    canonical_value: Mapped[str]  # ex.: "asus"
    status: Mapped[str]  # "active" | "candidate" (ver §4)
    created_at: Mapped[datetime]
```

Lookup determinístico: `raw_value_normalized → canonical_value`, sempre
`status="active"`. Nunca decidido pela IA em tempo de comparação — a IA
só pode **propor** uma linha nova (`status="candidate"`), nunca ativá-la
sozinha (§4). Isso resolve exatamente "ASUS"/"ASUSTeK"/"Asus" →
`board_brand=asus`, sem tocar no extractor nem na `monitoring_key`.

"Ryzen 9 9950X3D" == "9950X3D" **não é** um caso de alias — é o
extractor da categoria extraindo `model=9950x3d` de dentro de um texto
maior, descartando as palavras descritivas ("Ryzen 9", "Processador
AMD"). Isso já é o trabalho normal de qualquer `_Extractor` (mesmo
padrão de `_iphone`/`_galaxy_s` hoje, que já descartam "Apple"/"Samsung
Galaxy" do texto bruto).

## 4. Aprendizado incremental — separar conhecimento aprendível de decisão de equivalência

```
USER pede algo novo (categoria/atributo/valor não reconhecido)
  → IA interpreta o texto normalmente (sem mudança no papel dela)
  → Product Identity Engine tenta encaixar em CategoryDefinition
    existente
      → se encaixa com confiança: resolve normalmente (§1)
      → se NÃO encaixa: fail-closed --
          category desconhecida → GENERIC_CATEGORY, monitoring_key=None
          atributo/valor desconhecido dentro de categoria conhecida →
            registra candidato (ProductIdentityAlias.status="candidate"
            ou uma tabela irmã "categoria candidata"), a missão em si
            segue seu fluxo normal (GENERIC_CATEGORY ou PRODUCT_FAMILY,
            conforme o que JÁ resolveu), só não ganha monitoring_key
            ainda
  → ADMIN revisa candidatos (rotina operacional, não parte do fluxo de
    criação de missão) e PROMOVE explicitamente (ação humana/determinística
    -- vira "active", ou vira uma CategoryDefinition/AttributeDefinition
    nova via mudança de código revisada) -- nunca auto-promovido pela IA.
  → só DEPOIS da promoção, missões NOVAS (e o backfill, se rodado de
    novo) passam a compartilhar por esse conhecimento.
```

**Conhecimento aprendível** (pode crescer sem redesenho de banco/
arquitetura): categorias (`CategoryDefinition`, código versionado,
revisão normal de PR), aliases/valores (`ProductIdentityAlias`, dado,
crescimento orgânico), candidatos pendentes de promoção.

**Decisão de equivalência**: sempre a mesma função determinística
(`compute_monitoring_key`), nunca pula essa camada, nunca chama IA.

## 5. `monitoring_key`

Construída **depois** que a identidade já está resolvida pela UX
existente da TASK-097 (sem mudar essa UX) — reaproveita, não substitui,
`request_kind`/`requested_family_key`/`requested_identity_key`/
`requested_variant`/`variant_selection_mode`, que continuam gravados em
`MissionCriteria` exatamente como hoje:

- **`SPECIFIC_PRODUCT`** (todo atributo bloqueante resolvido) ou
  **`PRODUCT_FAMILY` com `variant_selection_mode = ALL`** (usuário
  declarou explicitamente "qualquer variante" — isso É uma restrição
  confirmada, não ambiguidade — TASK-097 já modela isso) ou
  **`PRODUCT_FAMILY` com `variant_selection_mode = SELECTED`** (usuário
  escolheu um `Product` global específico via `MissionProductSelection`)
  → identidade está **assentada**: `monitoring_key` computável.
- **`PRODUCT_FAMILY` com `variant_selection_mode = PENDING`** → ainda
  ambíguo do ponto de vista do PRÓPRIO usuário (a UX de seleção de
  variante da TASK-097 ainda não terminou) → `monitoring_key = None`
  até o usuário resolver (recomputada no momento em que `PENDING` vira
  `SELECTED`/`ALL` — o mesmo evento que já dispara hoje em código
  existente, `app/missions/service.py`).
- **`GENERIC_CATEGORY`** → o Product Identity Engine tentou e não
  conseguiu classificar em nenhuma `CategoryDefinition` conhecida (ou a
  categoria existe mas informação essencial não-bloqueante nenhuma foi
  extraída) → `monitoring_key = None`, fail-closed, sem exceção.

```
monitoring_key = "v1|" + category + "|" + brand + "|" + family + "|" + model
                + ("|" + variant if variant else "")
                + "".join(f"|{attr}={value_or_ANY}" for attr in
                           sorted(category_schema.attributes, key=name))
```

Hash SHA256 do payload canônico (mesmo padrão de `_key()` em
`identity.py` — compacto, sem limite de tamanho conforme atributos
crescem) **mais** o payload canônico em si guardado plano (JSON) em
`MonitoringItem.canonical_criteria` só para auditoria/depuração/exibição
no ADMIN — nunca usado para comparação (comparação é sempre pelo hash).

Regras fixas: mesma identidade estruturada → mesma chave; identidade
diferente (incluindo `ANY` vs valor) → chave diferente; versionada
(`v1|`, sobe pra `v2|` quando o schema de uma categoria muda —
recalcula só o que precisa, nunca colide com o formato antigo por
acidente); normalização sempre determinística (`normalize_for_matching`
default, ou a função registrada por atributo); **nenhuma IA e nenhum
fuzzy matching entram na comparação** — comparação é igualdade de string
de hash, ponto final.

## 6. Modelo de dados (Shared Monitoring)

> **Esta seção descreve o schema REAL implementado (fases 1/2/3A/3B).**
> O desenho anterior a qualquer código (histórico, nunca construído)
> previa `MissionCriteria.monitoring_item_id`, `MonitoringItemSchedule` e
> `MonitoringItemSource` como tabelas separadas — **nenhuma das três
> existe**. A implementação real fundiu agenda+backoff numa única tabela
> por `(item, loja)` e moveu o vínculo Mission→item para uma tabela
> própria, nunca uma coluna dentro de `MissionCriteria`. Ver `app/missions/
> models.py:290-424` para o código-fonte destas classes.

```
Mission (1) ── (1) MissionMonitoringItem ── (N) ─→ (1) MonitoringItem
   │                  (mission_id É a PK --                │
   │                   vínculo canônico único,              │
   │                   nunca em MissionCriteria)             │
   │                                                          ├─ (N) MonitoringItemStore
   └─ (N) MissionSource ←── preferência do USER               │    (PK composta: monitoring_item_id +
        (quais lojas ELE quer, cota TASK-107,                 │     store_id -- agenda E backoff
         SEM mudança de forma; caminho legado                 │     fundidos numa tabela só, por
         continua usando MissionSchedule)                     │     (item, loja), nunca por
                                                                │     (mission, loja))
                                                                ▼
                                                         CollectionRun
                                            (monitoring_item_id XOR mission_id, store_id,
                                             fairness_owner_user_id -- ver §12)
                                                                │
                                                                ▼
                                               Offer / PriceObservation (TASK-093, sem mudança)
                                                                │
                                                                ▼
                                         MissionOfferRelevance (mission_id, offer_id) --
                                         JÁ existe por missão (TASK-063): é o fan-out
                                         (§10), uma linha por missão vinculada.
```

`MonitoringItem` **não é `Offer`** (resultado da coleta, por loja) nem
`Product` (identidade cross-loja da TASK-097, só existe após alguma
coleta real) — é a representação do **pedido canônico de monitoramento
em si**, existe desde a criação da missão. Schema real
(`app/missions/models.py:290-324`):

```python
class MonitoringItem(Base):
    __tablename__ = "monitoring_items"
    __table_args__ = (
        CheckConstraint("identity_version > 0", ...),
        Index("uq_monitoring_items_monitoring_key", "monitoring_key", unique=True),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    monitoring_key: Mapped[str] = mapped_column(String(160), nullable=False)
    identity_version: Mapped[int]          # MONITORING_KEY_VERSION, para migração de schema futura
    canonical_identity: Mapped[dict]        # JSONB -- auditoria/ADMIN, nunca usado para comparação
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

**Vínculo Mission→MonitoringItem — `MissionMonitoringItem`, não uma
coluna em `MissionCriteria`** (`app/missions/models.py:327-353`):

```python
class MissionMonitoringItem(Base):
    __tablename__ = "mission_monitoring_items"
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True,
    )                                        # PK própria -- uma Mission nunca tem 2 vínculos
    monitoring_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitoring_items.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    created_at: Mapped[datetime]
```

`mission_id` sendo a própria chave primária é o que torna o vínculo
**canônico e único por definição** — não existe (e nunca existiu) um
segundo lugar onde uma Mission poderia apontar para um item diferente. A
ausência de linha nesta tabela para uma `mission_id` significa
"identidade não compartilhável ainda" (`monitoring_key = None`,
fail-closed) — a Mission continua 100% pelo caminho legado (`Mission
Schedule`), nunca um estado intermediário ambíguo.

**Necessidade agregada de coleta por `(item, loja)` — `MonitoringItemStore`**,
funde o papel de agenda (equivalente a `MissionSchedule`) e de backoff
(equivalente a `MissionSource.next_eligible_at`/`consecutive_blocks`,
DEC-046) numa tabela só, por item, nunca por missão
(`app/missions/models.py:356-424`):

```python
class MonitoringItemStore(Base):
    __tablename__ = "monitoring_item_stores"
    __table_args__ = (
        CheckConstraint("consecutive_blocks >= 0", ...),
        Index(
            "ix_monitoring_item_stores_due",
            "next_run_at", "monitoring_item_id", "store_id",
            postgresql_where="is_enabled",     # Bitmap Index Scan confirmado, ver §12
        ),
    )
    monitoring_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitoring_items.id", ondelete="CASCADE"), primary_key=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), primary_key=True,
    )                                       # PK composta (monitoring_item_id, store_id)
    is_enabled: Mapped[bool]                 # True enquanto >= 1 Mission ACTIVE vinculada exigir a loja
    next_run_at: Mapped[datetime | None]      # cadência (§12) -- quando reavaliar de novo
    last_run_at: Mapped[datetime | None]
    next_eligible_at: Mapped[datetime | None] # backoff DEC-046 -- vence sempre sobre a cadência
    consecutive_blocks: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

`MissionSource` (preferência de loja do usuário, cota TASK-107) **não
muda de forma** — continua existindo exatamente como antes; só deixou de
ser a fonte de backoff para missões vinculadas (que passa a ser
`MonitoringItemStore`). Nada aqui é apagado quando a última Mission
vinculada é pausada/cancelada — só `is_enabled` vira `False` e o
histórico (`next_run_at`/`next_eligible_at`/`consecutive_blocks`) para
de avançar, sem perder dado (§9).

`CollectionRun` ganha `monitoring_item_id: UUID | None` (`CHECK
ck_collection_runs_ownership_xor`: `mission_id` XOR `monitoring_item_id`,
nunca os dois, nunca nenhum) e `fairness_owner_user_id: UUID | None`
(fase 3B, detalhado no §12) — índice único parcial
`uq_collection_runs_running_monitoring_item_store` espelha o mesmo papel
que já existia para `mission_id`. Runs compartilhadas gravam
`monitoring_item_id` e deixam `mission_id = NULL`; runs do caminho
legado continuam com `mission_id` preenchido e `monitoring_item_id =
NULL`, exatamente como antes desta fase.

`MissionOfferRelevance(mission_id, offer_id)` **não muda em nada** —
ganhou só `last_observation_id` na fase 3A (DEC-048, fonte de "previous"
por Mission no fan-out compartilhado), documentado no §10/§11.

## 7. Criação/reconciliação de missão

> Fluxo REAL implementado. O ponto de entrada único é
> `reconcile_mission_monitoring_item(_async)` (`app/missions/monitoring.py`),
> nunca `resolve_or_create_monitoring_item` chamado direto por cada
> caller — evita exatamente a duplicação de lógica de vínculo que o
> desenho original já queria prevenir, só que resolvida numa camada acima
> da inicialmente prevista.

1. **Mission continua sendo entidade individual do usuário** — criada por
   `create_mission_from_criteria(_async)` (`app/missions/service.py`),
   sem mudança de forma; `MissionCriteria`/`MissionSource`/
   `MissionSchedule` continuam existindo exatamente como antes desta
   TASK.
2. **Product Identity Engine** (fase 1, `app.products.identity.
   resolve_monitoring_identity`/`resolve_monitoring_identity_for_family`/
   `resolve_monitoring_identity_for_resolved_product`) determina a
   identidade estruturada e a `monitoring_key` — determinístico, sem IA
   na decisão (§5). A identidade EFETIVA da missão segue a mesma
   precedência sempre: variante de `Product` selecionada >
   `VariantSelectionMode.ALL` > texto puro.
3. `reconcile_mission_monitoring_item(session, *, mission_id, criteria,
   mission_is_active, now)` (versão síncrona) /
   `reconcile_mission_monitoring_item_async(...)` (versão assíncrona) —
   **único ponto de entrada** para criar, trocar ou remover o vínculo:
   recalcula a identidade atual, resolve-ou-cria o `MonitoringItem`
   correspondente (`INSERT ... ON CONFLICT (monitoring_key) DO NOTHING` +
   `SELECT`, §8) e grava/atualiza a linha em `MissionMonitoringItem`
   (nunca uma coluna em `MissionCriteria` — ver §6).
4. **`MonitoringItemStore` é reconciliado a cada chamada** conforme as
   stores exigidas pelas Missions `ACTIVE` atualmente vinculadas ao item:
   cria a linha `(item, store)` se ainda não existir (`next_run_at =
   now`, `is_enabled = True`) e mantém `is_enabled` coerente — `True`
   enquanto ao menos uma Mission `ACTIVE` vinculada ainda exigir aquela
   loja, `False` quando a última sai. Se o item já existia com agenda em
   andamento, `next_run_at`/`next_eligible_at`/`consecutive_blocks`
   **nunca são reiniciados** por uma nova missão se juntando.
5. **Mission sem identidade compartilhável** (`monitoring_key = None`,
   fail-closed do Product Identity Engine — categoria desconhecida,
   `GENERIC_CATEGORY`, ou `PRODUCT_FAMILY` ainda `PENDING`) **continua
   100% pelo caminho legado**: nenhuma linha em `MissionMonitoringItem`,
   agendamento via `MissionSchedule` como sempre foi.
6. **Todo caller que pode alterar a identidade relevante de uma missão já
   criada** chama `reconcile_mission_monitoring_item(_async)` de novo —
   criação, seleção/confirmação de variante (`PENDING → SELECTED/ALL`,
   TASK-097), pós-coleta (resolução de `Product` global), e
   desidentificação de conta (`app.privacy.service.deidentify_account`).
   Nunca existe um segundo caminho duplicando essa lógica.
7. **Pause/cancel/remoção de vínculo nunca destrói histórico
   compartilhado**: `MonitoringItem`/`MonitoringItemStore` persistem
   mesmo quando a última Mission vinculada é cancelada — só
   `is_enabled` muda (§9). Cancelar/pausar uma Mission nunca é em
   cascata sobre outras Missions vinculadas ao mesmo item.
8. **Uma loja compartilhada permanece ativa enquanto qualquer outra
   Mission `ACTIVE` ainda depender dela** — a saída de uma Mission
   específica nunca desliga `MonitoringItemStore.is_enabled` sozinha se
   outra Mission ainda precisa da mesma `(item, store)`.

**Coexistência permanente, nunca as duas ao mesmo tempo para a mesma
necessidade de coleta**:

```
Mission com MissionMonitoringItem → caminho compartilhado (MonitoringItemStore).
Mission sem MissionMonitoringItem → caminho legado (MissionSchedule).
```

Backfill de missões antigas (ligar retroativamente missões já existentes
sem vínculo) fica fora do escopo desta fase — ver §14.

## 8. Concorrência

Dois usuários criando a mesma `monitoring_key` ao mesmo tempo: mesmo
padrão já comprovado sob concorrência real em produção
(`_resolve_offer`, `app/collection/orchestration.py`, TASK-079) —
`INSERT ... ON CONFLICT (monitoring_key) DO NOTHING` dentro de
`session.begin_nested()` (savepoint), seguido de `SELECT` se o insert
não afetou linha. Não é mecanismo novo, é reaplicação da MESMA solução
já usada para `Offer`/`Product`.

## 9. Pause / Resume / Cancel

> Corrigido (achado durante a reescrita do §6/§7): o mecanismo real não é
> "elegibilidade derivada por query" como o desenho original previa — é
> um par de helpers dedicados, chamados pelo mesmo `transition_mission(_
> async)` que já existia, cobrindo uniformemente qualquer chamador
> (ADMIN, criação de missão via `ACTIVATE` interno, comandos diretos).

- **PAUSE/CANCEL**: `transition_mission(_async)` (`app/missions/
  service.py`) chama `deactivate_monitoring_item_stores_if_unneeded(
  session, mission_id=..., now=...)` sempre que o status anterior era
  `ACTIVE` e o novo não é — desliga `MonitoringItemStore.is_enabled` para
  cada `(item, loja)` que esta Mission exigia, **só se nenhuma outra
  Mission `ACTIVE` vinculada ao mesmo item ainda precisar daquela loja**.
  `MissionSchedule.is_enabled = False` continua acontecendo à parte para
  o caminho legado (cancelamento), sem mudança. **Nada é apagado** — o
  histórico (`next_run_at`/`next_eligible_at`/`consecutive_blocks`) só
  para de avançar.
- **RESUME/ACTIVATE**: `activate_monitoring_item_stores(session,
  mission_id=..., now=...)` religa (ou cria, se ainda não existia)
  `MonitoringItemStore.is_enabled = True` para cada `(item, loja)` que a
  Mission volta a exigir — nunca precisa de intervenção manual.
- **Cancelamento nunca é em cascata**: cancelar Mission A nunca afeta
  Mission B só por compartilharem item — `deactivate_monitoring_item_
  stores_if_unneeded` sempre recheca se ainda existe outra Mission
  `ACTIVE` antes de desligar qualquer coisa.

## 10. Fan-out

Achado do audit: **a persistência já suporta isso hoje, quase sem
mudança**. `MissionOfferRelevance(mission_id, offer_id)` já existe
(TASK-063) exatamente para a mesma `Offer` ser `MATCH` para uma missão e
`NO_MATCH` para outra. Falta só a Fase C parar de assumir uma missão por
`CollectionRun`:

```
_persist_phase_a: resolve por (monitoring_item_id, store_id).
  Offer/PriceObservation continuam por Offer (TASK-093, §11).

_run_phase_b: roda 1x por oferta coletada -- igual a hoje.

_persist_phase_c: para cada oferta coletada, para CADA missão ativa
  vinculada ao item que também requer aquela loja: resolve relevância
  PARA AQUELA missão (MissionOfferRelevance como hoje -- necessário
  porque search_query pode diferir palavra-por-palavra entre missões
  vinculadas, ex. "9950x3d" vs "ryzen 9950x3d", então MATCH/NO_MATCH não
  pode ser assumido igual para todas); se MATCH, resolve "previous"
  ESCOPADO a essa missão (preserva DEC-048, §11) e avalia alerta PARA
  ESSA missão (target_amount por missão). finish_collection_run(...,
  SUCCEEDED) 1x, para o monitoring_item_id.
```

Coleta (rede/navegador) 1x; gravação de `Offer`/`PriceObservation` 1x;
relevância/alerta/pré-lista/notificação continuam 1x **por missão
vinculada** (mais CPU/DB local por claim bem-sucedida, zero rede extra —
exatamente o recurso caro que esta TASK/TASK-108 protegem).

## 11. Impacto na TASK-093 (dedupe de `PriceObservation`)

**Nenhuma mudança na lógica de dedupe em si** — `_same_commercial_state`/
`_installment_snapshot` já comparam por `Offer`, agnósticos de missão.

O que precisa de atenção (já preservado pelo desenho do §10): a consulta
de "previous" observation continua escopada por `mission_id`, nunca por
`monitoring_item_id` — proteção do DEC-048
(`test_target_reached_state_does_not_leak_between_missions_sharing_an_
offer`), necessária mesmo entre missões vinculadas: `target_amount` é
por missão, e um usuário que acabou de vincular uma missão nova a um
item já monitorado há tempos não pode herdar o histórico de alerta de
quem já monitorava. `PriceObservationComparison`/`_persist_phase_a`
(DEC-097) já são o mecanismo certo — passam a rodar 1x por missão
vinculada dentro do fan-out do §10, em vez de 1x total.

## 12. Integração com TASK-108 (fila justa / throttle / cadência)

> Esta seção descreve o scheduler FINAL implementado (fase 3B,
> `app.collection.orchestration.claim_due_work`, `app.collection.
> fairness`, `app.collection.shared_claim`, `app.collection.cadence`),
> não o algoritmo "creditar o fairness_owner depois do claim" do desenho
> original — substituído por um mecanismo de reserva por lock real,
> explicado abaixo, depois de o usuário provar um cenário concreto de
> deadlock cruzado contra a versão anterior.

### 12.1 Três mecanismos distintos — nunca confundir

| Mecanismo | Tabela | Decide | Escopo |
|---|---|---|---|
| Fairness entre usuários | `UserCollectionQueueState` | **QUEM** pode consumir capacidade agora (cooldown por usuário) | por `user_id`, TASK-108 |
| Cadência de monitoramento | `MonitoringItemStore.next_run_at` (compartilhado) / `MissionSchedule.next_run_at` (legado) | **QUANDO** uma necessidade específica de coleta precisa ser revisitada de novo | por `(item, loja)` ou por missão |
| Pacing por loja | `StoreThrottleState` | proteção contra rajada de acessos à MESMA loja entre alvos diferentes no MESMO ciclo | por `store_id`, global, TASK-108 |

Os três coexistem sempre; nenhum substitui o outro. Backoff por bloqueio
externo confirmado (DEC-046, `next_eligible_at` em `MonitoringItemStore`/
`MissionSource`) fica fora dos três — vence sempre, estruturalmente: o
claim recusa um recurso cujo `next_eligible_at` ainda não passou, mesmo
que `next_run_at` (cadência) já esteja due.

### 12.2 Unidade de trabalho e caminho legado

A unidade de trabalho compartilhada deixa de ser "1 claim = 1 missão" e
passa a ser "1 claim = 1 `(monitoring_item, store)`, que pode servir N
missões de M usuários diferentes". O caminho legado continua "1 claim =
1 `(mission, store)`" sem nenhuma mudança de unidade — as duas unidades
participam da **mesma fila de fairness**, nunca filas separadas com
níveis de proteção diferentes.

`claim_due_collections`/`_select_due_schedules_for_batch`
(`app/collection/orchestration.py`) são **compatibility/legacy-only**:
continuam existindo quase sem mudança (só ganharam o anti-join contra
`MissionMonitoringItem`, para nunca reivindicar uma Mission já vinculada
ao caminho compartilhado) para quem ainda as chama direto — auditado:
hoje só testes, nenhum runtime de produção. **Nunca criam efeito
colateral compartilhado** (nenhuma `CollectionRun` com
`monitoring_item_id`, nenhum avanço de `MonitoringItemStore`).
`CollectionOrchestrator`, o caminho de produção real
(`app/collection/worker.py`), usa exclusivamente `claim_due_work`.

### 12.3 `fairness_owner` — reserva por lock real, antes de qualquer loja

Cada claim compartilhada tem exatamente **um** dono de fairness,
escolhido entre os usuários elegíveis (fora de cooldown) com Mission
`ACTIVE` vinculada ao item e `MissionSource` para aquela loja — nunca
"creditar todos os vinculados" (penalizaria um usuário vinculado a vários
itens de frequências diferentes por trabalho que ele não "possui" no
turno).

`claim_due_work` roda em duas fases estritas, sempre na mesma ordem, para
nunca permitir o ciclo "transação A espera lock de usuário que B segura;
B espera lock de loja que A segura":

1. **Fase 1 — reserva de donos, sempre antes de qualquer loja**
   (`app.collection.fairness._reserve_fairness_owners`): para cada
   usuário candidato (união dos donos do caminho legado e do
   compartilhado, já ordenada por `queue_sort_key`), adquire
   `SELECT ... FOR UPDATE SKIP LOCKED` em `UserCollectionQueueState`, em
   ordem ascendente de `user_id` — nunca espera; um usuário cujo lock já
   está com outra transação é simplesmente pulado nesta rodada, nunca
   bloqueia. **Recheca `next_eligible_at` (cooldown) sob o lock já
   adquirido**, nunca confia só na seleção otimista anterior — um usuário
   cuja seleção ficou desatualizada entre a leitura e o lock (outra
   transação concorrente já avançou o cooldown dele) é rejeitado aqui,
   não silenciosamente aceito.
2. **Fase 2 — claims de recurso, só para donos já reservados** — Mission
   +loja (legado) e item+loja (compartilhado) numa lista única, ordenada
   por `(store_id, due_at, kind, resource_id)`: lojas sempre em ordem
   consistente entre transações concorrentes, `kind` só como desempate
   final, nunca prioridade estrutural do legado sobre o compartilhado (ou
   vice-versa).

Só quem teve **>= 1 claim real** na Fase 2 recebe o avanço de cooldown
(`app.collection.fairness._commit_fairness_turn_for_owner`, `UPDATE`
simples — o lock da Fase 1 já garante exclusão mútua, sem precisar de
CAS/token comparado). **Um dono reservado sem nenhum claim real** (loja
em throttle, corrida perdida para outro worker) **não paga cooldown
algum** — `UserCollectionQueueState` permanece exatamente como estava.
`UserCollectionQueueState.last_fairness_turn_id` é só rastro de
auditoria (qual ciclo creditou o avanço), nunca o mecanismo de exclusão
mútua em si — isso é o lock contínuo da Fase 1.

Os demais usuários vinculados (**riders**) recebem o resultado via
fan-out (§10) mas o próprio `UserCollectionQueueState` deles nunca muda —
não "gastam" turno, continuam na mesma posição de fila para qualquer
outra coisa que dependa deles, vinculada ou não.

### 12.4 `CollectionRun.fairness_owner_user_id`

Gravado na mesma transação/savepoint do claim compartilhado
(`app.collection.shared_claim._claim_shared_collection_in_session`):
**sempre preenchido** no caminho orquestrado (`claim_due_work` nunca
constrói uma tentativa compartilhada sem dono já resolvido pela Fase 1);
**sempre `NULL`** no caminho legado e também no caminho compartilhado
STANDALONE (`collect_monitoring_item_store` chamada fora do
orchestrator — scripts, ADMIN, testes — sem passar `fairness_owner_
user_id`: nunca toca `UserCollectionQueueState`, `NULL` documenta
explicitamente "execução fora da fila de fairness", nunca "o scheduler
esqueceu de gravar"). FK `users.id`, `ondelete="RESTRICT"` — auditado
`app.privacy.service.deidentify_account`: nunca apaga a linha `User`, só
remove credenciais/`telegram_user_id`, a FK nunca fica pendurada na
prática. `CHECK ck_collection_runs_fairness_owner_requires_shared`
impede `fairness_owner_user_id` preenchido numa run do caminho legado.

`StoreThrottleState`/`_advance_store_throttle` **não mudam em nada** —
já são globais por `store_id` e valem por igual para os dois caminhos
(legado e compartilhado); "1 claim = 1 acesso à loja" já é garantido por
definição, sem nenhum tratamento especial por tipo de claim.

### 12.5 Política de cadência (`app.collection.cadence`)

Módulo novo da fase 3B — decide o intervalo até a próxima coleta de uma
necessidade já bem-sucedida, aplicado **igualmente ao caminho
compartilhado** (`MonitoringItemStore.next_run_at`, via `resolve_
collection_cadence`) **e ao caminho legado** (`MissionSchedule.
next_run_at`, via `resolve_legacy_schedule_next_run_at` — usa a decisão
mais CEDO entre as lojas reivindicadas naquele ciclo para a missão, nunca
a mais tarde) — coexistência permanente nunca significou duas classes de
proteção diferentes.

Prioridade fixa (backoff DEC-046 já tratado fora, sempre vence,
estrutural — ver 12.1):

| Modo | Faixa | Critério |
|---|---|---|
| `PROMO_CALENDAR` | 30–45 min | `now` dentro de alguma `PromotionalWindow` ativa |
| `HIGH_ACTIVITY` | 30–45 min | loja com >= `high_activity_change_threshold` (padrão 3) mudanças comerciais reais numa janela de `high_activity_window_minutes` (padrão 30min), OU ainda dentro da histerese `StoreActivityState.high_activity_until` |
| `NORMAL` | 45–75 min | nenhum dos anteriores (alvo conceitual ~60min) |

Piso absoluto de 30 minutos reforçado em `CadenceConfig.__post_init__` —
nenhum modo, nem uma futura diferenciação de plano pago, pode baixar
disso.

**`HIGH_ACTIVITY` é sempre por loja** (nunca por produto/item individual,
nunca IA, nunca estatística sofisticada), baseado **somente** em mudanças
comerciais reais: uma `PriceObservation` só existe quando o estado
comercial mudou de verdade (TASK-093/DEC-097) — mas isso vale tanto para
`CHANGED` quanto para a PRIMEIRA observação de uma `Offer`
(`FIRST_OBSERVATION`). A detecção exclui `FIRST_OBSERVATION` via `NOT
EXISTS` (só conta observação que tem uma observação mais antiga para a
MESMA `Offer` — prova de mudança real, nunca chegada nova); `UNCHANGED_
REUSED` nunca gera linha nova, então nunca entra na contagem por
construção. `StoreActivityState.high_activity_until` guarda só a
HISTERESE (evita alternar `NORMAL`/`HIGH_ACTIVITY` a cada ciclo bem na
borda do limiar) — nunca cacheia a contagem em si, que é sempre
recomputada ao vivo. Sem schema novo dedicado à contagem.

`PromotionalWindow` é **dado, não código** — tabela (`label`, `starts_at`,
`ends_at`) para o calendário promocional (datas duplas, Black Friday,
Cyber Monday, futuras); ADMIN insere/remove linhas sem NENHUMA migration
nova. Não existe hoje UI ADMIN dedicada para gerenciar janelas — inserção
é operacional (SQL/script), documentado aqui para não presumir uma UI que
ainda não foi construída.

**Plano pago pode aumentar `max_active_missions`/store slots (TASK-107);
nunca aumenta a frequência de coleta de uma mesma necessidade** — cota
controla capacidade do usuário (quantas missões/lojas ele pode ter),
nunca a velocidade da mesma busca. `CadenceConfig` é global, sem
diferenciação por plano.

### 12.6 Índice e verificação de escala

`ix_monitoring_item_stores_due` (`next_run_at`, `monitoring_item_id`,
`store_id`, `WHERE is_enabled`) — `EXPLAIN ANALYZE` contra 5000
`MonitoringItemStore` sintéticos (2% due) confirma `Bitmap Index Scan`,
execução sub-milissegundo. `next_eligible_at` não entrou no índice por
desenho: a seletividade real é dominada por `next_run_at`, o filtro extra
sobre a fração já due é barato mesmo fora do índice.

`max_concurrent_user_batches` continua limitando quantos `fairness_
owner`s distintos entram no lote — semântica idêntica à de antes desta
fase (contagem de "donos", nunca de vinculados totais).

Cobertura de teste desta seção:
`tests/integration/test_unified_fair_queue.py` (reserva/lock, recheck de
cooldown sob lock, dono sem claim não paga cooldown, riders nunca têm o
próprio estado alterado, ausência de deadlock cruzado sob concorrência
real) e `tests/integration/test_cadence_and_high_activity.py`
(`NORMAL`/`PROMO_CALENDAR`/`HIGH_ACTIVITY`/histerese/exclusão de
`FIRST_OBSERVATION`, cadência legado usando a decisão mais cedo entre
lojas).

## 13. Impacto na TASK-107 (cotas)

**Nenhuma mudança.** `resolve_quota_limits`/`max_active_missions`/
`max_store_slots`/`max_daily_searches` continuam contando `Mission`/
`MissionSource` do próprio usuário, exatamente como hoje — sem nenhuma
relação com vínculo de item. Vincular-se a um item já monitorado não
torna a missão mais barata em cota; o usuário ainda gasta 1 slot de
missão e 1 slot de loja por `MissionSource` que criar. O único efeito é
que o TRABALHO DE COLETA por trás fica mais barato para o sistema, nunca
a cota do usuário. Nenhum plano/tier novo.

## 14. Migração / backfill

Sem perda de dado em nenhum passo; regra dura: **só vincula quando o
Product Identity Engine reconstrói deterministicamente a mesma
`monitoring_key` — nunca merge aproximado; ambiguidade = mantém
separado**.

1. **Migration 1** (puramente aditiva): `monitoring_items`,
   `monitoring_item_schedules`, `monitoring_item_sources`,
   `product_identity_aliases`; `mission_criteria.monitoring_item_id`
   (nullable) e `collection_runs.monitoring_item_id` (nullable) +
   índice único parcial espelhado (§6). Zero risco para o que já existe.
2. **Backfill**: para cada `MissionCriteria` de missão `ACTIVE`/`PAUSED`,
   calcula `monitoring_key` com o motor **na versão atual**; agrupa por
   chave; acha-ou-cria `MonitoringItem` por chave distinta (mesmo
   `ON CONFLICT` do §8); seta `monitoring_item_id`. Vincula
   retroativamente missões já idênticas, de graça. Ambíguo/
   `monitoring_key = None` → fica sem vínculo, exatamente como uma
   missão nova cairia — nunca força um vínculo de baixa confiança.
3. **Backfill de `MonitoringItemSource`/`MonitoringItemSchedule`**: por
   `(item, store)` recém-vinculado, `next_eligible_at`/
   `consecutive_blocks` = o **pior caso** (mais no futuro / maior
   contagem) entre os `MissionSource` originais daquela loja — nunca
   destrói um backoff que protegia a loja. `next_run_at` = o **mais
   próximo** entre os `MissionSchedule` originais — nunca atrasa
   ninguém que já esperava coleta iminente.
4. **Cutover no código** (scheduler + Fase A/C, commit coeso, atrás de
   testes de integração dedicados): passa a agendar/coletar por
   `(monitoring_item_id, store_id)`. Colunas antigas de agenda/backoff
   ficam não lidas pelo worker a partir daqui, não apagadas ainda.
5. **`CollectionRun` histórico**: intocado — `mission_id` preenchido,
   `monitoring_item_id = NULL` para sempre, fato histórico.
6. **Limpeza** (migration separada, só depois de validar em produção por
   um ciclo de deploy inteiro): `mission_criteria.monitoring_item_id`
   vira `NOT NULL`; `DROP COLUMN` das colunas de agenda/backoff antigas.

## 15. Riscos

- **Fairness da fila (§12)** continua o maior risco técnico — mesmo com
  dono único (mais simples que "creditar todos"), tem borda real:
  troca de dono entre ciclos, todos os vinculados em cooldown ao mesmo
  tempo, usuário se desvincula no meio de uma claim já selecionada.
  Precisa de testes de integração dedicados antes de merge.
- **Motor de identidade é trabalho real e significativo, não incremental
  pequeno** — cobrir bem 5-10 categorias com atributos corretos
  (bloqueante vs `ANY`) exige conhecimento de domínio por categoria
  (o que realmente diferencia produtos aos olhos de quem compra), não só
  parsing de texto. Risco de sub-especificar uma categoria (marcar algo
  bloqueante como `ANY` por engano, ou vice-versa) e vincular missões
  que o usuário não considera equivalentes.
- **Dois pontos de entrada de criação de missão** (síncrono/Telegram,
  async/Web) precisam do mesmo tratamento — risco real de implementar só
  em um. Mitigação: helper único compartilhado (§7), testado nos dois
  call sites.
- **`_persist_phase_c` fica mais cara por claim compartilhada** (loop
  extra por missão vinculada) — aceitável (CPU/DB local, não rede), mas
  precisa de teste de carga antes de produção com uso real alto.
- **Aliases/candidatos exigem rotina operacional nova** (alguém — ADMIN
  — precisa revisar e promover candidatos periodicamente) — sem isso, o
  "aprendizado incremental" nunca avança e tudo que cai fora do registry
  inicial fica fail-closed para sempre. Isso é seguro (nunca erra por
  vincular errado), mas é trabalho humano recorrente que precisa existir
  de fato, não só no papel.

## Bloqueadores antes de começar código

1. **Aprovação explícita do desenho de fairness (§12)** — é a peça com
   maior potencial de comportamento sutilmente injusto em produção; não
   deve ser implementada sem revisão dedicada.
2. **Decisão de onde vive o Product Identity Engine** — evoluir
   `app/products/identity.py` no lugar (proposto) vs extrair um pacote
   novo (`app/products/identity_engine.py` ou módulo próprio) — decisão
   de organização de código, não de arquitetura, mas melhor travada
   antes do primeiro PR para não gerar retrabalho de import.
3. **Confirmar as primeiras categorias do registry (§1.3)** — cobrir
   todas de uma vez não é razoável; precisa de uma lista curta e
   priorizada para a primeira entrega (proponho `cpu`+`gpu` primeiro,
   por serem os exemplos do próprio pedido e terem alto volume de
   monitoramento de preço esperado).
4. **Quem revisa/promove candidatos de alias/categoria (§4)** — papel
   operacional novo, precisa de dono definido (ADMIN via painel? Só
   engenharia via PR?) antes do mecanismo de aprendizado incremental
   fazer sentido em produção.

## Ordem recomendada de implementação

1. Product Identity Engine — ontologia (`CategoryDefinition`/
   `AttributeDefinition`) + registry com `cpu`/`gpu` (as 2 categorias do
   pedido) + iPhone/Galaxy S migrados para o mesmo registry (sem mudar
   comportamento) + testes unitários exaustivos (todas as combinações
   de atributo bloqueante/`ANY`/valor). Isolado, sem tocar em mais nada.
2. `ProductIdentityAlias` (tabela + lookup determinístico) + testes,
   ainda isolado.
3. `compute_monitoring_key` sobre o motor + testes exaustivos
   (`request_kind` × `variant_selection_mode` × atributos presentes/
   `ANY`/ausentes).
4. Modelo de dados (§6) + migration 1 (aditiva) + testes de modelo.
5. Criação de missão vinculando/criando `MonitoringItem` (§7/§8) nos
   dois call sites, helper compartilhado, teste de corrida real.
6. Pause/Resume/Cancel (§9) — query de elegibilidade derivada + testes.
7. Cutover do scheduler/coleta (§10/§12) — maior risco, testado em
   isolamento antes de tocar em dashboard ADMIN ou UX visível.
8. Backfill (§14, passos 2-3) contra snapshot real (staging), validado,
   só então promovido.
9. Limpeza de colunas antigas (§14, passo 6) — depois de um ciclo de
   deploy inteiro estável, tarefa separada.

## Fora de escopo

Implementação de código nesta rodada (só desenho). Cobertura completa de
todas as categorias listadas no §1.3 de uma vez (entra incrementalmente,
por demanda). Qualquer mudança na lógica de dedupe de `PriceObservation`
em si (TASK-093, inalterada) ou no pacing global por loja
(`StoreThrottleState`, TASK-108, inalterado). Sistema de planos/tiers
novo (TASK-107, inalterado). Interface de revisão de candidatos para o
ADMIN (mecanismo de dado existe, painel/fluxo de aprovação fica para
quando o aprendizado incremental for de fato implementado).
