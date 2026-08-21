# Comandos de missão via Telegram

O webhook (`POST /telegram/webhook`) resolve a identidade do usuário e usa IA
somente quando existe uma tarefa semântica explícita. Mensagens soltas não são
mais tratadas como intenção universal: a criação começa por `/criar_missao`
(`/criar-missao` também é aceito quando digitado) e apenas a mensagem seguinte
é entregue ao `IntentInterpreter`. O estado `await_create_mission_description`
é persistido em `User.pending_intent`, pertence ao usuário e expira em 10
minutos.

Desde a TASK-058, `create_mission` e `mission_command` não executam mais
direto: ficam **encenados** e só executam após confirmação explícita do
usuário — ver "Confirmação antes de executar" abaixo. Editar lojas e/ou
preço-alvo de uma missão (TASK-069) segue o mesmo padrão de confirmação
final, mas desde a TASK-071 só é alcançável pelo menu guiado do
`/editar_missao` (alias digitável `/editar-missao`) — nunca mais por texto livre interpretado pela IA (ver
o item `edit_mission` abaixo).

## Despacho por `IntentKind`

- **`create_mission`**: fontes efetivas são as informadas em
  `IntentParameters.sources` quando presentes. Quando o `Intent` não traz
  nenhuma (TASK-070), o webhook **não** assume mais as quatro fontes da
  V1 automaticamente — encena um estado pendente à parte
  (`await_create_mission_sources`, preservando `search_query`/
  `target_amount`/`target_currency` já interpretados) e pergunta por
  lista numerada (`1 Pichau, 2 Terabyte, 3 Amazon, 4 Kabum, 5 Todas`,
  ordem própria deste fluxo — diferente da usada pelo `/cadastro`,
  TASK-067). A resposta é interpretada de forma determinística, sem IA
  (`parse_numbered_store_selection`, `backend/app/telegram/confirmation.py`):
  qualquer token não reconhecido invalida a resposta inteira (nunca
  aceita parcialmente); só depois de uma seleção válida a missão fica
  encenada como `create_mission`, seguindo para a confirmação normal
  descrita abaixo. `create_mission_from_criteria`
  (`backend/app/missions/service.py`) ainda mantém um fallback interno
  para as quatro fontes quando chamado com `source_codes` vazio — usado
  por outros chamadores (`backend/scripts/validate_collection_worker.py`)
  — mas o webhook nunca mais o exercita, já que sempre resolve as fontes
  antes de chegar lá. Uma `CREATE_MISSION` confirmada sempre ativa
  imediatamente — não existe caminho para ficar em `draft` por falta de
  fonte.
- **Entrada pública de criação**: `/criar_missao` entra no estado
  `await_create_mission_description`. Só a próxima descrição válida desse
  mesmo usuário chama IA; o estado é consumido antes da chamada e não fica
  preso em caso de falha do provider. Um resultado que não seja
  `CREATE_MISSION` é recusado, sem executar outra intenção. Mensagem livre em
  `IDLE` recebe orientação fixa para usar `/criar_missao`, sem IA.
- **`query_mission`**: somente leitura, continua respondendo direto, sem
  confirmação. Com `mission_reference`, usa `find_missions_by_reference`
  (busca case-insensitive por substring em `MissionCriteria.search_query`);
  sem referência, usa `list_missions_for_user` (mais recentes primeiro). A
  resposta lista o que for encontrado, incluindo o caso de nenhuma missão.
- **`mission_command`**: `resolve_mission_for_command`
  (`backend/app/missions/query.py`) exige exatamente uma missão alvo no
  momento em que a intenção é interpretada (antes de encenar). Com
  `mission_reference`, a correspondência deve ser única. Sem referência,
  exige exatamente uma missão não terminal do usuário — a V1 nunca expõe um
  identificador de missão, então mais de uma candidata é ambiguidade real,
  não um detalhe de implementação. Ao ser confirmado, `transition_mission`
  (TASK-021) executa o comando usando a versão de estado capturada no
  momento em que a confirmação foi encenada.
