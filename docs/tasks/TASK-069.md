# TASK-069 — Editar missão existente (lojas e/ou preço-alvo)

Status: **Concluída em 2026-08-10**, desenho aprovado explicitamente pelo
usuário (incluindo uma instrução adicional sobre o fluxo de pausa antes
de editar), implementada e validada com pipeline oficial e testes de
integração real (PostgreSQL).

Dependência: nenhuma direta. Última TASK da `v1.0.2`
(`docs/V1_0_2.md`, item 3; `DEC-057`); TASK-065/066/067/068 já
concluídas. É o item de maior superfície de mudança — domain model,
confirmação por IA e efeito sobre agenda/histórico.

## Contexto

`DEC-057` registrou o pedido do usuário e deixou o mecanismo
explicitamente em aberto: *"novo `MissionCommand`, fluxo de confirmação
no Telegram igual ao de criação/TASK-058, efeito sobre
`mission_schedules`/histórico já coletado) não decidido agora — fica
para a TASK."* Esta auditoria responde "o que existe hoje" antes de
propor o desenho.

## Auditoria (2026-08-10)

### 1. Hoje não existe nenhum caminho de edição

`MissionCommand` (`backend/app/missions/models.py`) só tem
`ACTIVATE`/`PAUSE`/`RESUME`/`COMPLETE`/`CANCEL`/`EXPIRE`. O dicionário
`TRANSITIONS` (`backend/app/missions/service.py`) só mapeia mudanças de
`Mission.status` — nunca escreve em `MissionCriteria`/`MissionSource`.
`create_mission_from_criteria` é o **único** lugar que grava essas duas
tabelas, e sempre cria uma `Mission` nova (UUID novo). Busca em todo
`backend/app` confirma: `MissionCriteria.target_amount`/
`target_currency` e `MissionSource` nunca aparecem do lado esquerdo de
uma atribuição fora dessa função. `docs/MISSION_CRITERIA.md` chama os
critérios de "editáveis", mas isso é uma descrição aspiracional — não
existe código que edite.

### 2. Fluxo de confirmação da TASK-058 é reusável como padrão, não como função genérica

