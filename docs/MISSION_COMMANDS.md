# Comandos de missão via Telegram

A TASK-035 fecha o loop iniciado pelas TASKs 032 a 034 e 056: o webhook
(`POST /telegram/webhook`) resolve a identidade do usuário
(`get_or_create_telegram_user`, TASK-056) e executa a ação de missão
correspondente ao `Intent` já traduzido, respondendo ao Telegram. Sem teclado
interativo — a seleção de fontes vem do que o `IntentInterpreter` (TASK-032)
já extraiu do texto livre — e sem as notificações proativas orientadas a
evento, implementadas separadamente na TASK-036.

Desde a TASK-058, `create_mission` e `mission_command` não executam mais
direto: ficam **encenados** e só executam após confirmação explícita do
usuário — ver "Confirmação antes de executar" abaixo. Editar lojas e/ou
preço-alvo de uma missão (TASK-069) segue o mesmo padrão de confirmação
final, mas desde a TASK-071 só é alcançável pelo menu guiado do
`/editar-missao` — nunca mais por texto livre interpretado pela IA (ver
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
  do `/editar-missao` (TASK-071, `backend/app/telegram/router.py`):
  1. Resolve qual missão sem IA: exatamente 1 `PAUSED` seleciona
     automaticamente; mais de 1 `PAUSED` lista os títulos numerados para
     escolher; sem nenhuma `PAUSED`, reaproveita o pedido de pausa já
     existente (`pause_for_edit`, TASK-069) para a(s) missão(ões)
     `ACTIVE` — sem nenhuma pausada nem ativa, avisa que não há nada para
     editar. Nunca edita uma `ACTIVE` diretamente.
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
     `stage_edit_mission`/`describe_edit_mission` (TASK-069, sem
     alteração) — a confirmação final sim/não continua usando
     `resolve_answer` (a mesma classificação de IA usada por toda
     confirmação do sistema; não é o `IntentInterpreter`). Missões
     `DRAFT` ou em status terminal nunca aparecem como candidatas.
- **`unknown`**: resposta fixa pedindo para o usuário reformular, deixando
  explícito que o bot não conversa sobre outros assuntos.

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

A classificação da resposta (`confirmar`/`cancelar`/`não entendi`) passa
pelo `AIProviderManager` do próprio perfil do usuário, com um propósito e
um prompt dedicados (`interpret_confirmation_reply`) — não pela palavra
exata nem pelo vocabulário fechado do `IntentInterpreter` — para reconhecer
respostas informais, gírias e erros de português. Confirmado, a ação
gravada é executada; cancelado, é descartada; não reconhecido, a
confirmação continua pendente e o usuário é convidado a responder de novo.

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
documentados em `docs/MARKETPLACE_SOURCES.md`.

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