- **Cancelamento, pausa e retomada públicos (TASK-090)**: `/cancelar_missao`
  (alias digitável `/cancelar-missao`), `/pausar` e `/retomar` compartilham o
  mesmo fluxo local determinístico (`_start_manual_command_flow`,
  `backend/app/telegram/router.py`), variando só a consulta de candidatas —
  `/cancelar_missao` usa `list_mission_command_candidates` (todas as missões
  não terminais do usuário); `/pausar` usa `_query_missions_by_status` restrita
  a `ACTIVE`; `/retomar` usa a mesma consulta restrita a `PAUSED`. Zero
  candidatas gera resposta fixa (sem IA); uma candidata segue para
  confirmação única (`stage_mission_command`); várias geram lista numerada e
  aceitam seleção múltipla (`stage_mission_command_choice` +
  `parse_multi_numbered_choice`, TASK-085) — `1`, `1,3` e `2, 4, 5` são
  aceitos, espaços são tolerados, duplicatas são ignoradas e qualquer token
  inválido invalida a resposta inteira (nunca execução parcial). Comando,
  seleção, confirmação e execução não usam IA em nenhum dos três; a seleção
  numérica só resolve missões da própria listagem do usuário (isolamento por
  ownership). A transição usa `command=cancel|pause|resume`, versão
  otimista e ownership; somente `cancel` também desativa
  `MissionSchedule.is_enabled` na mesma transação — pausar e retomar não
  precisam disso porque a elegibilidade de coleta já filtra diretamente por
  `Mission.status == ACTIVE` (`backend/app/collection/orchestration.py`,
  `backend/app/missions/schedule.py`).
- **`edit_mission`**: **desativado como intenção livre desde a TASK-071**
  — o `IntentInterpreter` ainda classifica mensagens como `edit_mission`,
  mas o webhook não executa mais nada a partir disso; responde só
  orientando a usar `/editar-missao`. O motivo: `IntentParameters.sources`
  sempre foi tratado como a lista completa final de lojas (não uma
  diferença), mas a IA nunca sabe quais lojas a missão já tem — pedir
  "adiciona kabum e terabyte" sem repetir a loja já selecionada fazia a
  confirmação **remover** essa loja sem o usuário perceber facilmente.
  Editar lojas e/ou preço-alvo (nunca `search_query`/`title`) de uma
  missão já criada agora só acontece pelo **menu guiado e determinístico**
  do `/editar_missao` (TASK-071, `backend/app/telegram/router.py`):
  1. Resolve qual missão sem IA: exatamente 1 `PAUSED` seleciona
     automaticamente; mais de 1 `PAUSED` lista os títulos numerados para
     escolher; sem nenhuma `PAUSED`, reaproveita o pedido de pausa já
     existente (`pause_for_edit`, TASK-069) para a(s) missão(ões)
     `ACTIVE` — sem nenhuma pausada nem ativa, avisa que não há nada para
     editar. Nunca edita uma `ACTIVE` diretamente. Desde a TASK-090, ao
     confirmar a pausa de uma missão `ACTIVE`, o fluxo não termina mais numa
     mensagem pedindo para reenviar `/editar_missao`: encena diretamente o
     próximo estado (`await_edit_menu_choice`) e o menu principal já aparece
     na mesma resposta, carregando `auto_paused=True` até o fim da edição.
     Quando a missão já estava `PAUSED` antes do comando, `auto_paused` fica
     `False`. Esse booleano não altera nenhuma regra de transição — só o
     texto final do passo 5, para diferenciar "pausei agora para editar" de
     "já estava pausada". Em nenhum dos dois casos `/editar_missao` retoma a
     missão sozinho.
  2. Menu principal (`1 - Lojas`, `2 - Preço-alvo`), sem IA.
  3. **Lojas**: submenu `1 - Adicionar` / `2 - Remover`. Adicionar mostra
     só as lojas ainda não vinculadas; remover mostra só as vinculadas e
     nunca permite zerar todas (validado no fluxo e de novo pelo
     serviço). Seleção numerada estrita — reaproveita
     `parse_numbered_store_selection` (TASK-070): qualquer token não
     reconhecido invalida a resposta inteira.
  4. **Preço-alvo**: valor digitado diretamente (vírgula ou ponto como
     separador decimal, moeda sempre BRL), parser determinístico; `0`
     remove o alvo.
  5. Todos os caminhos convergem para o mesmo payload
     `stage_edit_mission`/`describe_edit_mission` (TASK-069, com o campo
     `auto_paused` acrescentado na TASK-090) — a confirmação final sim/não
     continua usando `resolve_answer` (classificação local de vocabulário
     fechado). A mensagem de conclusão sempre orienta usar `/retomar`.
     Missões `DRAFT` ou em status terminal nunca aparecem como candidatas.
- **`unknown`/mensagem solta**: resposta fixa orientando a usar
  `/criar_missao` ou `/ajuda`; nunca chama IA.

