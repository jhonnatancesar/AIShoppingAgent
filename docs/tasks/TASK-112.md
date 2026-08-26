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

Fase 3A concluída (2026-08-26, rodada 4 de correções), aguardando
commit: prova que UMA necessidade `(MonitoringItem, store)` executa UMA
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
produção -- ainda não integradas ao loop de produção. Ver
`docs/internal/decision-log.md` para o registro completo de decisões.**

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

```
Mission (1) ── (1) MissionCriteria ── (N) ─→ (1) MonitoringItem
   │                                               │
   └─ (N) MissionSource ←── preferência do USER    ├─ (1) MonitoringItemSchedule
        (quais lojas ELE quer, cota TASK-107,       │    (quando reavaliar de novo --
         SEM mudança de forma)                       │     era 1 por Mission, agora 1 por item)
                                                       │
                                                       └─ (N) MonitoringItemSource
                                                            (backoff DEC-046 por (item, loja) --
                                                             era por (mission, loja))
                                                                 │
                                                                 ▼
                                                         CollectionRun
                                                     (monitoring_item_id, store_id)
                                                                 │
                                                                 ▼
                                               Offer / PriceObservation (TASK-093, sem mudança)
                                                                 │
                                                                 ▼
                                         MissionOfferRelevance (mission_id, offer_id) --
                                         JÁ existe por missão (TASK-063): é o fan-out
                                         pronto, só falta a Fase C iterar (§9).
```

`MonitoringItem` **não é `Offer`** (resultado da coleta, por loja) nem
`Product` (identidade cross-loja da TASK-097, só existe após alguma
coleta real) — é a representação do **pedido canônico de monitoramento
em si**, existe desde a criação da missão.

```python
class MonitoringItem(Base):
    __tablename__ = "monitoring_items"
    __table_args__ = (Index("uq_monitoring_items_key", "monitoring_key", unique=True),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    monitoring_key: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_criteria: Mapped[dict] = mapped_column(JSONB, nullable=False)  # auditoria/ADMIN
    created_at: Mapped[datetime]
```

`MissionCriteria` ganha `monitoring_item_id: UUID | None` (FK `ON DELETE
RESTRICT`, nullable durante a migração — ver §13; missão nova sempre
resolve, mesmo que para um item "sozinho" quando `monitoring_key` é
`None`, que nesse caso NÃO cria `MonitoringItem` nenhum — fica `NULL`
mesmo, sem custo de linha extra por missão que nunca compartilha nada).
Muitas `MissionCriteria` apontam para o mesmo item — N:1 direto, sem
tabela de junção (uma missão nunca precisa de mais de um item).

`MonitoringItemSchedule`/`MonitoringItemSource` espelham exatamente
`MissionSchedule`/`MissionSource` de hoje, só rescopados de `mission_id`
para `monitoring_item_id` — `MissionSource` em si **continua existindo
sem mudança de forma**, só deixa de guardar backoff (que muda de dono).

`CollectionRun` ganha `monitoring_item_id: UUID | None`. Achado
favorável do audit: `CollectionRun.mission_id` **já é nullable hoje**
(com índice único parcial `postgresql_where="status = 'running' AND
mission_id IS NOT NULL"`) — o schema já tem essa flexibilidade, sem
`ALTER COLUMN ... DROP NOT NULL` em produção. Runs novas gravam
`monitoring_item_id` e deixam `mission_id = NULL`; runs históricas
continuam com `mission_id` preenchido, intocadas. Novo índice espelhado:
`(monitoring_item_id, store_id)` único parcial para `status='running'`.

`MissionOfferRelevance(mission_id, offer_id)` **não muda em nada**.

## 7. Criação de missão

Dentro da mesma transação que já cria `Mission`/`MissionCriteria`/
`MissionSource`/`MissionSchedule` (`create_mission_from_criteria(_async)`,
`app/missions/service.py` — os dois pontos de entrada, síncrono/Telegram
e async/Web, precisam do MESMO tratamento; helper único
`resolve_or_create_monitoring_item(session, criteria)` chamado pelos
dois, para não duplicar a lógica de vínculo em dois lugares que podem
divergir):

```
USER input → IA interpreta/extrai (inalterado)
  → MissionCriteria criada, request_kind/family_key/identity_key/model
    resolvidos pela UX já existente da TASK-097 (inalterado)
  → Product Identity Engine calcula monitoring_key (novo, síncrono,
    sem IA -- §5)
  → monitoring_key is None: MissionCriteria.monitoring_item_id fica NULL,
    comportamento de hoje, sem risco
  → senão: achar-ou-criar MonitoringItem por monitoring_key
  → MissionCriteria.monitoring_item_id = item.id
  → para cada store pedida (MissionSource, inalterado): upsert
    MonitoringItemSource(item.id, store_id) se ainda não existir
  → item novo: cria MonitoringItemSchedule(item.id, next_run_at=agora)
  → item já existia: NÃO mexe no schedule -- já está rodando
```

## 8. Concorrência

