# TASK-071 — Fluxo guiado e determinístico para `/editar-missao` (sem IA)

Status: **Concluída em 2026-08-11**, desenho aprovado explicitamente pelo
usuário (incluindo o ajuste obrigatório de preservar a remoção de lojas
já existente na TASK-069), implementada e validada com pipeline
oficial.

Dependência: TASK-069 (`docs/tasks/TASK-069.md`, edição de missão) e
TASK-070 (`docs/tasks/TASK-070.md`, lista numerada na criação). Não é
item da `v1.0.2` — pedido explícito do usuário, feito depois da TASK-070,
para corrigir um risco de comportamento identificado no fluxo de edição
já publicado.

## Contexto

Depois da TASK-070 publicada, o usuário pediu uma simulação de como
funcionava editar lojas de uma missão hoje (TASK-069). A simulação
revelou um problema real: `IntentParameters.sources` é sempre tratado
como **a lista completa final** de lojas (não uma diferença/adição), mas
o `IntentInterpreter` (IA) só vê o texto da mensagem — nunca sabe quais
lojas a missão já tem. Se o usuário disser "adiciona kabum e terabyte"
sem repetir a loja que já estava selecionada, a IA extrai só
`["kabum", "terabyte"]`, e confirmar essa edição **remove** a loja
anterior sem o usuário perceber facilmente (a tela de confirmação mostra
"antes → depois", mas o risco de erro por texto livre continua real).

## Decisão

Substituir a entrada por texto livre no fluxo de edição por um **menu
guiado e 100% determinístico**, sem nenhuma chamada ao `IntentInterpreter`
a partir do comando `/editar-missao`. O par confirmar/cancelar (sim/não)
final continua existindo e continua usando o classificador de IA já
existente (`interpret_confirmation_reply`, TASK-058) — esse não é
`IntentInterpreter` e não foi alvo da preocupação do usuário; é o mesmo
padrão já usado por toda confirmação do sistema, incluindo o próprio
`await_create_mission_sources` da TASK-070.

**O caminho antigo (editar por texto livre, fora do `/editar-missao`) é
desativado** — decisão explícita do usuário: manter os dois em paralelo
preservaria o mesmo risco identificado. Uma mensagem que a IA ainda
classifique como `edit_mission` passa a responder só orientando a usar
`/editar-missao`, sem extrair nem aplicar nada.

## Desenho aprovado (2026-08-11)

1. `/editar-missao`.
2. Resolução da missão, sem IA:
   - exatamente 1 `PAUSED`: seleciona automaticamente;
   - mais de 1 `PAUSED`: lista numerada dos títulos para escolher;
   - nenhuma `PAUSED`, mas existe exatamente 1 `ACTIVE`: mantém o fluxo
     já existente de oferecer pausa (`pause_for_edit`, TASK-069, sem
     mudança);
   - nenhuma `PAUSED`, mais de 1 `ACTIVE`: lista numerada das ativas para
     escolher qual pausar;
   - nenhuma `PAUSED` nem `ACTIVE`: informa que não há nada para editar.
   - Nunca edita uma missão `ACTIVE` diretamente; depois de pausada, a
     missão permanece `PAUSED` (regra já definida na TASK-069, intocada).
3. Menu principal (sem IA):
   ```
   ✏️ O que deseja editar na missão "<título>"?

   1 - Lojas
   2 - Preço-alvo
   ```
4. Se "1 - Lojas":
   ```
   🏪 O que deseja fazer?

   1 - Adicionar lojas
   2 - Remover lojas
   ```
   - **Adicionar**: mostra só as lojas ainda não vinculadas (numeração
     dinâmica, só a partir das que faltam); aceita múltiplas separadas por
     vírgula; mostra "antes → depois"; pede confirmação sim/não antes de
     persistir.
   - **Remover**: mostra só as lojas atualmente vinculadas; aceita
     múltiplas; **nunca permite que a missão termine sem nenhuma loja**
     (validado antes de chegar à confirmação, e novamente pelo serviço);
     preserva integralmente `CollectionRun`/`PriceObservation` e demais
     históricos (mesma garantia já existente em `edit_mission_criteria`);
     mostra "antes → depois"; pede confirmação sim/não.
5. Se "2 - Preço-alvo": pede o valor diretamente (ex.: "300" ou "300.50"),
   parser numérico determinístico (aceita vírgula ou ponto como
   separador decimal, moeda sempre BRL — única moeda usada na V1); `0`
   remove o alvo (`clear_target`); mostra "antes → depois"; pede
   confirmação sim/não.

Toda entrada inválida em qualquer passo (menu fora do intervalo, loja não
reconhecida, valor não numérico ou negativo) mantém o mesmo estado
pendente e repete o pedido — nunca avança nem executa nada. Reenviar
`/editar-missao` a qualquer momento reinicia o fluxo do zero (mesmo
padrão de outros comandos estáticos, que já têm prioridade sobre
`pending_intent`).