## Confirmação antes de executar (TASK-058, estendida nas TASK-069/071)

Depois que `create_mission`/`mission_command` são interpretados e
validados, ou que o menu guiado de `/editar-missao` (TASK-071) chega a um
resumo de edição, o webhook não executa a ação — grava os dados mínimos
necessários em `User.pending_intent` (JSONB) e responde descrevendo a
ação em português, pedindo confirmação
(`backend/app/telegram/confirmation.py`). A mensagem seguinte do mesmo
usuário é tratada como resposta a essa confirmação, **antes** de qualquer
outro processamento (comandos `/cadastro`/`/upgrade`, cadastro em
andamento e interpretação por IA só entram em jogo se não houver
confirmação pendente) — isso vale tanto para o par confirmar/cancelar
final quanto para os passos intermediários do menu guiado, que também são
resolvidos antes da IA.

A classificação é totalmente local: `sim`, `s` e `1` confirmam; `não`,
`nao`, `n` e `2` recusam. Qualquer outra resposta mantém a confirmação
pendente e pede novamente o vocabulário válido. O fluxo não chama
`AIProviderManager`, portanto indisponibilidade de Gemini/Groq/OpenRouter não
impede uma confirmação nem um cancelamento.

## Nomes formais na Bot API

A Bot API aceita apenas letras minúsculas, dígitos e underscore no campo
`BotCommand.command`. Por isso o menu registra `/criar_missao`,
`/cancelar_missao`, `/pausar`, `/retomar`, `/listar_missoes` e
`/editar_missao`. Os equivalentes com hífen (quando existem) são aceitos
pelo roteador quando digitados como texto, mas não são enviados a
`setMyCommands`.

`/listar_missoes`, `/listar-missoes`, `missoes` e `missões` compartilham uma
consulta determinística, sem IA, numerada e restrita ao proprietário. São
exibidos somente os estados ativa, pausada e cancelada, nessa ordem, com os
status visuais `🟢 ativa`, `⏸️ pausada` e `❌ cancelada`.

## Limite entre `204` e `500`

Depois que o transporte e a identidade mínima do canal são autenticados
(TASK-046), a rota distingue dois tipos de falha:

- **Erros esperados e conhecidos de domínio/validação** —
  `MissionNotFoundError`, `MissionVersionConflictError`,
  `InvalidMissionTransitionError`, `MissionTransitionConditionError`,
  `MissionEditConditionError` (TASK-069, status não editável ou nenhuma
  fonte restante) (`backend/app/missions/service.py`),
  `MissionReferenceError` (`backend/app/missions/query.py`) e
  `MissionIntentError` (um `Intent` de criação sem `search_query`) — são
  registrados, respondidos ao usuário com uma mensagem explicativa, e a
  rota ainda devolve `204`.
- **Qualquer outra falha** — incluindo `IntegrityError` residual não tratada
  no serviço apropriado (a única corrida esperada com `IntegrityError` é a
  de `get_or_create_telegram_user`, contida internamente por `SAVEPOINT`,
  TASK-056) — **não é capturada pela rota** e sobe como `500`, sem mascarar
  o bug.

Essa distinção estende o princípio já registrado pela TASK-034 (`DEC-010`):
a entrega de uma atualização pelo Telegram e o processamento subsequente são
falhas independentes, mas só falhas *conhecidas* de domínio viram resposta
controlada — falhas inesperadas continuam visíveis como `500` (`DEC-013`).

## Seed das lojas da V1

`MissionSource.store_id` referencia `Store`, e nenhuma linha existia até a
TASK-035 para os quatro códigos selecionáveis. A migração `20260807_0002`
semeia essas quatro lojas (código, nome, `base_url`, tipo) com os valores já
documentados em `docs/architecture/providers.md`.

## Sessão de banco por requisição

`backend/app/database/dependency.py` define `get_session`, a primeira
dependência FastAPI de sessão por requisição do projeto: comita quando a
rota inteira termina sem exceção, desfaz em qualquer exceção e sempre fecha
a sessão. Os serviços de missão continuam usando `session.flush()`, nunca
`commit()` — a transação pertence ao chamador, agora a própria rota.

## Limites

Este documento não cobre login por usuário/senha (TASK-061), comandos
apresentados por teclado interativo (evolução futura), notificações
proativas (implementadas separadamente na TASK-036) nem preferências de
notificações (implementadas separadamente na TASK-037).
