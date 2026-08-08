# Comandos de missão via Telegram

A TASK-035 fecha o loop iniciado pelas TASKs 032 a 034 e 056: o webhook
(`POST /telegram/webhook`) resolve a identidade do usuário
(`get_or_create_telegram_user`, TASK-056) e executa a ação de missão
correspondente ao `Intent` já traduzido, respondendo ao Telegram. Sem teclado
interativo — a seleção de fontes vem do que o `IntentInterpreter` (TASK-032)
já extraiu do texto livre — e sem as notificações proativas orientadas a
evento, que continuam reservadas à TASK-036.

## Despacho por `IntentKind`

- **`create_mission`**: `create_mission_from_criteria`
  (`backend/app/missions/service.py`) cria `Mission` + `MissionCriteria` e
  ativa imediatamente. Fontes efetivas: as informadas em
  `IntentParameters.sources` quando presentes; quando o `Intent` não traz
  nenhuma, usa automaticamente as quatro fontes da V1 (Pichau, Terabyte,
  Amazon, Kabum). Uma `CREATE_MISSION` válida é **sempre** criada com pelo
  menos uma fonte e sai `active` — não existe caminho para ficar em `draft`
  por falta de fonte.
- **`query_mission`**: com `mission_reference`, usa
  `find_missions_by_reference` (busca case-insensitive por substring em
  `MissionCriteria.search_query`); sem referência, usa
  `list_missions_for_user` (mais recentes primeiro). A resposta lista o que
  for encontrado, incluindo o caso de nenhuma missão.
- **`mission_command`**: `resolve_mission_for_command`
  (`backend/app/missions/query.py`) exige exatamente uma missão alvo. Com
  `mission_reference`, a correspondência deve ser única. Sem referência,
  exige exatamente uma missão não terminal do usuário — a V1 nunca expõe um
  identificador de missão, então mais de uma candidata é ambiguidade real,
  não um detalhe de implementação. Resolvida a missão, `transition_mission`
  (TASK-021) executa o comando normalmente.
- **`unknown`**: resposta fixa pedindo para o usuário reformular.

## Limite entre `204` e `500`

Depois que uma atualização é autenticada, a rota distingue dois tipos de
falha:

- **Erros esperados e conhecidos de domínio/validação** —
  `MissionNotFoundError`, `MissionVersionConflictError`,
  `InvalidMissionTransitionError`, `MissionTransitionConditionError`
  (`backend/app/missions/service.py`), `MissionReferenceError`
  (`backend/app/missions/query.py`) e `MissionIntentError` (um `Intent` de
  criação sem `search_query`) — são registrados, respondidos ao usuário com
  uma mensagem explicativa, e a rota ainda devolve `204`.
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

Este documento não cobre autenticação real de usuário (TASK-046), comandos
apresentados por teclado interativo (evolução futura), notificações
proativas (TASK-036) nem preferências de usuário (TASK-037).