## Reaproveitamento (sem duplicar lógica já existente)

- `edit_mission_criteria` (`backend/app/missions/service.py`): **sem
  nenhuma alteração**. Continua exigindo `PAUSED`, validando
  `expected_state_version`, calculando o diff de `MissionSource` e
  rejeitando zerar todas as lojas — dupla proteção junto com a validação
  do próprio fluxo guiado.
- `stage_edit_mission`/`describe_edit_mission`
  (`backend/app/telegram/confirmation.py`, TASK-069): **sem alteração**.
  Os três sub-fluxos (adicionar, remover, preço-alvo) convergem para o
  mesmo payload `"kind": "edit_mission"` já existente — a tela final
  "antes → depois" e a execução (`_execute_edit_mission`) são
  exatamente as mesmas da TASK-069.
- `stage_pause_for_edit`/`describe_pause_for_edit`/`_execute_pause_for_edit`
  (TASK-069): **sem alteração**. Reaproveitado quando a resolução da
  missão cai no caso "só existe(m) `ACTIVE`".
- `parse_numbered_store_selection` (`backend/app/telegram/confirmation.py`,
  TASK-070): reaproveitado para interpretar a seleção de lojas em
  adicionar/remover — já era genérico/configurável por decisão
  explícita da TASK-070, agora esse reuso se confirma na prática.
- `_CREATE_MISSION_SOURCE_OPTIONS` (ordem `1 Pichau/2 Terabyte/3 Amazon/
  4 Kabum`, TASK-070): reaproveitada como a ordem canônica para montar as
  listas dinâmicas de "faltam"/"já vinculadas".

## Fora do escopo desta TASK

- Alterar `MissionSchedule`, providers, coleta, ranking, alertas, TASK-068
  ou o comportamento de criação de missão (TASK-070) — nenhum tocado.
- Suportar moeda diferente de BRL na edição de preço-alvo.
- Qualquer forma de cancelar no meio da navegação além de reenviar
  `/editar-missao`.
- Remover `IntentKind.EDIT_MISSION` do vocabulário do `IntentInterpreter`
  — a classificação continua existindo (não afeta nada), só deixou de
  ser executada.

## Implementação (2026-08-11)

- **`backend/app/telegram/confirmation.py`**: `parse_single_numbered_choice`
  (escolha única numérica, para o menu principal, o submenu de lojas e a
  lista de missões); `describe_mission_choice_prompt`/
  `describe_mission_choice_retry`; `describe_no_editable_mission`;
  `describe_edit_menu`/`describe_edit_menu_retry`;
  `describe_edit_lojas_menu`/`describe_edit_lojas_menu_retry`;
  `missing_store_options`/`current_store_options` (numeração dinâmica na
  ordem canônica da TASK-070); `describe_edit_add_sources_prompt`/
  `describe_edit_remove_sources_prompt`/`describe_edit_source_selection_retry`;
  `describe_edit_add_sources_none_missing`/
  `describe_edit_remove_sources_too_few`/
  `describe_edit_remove_sources_would_empty`; `resolve_edit_source_selection`
  (reaproveita `parse_numbered_store_selection`, sem atalho de "todas");
  `describe_edit_target_amount_prompt`/`describe_edit_target_amount_retry`;
  `parse_target_amount_entry` (aceita vírgula ou ponto, rejeita negativo).
- **`backend/app/telegram/router.py`**: `/editar-missao` passou a chamar
  `_start_edit_mission_flow` (antes: resposta estática). Novas funções de
  orquestração: `_query_missions_by_status`/`_query_mission_source_codes`
  (únicos pontos que tocam o banco além do passo final);
  `_stage_edit_mission_choice`/`_apply_edit_mission_choice` (lista de
  missões); `_stage_pause_offer_for_edit` (reusa `stage_pause_for_edit`);
  `_stage_edit_menu`/`_apply_edit_menu_choice` (menu principal, único
  outro ponto com acesso a banco, para carregar `current_sources` ou o
  alvo atual conforme o ramo escolhido); `_apply_edit_lojas_choice`;
  `_start_add_sources`/`_start_remove_sources`; `_apply_edit_add_sources`/
  `_apply_edit_remove_sources`; `_apply_edit_target_amount`. Todas essas
  últimas convergem para `stage_edit_mission`/`describe_edit_mission`
  (TASK-069, inalteradas). `_dispatch_intent` não chama mais
  `_stage_edit_mission` para `IntentKind.EDIT_MISSION` — responde com
  `_EDIT_MISSION_FREE_TEXT_REDIRECT`. A função antiga `_stage_edit_mission`
  foi removida (código morto depois da mudança).
- **Nenhuma alteração** em `backend/app/missions/service.py`,
  `backend/app/intent/contracts.py`, `backend/app/intent/interpreter.py`
  (o vocabulário `edit_mission` continua existindo, só não é mais
  executado), `backend/app/users/registration.py` (`/cadastro`
  intocado), providers, coleta, ranking, alertas, TASK-068 ou TASK-070.