Dois usuários criando a mesma `monitoring_key` ao mesmo tempo: mesmo
padrão já comprovado sob concorrência real em produção
(`_resolve_offer`, `app/collection/orchestration.py`, TASK-079) —
`INSERT ... ON CONFLICT (monitoring_key) DO NOTHING` dentro de
`session.begin_nested()` (savepoint), seguido de `SELECT` se o insert
não afetou linha. Não é mecanismo novo, é reaplicação da MESMA solução
já usada para `Offer`/`Product`.

## 9. Pause / Resume / Cancel

- **PAUSE/CANCEL**: `transition_mission` (`app/missions/service.py`)
  **sem nenhuma mudança**. O que passa a rodar depois: elegibilidade de
  `(monitoring_item_id, store_id)` para agendamento é **derivada por
  query** (EXISTS `MissionCriteria` com esse `monitoring_item_id`, cuja
  `Mission.status == ACTIVE`, com `MissionSource` para aquela loja) —
  nunca um flag persistido que precisa ser mantido em dia por
  trigger/cascade. Se não existir mais nenhuma missão ativa exigindo
  aquela combinação, ela simplesmente para de aparecer na query de claim
  (§11). Se o item inteiro não tem mais nenhuma loja com missão ativa,
  `MonitoringItemSchedule.is_enabled = False`. **Nada é apagado.**
- **RESUME**: reativa a `Mission` — a query derivada volta a enxergá-la
  automaticamente, sem precisar "religar" nada explicitamente.
- **Cancelamento nunca é em cascata**: cancelar Mission A nunca afeta
  Mission B só por compartilharem item.

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

## 12. Integração com TASK-108 (fila justa / throttle)

Ponto mais delicado do desenho: a unidade de trabalho deixa de ser "1
claim = 1 missão" e passa a ser "1 claim = 1 `(monitoring_item, store)`,
que pode servir N missões de M usuários diferentes".

**`fairness_owner` — decisão revisada** (não "creditar todos"): cada
claim compartilhada tem exatamente **um** dono de fairness, escolhido
deterministicamente entre os usuários **elegíveis** (fora de cooldown)
com missão ativa vinculada:

```
_select_due_schedules_for_batch (rescopado a MonitoringItemSchedule):
  para cada (item, store) due:
    linked_users = usuários com Mission ACTIVE, MissionCriteria.
      monitoring_item_id = item, MissionSource para essa store
    eligible = linked_users sem cooldown ativo (UserCollectionQueueState.
      next_eligible_at IS NULL OR <= due_at)
    se eligible vazio: claim NÃO elegível ainda -- aguarda o primeiro
      dos linked_users ficar elegível (nunca escolhe alguém em cooldown)
    fairness_owner = argmin(eligible, key=mesmo sort_key de hoje:
      last_processed_at IS NULL primeiro, depois mais antigo, depois
      user_id como desempate estável)
    a claim herda o sort_key do fairness_owner para o round-robin geral
  -- mesmo algoritmo de hoje (agrupar por "dono", ordenar, cortar em
     max_users, incluir tudo que pertence aos donos selecionados) --
     só que "dono" agora é o fairness_owner por claim, não o user_id
     direto de uma MissionSchedule.

depois de uma claim bem-sucedida:
  _advance_user_queue_state roda SÓ para o fairness_owner.
  Os demais linked_users (riders) recebem o resultado via fan-out (§10)
  mas o PRÓPRIO UserCollectionQueueState deles não muda -- eles não
  "gastaram" o turno, continuam com a MESMA posição de fila que tinham
  antes para qualquer outra coisa (vinculada ou não) que dependa deles.
```

Por que dono único (e não "créditar todos", desenho anterior descartado):
creditar cooldown para todo `user_id` vinculado penalizaria um usuário
vinculado a vários itens compartilhados de frequências diferentes --
cada colheita de QUALQUER item que ele nem "possui" no turno empurraria
o cooldown dele, podendo starvar as missões próprias dele sem relação
nenhuma com esses itens. Dono único elimina esse efeito colateral: um
usuário só paga cooldown pelo trabalho que ele de fato consumiu como
dono, nunca por carona de outros.

"Nenhum usuário ganha prioridade extra por estar vinculado a mais
gente" fica garantido porque: (1) o `fairness_owner` é sempre só UM,
nunca importa se há 2 ou 100 vinculados; (2) os outros 99 não têm seu
próprio estado de fila alterado por essa claim, então não "leapfroggeiam"
ninguém de propósito; (3) uma claim nunca entra na fila mais de uma vez
— é uma linha só na seleção, independente de quantos usuários dependem
dela.

`StoreThrottleState`/`_advance_store_throttle` **não mudam em nada** —
já são globais por `store_id`; "1 claim = 1 acesso à loja" já é
garantido por definição.

`max_concurrent_user_batches` continua limitando quantos
`fairness_owner`s distintos entram no lote — semântica idêntica à de
hoje (contagem de "donos", não de linked_users totais).

Esta seção precisa de testes de integração dedicados antes de qualquer
merge, nos moldes de `test_fair_queue_*`/`test_store_throttle_*` já
existentes, cobrindo pelo menos: dono elegível com riders em cooldown;
todos os linked_users em cooldown (claim não selecionável); dono troca
de ciclo pra ciclo conforme cooldowns evoluem; rider nunca tem o próprio
`UserCollectionQueueState` alterado por claim da qual não é dono.

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