`User.pending_intent` (JSONB) guarda o payload da ação encenada.
`backend/app/telegram/confirmation.py` já tem duas funções
`stage_*`/`describe_*` específicas (`create_mission`, `mission_command|).
`backend/app/telegram/router.py` despacha por um `if/else` fechado em 3
pontos (`_resolve_pending_intent`'s escolha de `Permission`,
`_execute_pending_intent`'s dispatch, e os pares `stage_*`/`describe_*`/
`_execute_*`). Não existe um "stage qualquer mutação, confirme, aplique"
genérico — um novo tipo de ação editável precisa de mais um branch em
cada um desses 3 pontos, seguindo o padrão já usado, não uma extensão
automática.

### 3. `IntentKind`/`IntentParameters` já têm o formato certo, faltando o `kind`

`IntentKind` (`backend/app/intent/contracts.py`) hoje é
`CREATE_MISSION`/`QUERY_MISSION`/`MISSION_COMMAND`/`UNKNOWN` — nenhum
cobre "editar critérios/lojas". `IntentParameters` já tem
`target_amount`/`target_currency`/`sources`/`mission_reference` (reusados
do desenho de criação) — a *forma* dos dados de uma edição já existe no
contrato; falta o `kind`/vocabulário. `IntentInterpreter` classifica
contra uma enumeração fechada (prompt + parser estritos,
`backend/app/intent/interpreter.py`) — qualquer novo `kind`/vocabulário
exige atualizar prompt, enum e parser junto.

### 4. Agenda e histórico já são resilientes a mudança (nada precisa de tratamento especial)

`claim_due_collections` relê `MissionCriteria`/`MissionSource` do banco
a cada ciclo — não há cache. Isso significa: **uma vez editados, os
novos critérios/lojas já valem automaticamente no próximo ciclo de
coleta**, sem nenhum código adicional. `evaluate_price_alerts` também lê
`target_amount`/`target_currency` sempre ao vivo do `MissionCriteria`
passado. `CollectionRun`/`PriceObservation` têm FK `RESTRICT` para
`mission_id`/`store_id`/`offer_id` — nada no código deleta essas linhas
hoje, e a garantia de "nunca apagar histórico" já é estrutural
(constraint de banco), não só convenção. Remover uma loja da missão só
precisa apagar a linha de `MissionSource` correspondente (sem FK
`RESTRICT` bloqueando isso) — o histórico de `CollectionRun`/
`PriceObservation` daquela loja permanece intacto, órfão de
`MissionSource` mas não de `mission_id`/`store_id`.

### 5. Cache de relevância (TASK-063) e campos da pré-lista (TASK-068) não colidem com este escopo

`MissionOfferRelevance` é keyed por `search_query` ficar imutável — o
escopo desta TASK (`DEC-057`) é só lojas e preço-alvo, **não**
`search_query`, então esse cache não é invalidado por esta TASK.
`prelist_sent`/`prelist_lowest_amount` (TASK-068) não leem
`target_amount` em nenhum ponto — só reagem a ofertas `MATCH` já
coletadas. Nenhum reset é estruturalmente necessário nesses campos por
causa de uma edição de preço-alvo.

### 6. Autorização e erros conhecidos

`Permission` (`backend/app/authorization/policy.py`) tem
`MISSION_CREATE`/`MISSION_READ`/`MISSION_TRANSITION` — nenhuma permissão
de edição existe. `_KNOWN_DISPATCH_ERRORS`
(`backend/app/telegram/router.py`) é a lista fechada de erros de domínio
tratados como `204` + mensagem (não `500`) — um novo erro específico de
edição precisa entrar nessa lista para ter o mesmo tratamento seguro.

### 7. Migration

Head atual: `20260810_0001` (TASK-068). Nova migration seria
`20260810_0002` (mesmo dia).

## Decisões aprovadas pelo usuário (2026-08-10)

1. **Agenda**: não é tocada por esta TASK. `MissionSchedule.next_run_at`
   permanece exatamente como estava.
2. **Status editável**: **somente `PAUSED`**. Uma missão `ACTIVE` precisa
   ser pausada (`/pausar`, comando já existente) antes de qualquer
   edição; depois de editar, a missão **continua `PAUSED`** — só volta a
   coletar quando o usuário decidir retomá-la (`RESUME`, já existente).
   **Achado que simplifica bastante o desenho**: `find_due_schedules`
   (`backend/app/missions/schedule.py:31`) já filtra
   `Mission.status == MissionStatus.ACTIVE` — uma missão `PAUSED` **nunca
   é reivindicada** por `claim_due_collections`. Editar uma missão pausada
   é, portanto, estruturalmente seguro: não existe nenhuma coleta em
   andamento com a qual a edição possa colidir, e a decisão "1. Agenda:
   não tocar" já é automaticamente coerente — não há ciclo rodando para
   esperar. `next_run_at` só volta a importar quando o usuário retomar
   (comportamento do `RESUME` já existente, intocado por esta TASK).
3. **Preço-alvo**: a edição pode limpar o alvo (`target_amount`/
   `target_currency` juntos → `NULL`/`NULL`), mesma regra de "par ou
   nenhum" já usada na criação (`ck_mission_criteria_target_pair`).
4. **Instrução adicional (dada durante o desenho)**: se o usuário pedir
   para editar uma missão `ACTIVE`, o bot não rejeita direto — pergunta
   se ele quer pausar agora (mesmo par confirmar/cancelar "1"/"2" já
   usado em toda confirmação). Se confirmar, o bot pausa a missão e
   responde orientando a reenviar o pedido de edição via um comando
   dedicado, `/editar-missao`. Se cancelar, nada muda. Isso separa
   claramente "pausar" de "editar" em dois passos distintos — a
   confirmação de pausa nunca executa a edição em seguida sozinha.

## Desenho proposto

### Escopo estrito (igual ao `DEC-057`)

Editar **lojas selecionadas** (`MissionSource`) e/ou **preço-alvo**
(`MissionCriteria.target_amount`/`target_currency`) de uma missão já
criada. **Não** edita `search_query` (evita qualquer questão sobre o
cache de relevância da TASK-063) nem `title`. Edição parcial: o usuário
pode mudar só lojas, só preço, ou os dois juntos numa mesma confirmação.

### 1. Novo `IntentKind.EDIT_MISSION`, não um `MissionCommand` novo

`MissionCommand` é fechado sobre transições de status (`TRANSITIONS`);
uma edição de critérios não muda `status`, então não se encaixa nesse
vocabulário. Proposta: `IntentKind.EDIT_MISSION`, estrutura paralela a
`CREATE_MISSION`, reusando os campos já existentes de
`IntentParameters` (`mission_reference` obrigatório, `target_amount`/
`target_currency`/`sources` opcionais — pelo menos um dos dois precisa
vir preenchido).

### 2. Serviço: `edit_mission_criteria` (novo, em `missions/service.py`)

Bloqueia a `Mission` (`SELECT ... FOR UPDATE`, mesmo padrão de
`transition_mission`), valida `expected_state_version` (capturado no
momento do *stage*, igual ao `_stage_mission_command` já faz — cobre o
caso de a missão ser retomada por outro caminho entre o *stage* e a
confirmação), confirma que o status é **`PAUSED`** (rejeita `DRAFT`,
`ACTIVE` e os estados terminais `COMPLETED`/`CANCELLED`/`EXPIRED` com
mensagem clara — para `ACTIVE`, orientando pausar primeiro). Atualiza só
os campos fornecidos, sem alterar `status`/`MissionSchedule`:

- **Preço-alvo**: sobrescreve `target_amount`/`target_currency` (ou
  limpa os dois para "sem alvo", espelhando a regra de criação que já
  aceita ausência de alvo).
- **Lojas**: calcula o diff entre o conjunto atual de `MissionSource` e
  o novo pedido — insere as novas, **remove** (`DELETE`, não soft-delete)
  as que saíram. Exige pelo menos 1 loja restante (rejeita edição que
  zeraria as fontes). Histórico (`CollectionRun`/`PriceObservation`) das
  lojas removidas **nunca é tocado** — confirmado seguro pela auditoria
  (item 4).

Não altera `Mission.status`/`state_version`/`MissionTransition`/
`MissionSchedule` — edição de critérios é conceitualmente distinta de
transição de ciclo de vida; `updated_at` do `Mission` é tocado para
refletir a mudança. Como só missões `PAUSED` são editáveis e
`find_due_schedules` já exclui missões não-`ACTIVE`, não há agenda
rodando para coordenar — o usuário retoma quando quiser, com os
critérios já atualizados.

### 3. Confirmação: mesmo padrão da TASK-058, 3 pontos estendidos

- `confirmation.py`: `stage_edit_mission`/`describe_edit_mission` novos,
  mesma forma dos pares existentes — descrição mostra "antes → depois"
  (lojas atuais vs. novas, alvo atual vs. novo). Um segundo par,
  `stage_pause_for_edit`/`describe_pause_for_edit`, reusa o mesmo
  confirmar/cancelar para o passo de pausa (decisão 4 acima) — nenhum
  vocabulário de IA novo, ambos reusam `resolve_answer`.
- `router.py`: novo branch em `_dispatch_intent` (kind `EDIT_MISSION`),
  que decide entre encenar a edição direto (missão já `PAUSED`) ou
  encenar `pause_for_edit` (missão `ACTIVE`); em `_resolve_pending_intent`
  (permissão, via `_PENDING_INTENT_PERMISSIONS`) e em
  `_execute_pending_intent` (dispatch de 4 vias: `create_mission`/
  `edit_mission`/`pause_for_edit`/`mission_command`). Confirmar um
  `pause_for_edit` chama `transition_mission` com `PAUSE` de verdade e
  responde orientando a enviar `/editar-missao` — não executa a edição
  em seguida automaticamente. Novo comando `/editar-missao` (guia estático,
  sem IA) para o usuário reiniciar o pedido depois de pausar.
- Nova `Permission.MISSION_EDIT` (não reaproveita `MISSION_TRANSITION`,
  que é sobre status) — autorização por ownership, mesmo padrão das
  demais. `pause_for_edit` usa `MISSION_TRANSITION` (é uma pausa de
  verdade).
- Novo(s) erro(s) de domínio (ex.: `MissionEditConditionError` para
  "sem loja restante"/"status não editável") adicionados a
  `_KNOWN_DISPATCH_ERRORS`.

### 4. Migration

Nenhuma migration de schema é necessária: `MissionCriteria`/
`MissionSource` já suportam `UPDATE`/`DELETE` no nível do banco (só não
eram usados assim); nenhum campo novo foi decidido. Head do banco
permanece `20260810_0001`.

## Implementação (2026-08-10)

- **`backend/app/intent/contracts.py`**: `IntentKind.EDIT_MISSION`;
  `IntentParameters.clear_target: bool` (default `false`, mutuamente
  exclusivo com `target_amount`/`target_currency` preenchidos);
  `Intent.__post_init__` exige `mission_reference` e pelo menos uma
  mudança (`clear_target`/`target_amount`/`sources`) para `EDIT_MISSION`.
- **`backend/app/intent/interpreter.py`**: `edit_mission` e
  `clear_target` no prompt fechado (com dois novos exemplos), parser
  valida `clear_target` como bool.
- **`backend/app/authorization/policy.py`**: `Permission.MISSION_EDIT`
  nova, atribuída a `USER` (mesma base de `MISSION_CREATE`/
  `MISSION_TRANSITION`).
- **`backend/app/missions/service.py`**: `edit_mission_criteria` (nova)
  e `MissionEditConditionError` (novo). Bloqueia a `Mission` (`FOR
  UPDATE`), valida `expected_state_version`, exige `status is PAUSED`,
  atualiza `MissionCriteria` quando `target_update` é passado, faz o
  diff de `MissionSource` (bulk `delete()` das removidas, `session.add()`
  das novas) quando `source_codes` é passado. Nunca toca
  `status`/`state_version`/`MissionTransition`/`MissionSchedule`.
- **`backend/app/telegram/confirmation.py`**: `stage_edit_mission`/
  `describe_edit_mission` (mostra "antes → depois" de alvo e lojas, e
  lembra que a missão continua pausada) e `stage_pause_for_edit`/
  `describe_pause_for_edit` (novos).
- **`backend/app/telegram/router.py`**: `/editar-missao`
  (`_EDIT_MISSION_COMMAND`) como comando estático; `_stage_edit_mission`
  (decide entre encenar a edição ou pedir pausa primeiro, conforme
  `Mission.status`); `_execute_edit_mission`/`_execute_pause_for_edit`;
  `_PENDING_INTENT_PERMISSIONS` substituindo o `if/elif` fixo de
  permissão por `kind`; `_KNOWN_DISPATCH_ERRORS` ganhou
  `MissionEditConditionError`; `_UNKNOWN_REPLY` menciona o novo comando.
- **Nenhuma migration**: confirmado no desenho — `MissionCriteria`/
  `MissionSource` já suportavam `UPDATE`/`DELETE`. Head do banco
  permanece `20260810_0001`.

## Validação (2026-08-10)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, **815 testes (90,69% cobertura)**, migration head
  `20260810_0001` (sem alteração), **21 testes de integração PostgreSQL
  reais** — todos aprovados (`Pipeline local aprovado.`).
- **Testes novos de contrato** (`tests/test_intent_contracts.py`,
  `tests/test_intent_interpreter.py`): `EDIT_MISSION` exige
  `mission_reference` e pelo menos uma mudança real; `clear_target`
  rejeita não-bool e a combinação com `target_amount`; o prompt documenta
  `edit_mission`/`clear_target`.
- **Testes novos de confirmação** (`tests/test_telegram_confirmation.py`):
  `stage_edit_mission`/`describe_edit_mission` (mudança só de alvo, só de
  lojas, limpeza de alvo) e `stage_pause_for_edit`/`describe_pause_for_edit`.
- **Testes novos de serviço** (`tests/test_mission_edit.py`, unitário
  com `MagicMock`): validação de entrada, missão não encontrada,
  conflito de versão, todo `MissionStatus` que não é `PAUSED` rejeitado,
  atualização só de alvo, limpeza de alvo, diff de lojas
  (adicionar/remover), código de loja desconhecido.
- **Teste de integração real** (`tests/integration/test_mission_edit.py`,
  PostgreSQL): edição de alvo + lojas junto preservando `status`/
  `state_version`; limpeza de alvo; rejeição de missão `ACTIVE` e de
  versão desatualizada; rejeição de zerar todas as lojas;
  **`test_removing_a_store_never_touches_its_price_history`** — cria um
  `CollectionRun` real para uma loja, remove essa loja da missão via
  `edit_mission_criteria`, confirma que o `CollectionRun` permanece
  intacto no banco (só a linha de `MissionSource` some).
- **Testes novos de router** (`tests/test_telegram_router.py`): intenção
  de edição numa missão `PAUSED` encena confirmação sem chamar
  `edit_mission_criteria` antes da hora; intenção numa missão `ACTIVE`
  encena `pause_for_edit` em vez de editar direto; confirmar
  `pause_for_edit` pausa de verdade e orienta enviar `/editar-missao`;
  cancelar `pause_for_edit` não pausa nada; confirmar `edit_mission`
  chama `edit_mission_criteria` e limpa `pending_intent`; missão em
  status terminal responde com `MissionEditConditionError` sem encenar
  nada; `/editar-missao` responde com o guia estático sem chamar IA.
- **Sem chamada de IA nova**: confirmado por auditoria de código —
  `EDIT_MISSION` reusa `IntentInterpreter` (vocabulário fechado
  estendido) e `pause_for_edit`/`edit_mission` reusam
  `interpret_confirmation_reply` (`resolve_answer`), o mesmo par
  confirmar/cancelar já usado por `create_mission`/`mission_command`.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1`; nenhuma tag `v1.0.2` criada.

## Escopo confirmado

1. `IntentKind.EDIT_MISSION` + `IntentParameters.clear_target`.
2. `Permission.MISSION_EDIT` nova.
3. `edit_mission_criteria` (serviço) + `MissionEditConditionError`.
4. Confirmação de edição (`stage_edit_mission`/`describe_edit_mission`)
   e de pausa-antes-de-editar (`stage_pause_for_edit`/
   `describe_pause_for_edit`), ambas via `/editar-missao` e o par
   confirmar/cancelar já existente.
5. Só missões `PAUSED` são editáveis; `ACTIVE` oferece pausar primeiro;
   `DRAFT`/terminais são rejeitados direto.
6. Missão permanece `PAUSED` após a edição; agenda (`MissionSchedule`)
   intocada; histórico (`CollectionRun`/`PriceObservation`) das lojas
   removidas nunca é apagado.
7. Nenhuma migration; nenhuma IA nova; produção intocada; nenhuma tag
   `v1.0.2`.

## Fora do escopo desta TASK

- Editar `search_query`/`title` (evita qualquer risco sobre o cache de
  relevância da TASK-063).
- Qualquer IA nova além da classificação de intenção já existente
  (`IntentInterpreter`) e da confirmação já existente
  (`interpret_confirmation_reply`) — ambas reusadas, não expandidas em
  capacidade.
- Reset de `prelist_sent`/`prelist_lowest_amount` (auditoria mostrou que
  não é estruturalmente necessário — a confirmar se a decisão final
  mudar isso).
- Produção; nenhuma tag `v1.0.2`.

## Encerramento

Concluída em 2026-08-10. Uma missão já criada pode ter suas lojas e/ou
preço-alvo editados, mas só enquanto `PAUSED`; pedir a edição de uma
missão `ACTIVE` oferece pausar primeiro (confirmação "1"/"2") e depois
orienta reenviar via `/editar-missao` — pausar e editar nunca acontecem
como um único passo automático. A edição nunca toca `status`,
`state_version`, `MissionTransition` ou `MissionSchedule`; o histórico de
coleta de uma loja removida nunca é apagado (confirmado por teste de
integração real). Nenhuma IA nova envolvida — reusa o classificador de
intenção (vocabulário fechado estendido) e o classificador de
confirmação já existentes. Com esta TASK, os 5 itens do **planejamento
original** de `docs/V1_0_2.md` estão implementados e validados; produção
da `v1.0.1` intocada; nenhuma tag `v1.0.2` criada.

**Nota pós-conclusão (2026-08-10):** ao aprovar a publicação desta TASK,
o usuário ampliou o escopo da `v1.0.2` com mais dois itens (`DEC-060`,
registrados em `docs/V1_0_2.md` como 6 e 7 — bloquear `/cadastro` para
usuário já autenticado e perguntar as lojas por lista numerada quando
uma missão for criada sem nenhuma informada), sem implementação e sem
TASK aberta. **A `v1.0.2` continua aberta** — esta nota não altera nada
do que a TASK-069 implementou ou validou.