- **Nenhuma migration**: nenhum campo novo de banco.

## Validação (2026-08-11)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, **876 testes (91,00% cobertura)**, migration head
  `20260810_0001` (sem alteração), **21 testes de integração PostgreSQL
  reais** — todos aprovados (`Pipeline local aprovado.`).
- **Testes novos de confirmação** (`tests/test_telegram_confirmation.py`):
  `parse_single_numbered_choice` (válido, fora do intervalo, não numérico,
  múltiplo); listas de missões e menus renderizados corretamente;
  `missing_store_options`/`current_store_options` na ordem canônica,
  excluindo/incluindo corretamente; `resolve_edit_source_selection`
  validando por completo, sem atalho de "todas"; `parse_target_amount_entry`
  aceitando vírgula/ponto, `0`, rejeitando negativo e texto não numérico.
- **Testes novos de fluxo** (`tests/test_telegram_router.py`), cobrindo a
  resolução de missão (1 `PAUSED` auto-seleciona; mais de 1 lista para
  escolher; sem `PAUSED` mas 1 `ACTIVE` oferece pausa; sem `PAUSED` e
  mais de 1 `ACTIVE` lista para escolher; nenhuma editável avisa),
  navegação do menu principal e do submenu de lojas (com retorno de
  `current_sources`/alvo atual carregados do banco), adicionar lojas
  (mostra só as que faltam, bloqueia quando já tem todas), remover lojas
  (mostra só as vinculadas, bloqueia quando só há uma, rejeita zerar
  todas mantendo o mesmo estado pendente), edição de preço-alvo (número
  direto, vírgula decimal, `0` limpa o alvo), e o **caminho antigo por
  texto livre respondendo só com o redirecionamento**, nunca chamando
  `resolve_mission_for_command` nem encenando nada.
- **`edit_mission_criteria`/`stage_edit_mission`/`stage_pause_for_edit`
  intocados**: confirmado por diff — nenhuma linha alterada nesses
  símbolos; os testes de integração já existentes da TASK-069
  (`tests/integration/test_mission_edit.py`) continuam passando sem
  modificação.
- **Sem chamada de IA nova, e uma removida**: toda a navegação a partir
  de `/editar-missao` é determinística; a única IA envolvida no fluxo
  inteiro é a confirmação final sim/não já existente
  (`interpret_confirmation_reply`), reaproveitada sem alteração. O
  caminho antigo (`IntentKind.EDIT_MISSION` executando a partir de texto
  livre) foi desativado.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1`; nenhuma tag `v1.0.2` criada.

## Escopo confirmado

1. `/editar-missao` abre um menu guiado, 100% determinístico, sem
   `IntentInterpreter`.
2. Resolução de missão sem IA (`PAUSED` única, lista para múltiplas,
   reaproveita `pause_for_edit` para `ACTIVE`, avisa quando não há
   nada).
3. Menu `1 Lojas`/`2 Preço-alvo`; lojas ganha `1 Adicionar`/`2 Remover`.
4. Adicionar mostra só as lojas que faltam; remover mostra só as
   vinculadas e nunca permite zerar todas.
5. Preço-alvo aceita valor direto (vírgula ou ponto), `0` remove o alvo.
6. Toda entrada inválida mantém o mesmo estado pendente e repete o
   pedido.
7. Caminho antigo por texto livre desativado — só orienta a usar
   `/editar-missao`.
8. `edit_mission_criteria`, `stage_edit_mission`/`describe_edit_mission`,
   `stage_pause_for_edit`/`describe_pause_for_edit` e
   `parse_numbered_store_selection` reaproveitados sem alteração;
   ownership, regra de só `PAUSED` editável, preservação de histórico e
   `MissionSchedule` intocados.
9. Nenhuma migration; produção intocada; nenhuma tag `v1.0.2`; nenhuma
   outra TASK iniciada.

## Encerramento

Concluída em 2026-08-11. `/editar-missao` deixou de ser um comando
estático seguido de texto livre e virou um menu guiado, 100%
determinístico — nenhum passo de navegação (qual missão, o que editar,
quais lojas) chama o `IntentInterpreter`; só a confirmação final sim/não
continua usando o classificador de IA já existente, igual a todo o resto
do sistema. O caminho antigo (editar por texto livre) foi desativado por
decisão explícita do usuário, depois de uma simulação revelar que ele
podia remover uma loja da missão sem o usuário perceber — `sources`
sempre foi a lista completa final, mas a IA nunca sabia o que a missão
já tinha. Toda a lógica de domínio (validação de `PAUSED`, diff de
lojas, proteção contra zerar todas, preservação de histórico) permanece
exatamente a mesma da TASK-069, reaproveitada sem alteração. Produção da
`v1.0.1` intocada; nenhuma tag `v1.0.2` criada; nenhuma outra TASK
iniciada.
