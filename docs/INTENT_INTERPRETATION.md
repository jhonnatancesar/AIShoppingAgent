# Interpretação de intenção

A TASK-032 traduz uma mensagem livre do usuário em uma intenção estruturada,
agnóstica de canal e sem lógica de domínio. `IntentInterpreter` é o único ponto
de entrada e usa `AIProviderManager` no perfil `USER` por padrão, conforme
`docs/AI_PROVIDER_MANAGER.md`.

Desde a TASK-059, `interpret` aceita um parâmetro nomeado opcional `profile`
(default `USER`). O caminho de produção — o webhook do Telegram (TASKs
033–035) — nunca o passa e continua sempre em `USER`. Só ferramentas de
validação manual (`backend/scripts/validate_intent_interpreter.py`) podem
passar `ADMIN`/`DEV`, para rodar contra a cascata de fallback do
`AdminDevAIProviderManager` (Gemini premium → Groq → Gemini gratuito) sem
consumir a cota gratuita compartilhada com usuários reais do `USER`.

## Contrato

- `Intent` é imutável e carrega correlação, `kind`, mensagem original,
  instante de interpretação, comando opcional e parâmetros.
- `IntentKind` é um vocabulário fechado com quatro valores: `create_mission`,
  `query_mission`, `mission_command` e `unknown`.
- `command` só é aceito quando `kind` é `mission_command` e reaproveita
  diretamente `MissionCommand` de `docs/MISSION_SYSTEM.md`
  (`activate`, `pause`, `resume`, `complete`, `cancel`, `expire`); nenhum
  comando novo é criado por esta tarefa.
- `IntentParameters` reaproveita conceitos já existentes: `search_query` e o
  par `target_amount`/`target_currency` espelham `MissionCriteria`
  (`docs/MISSION_CRITERIA.md`), `sources` aceita somente as quatro fontes
  selecionáveis da V1 (`pichau`, `terabyte`, `amazon`, `kabum`, conforme
  `docs/TELEGRAM.md`) e `mission_reference` é um texto livre para identificar
  a missão alvo de uma consulta ou comando, já que a V1 não expõe API nem
  identificador numérico ao usuário.
- `create_mission` e `query_mission` cobrem exatamente a exigência de
  `docs/MVP.md`: "Um usuário autorizado consegue criar e consultar uma missão
  pelo canal Telegram".

## Interpretação

`IntentInterpreter.interpret` monta uma `AIRequest` com propósito
`interpret_purchase_intent`, perfil `USER` e uma mensagem de sistema que exige
resposta em um único formato JSON fechado. A resposta é convertida com parsing
estrito: JSON inválido, campos fora do contrato, valores fora do vocabulário
fechado ou combinação inconsistente entre `kind` e `command` resultam sempre
em `IntentKind.UNKNOWN`, nunca em um comando inventado.

Falhas do provedor (`AIProviderError`, incluindo cota excedida ou
indisponibilidade) não são convertidas em `unknown`: elas continuam
propagadas para que um canal futuro informe o usuário, preservando o aviso de
cota definido na TASK-031.

## Limites

Este módulo não conhece Telegram, webhook, teclado ou qualquer outro canal
(TASK-033 em diante). Ele não executa transições de missão nem decide criar
ou consultar nada: apenas devolve a intenção estruturada para que uma camada
futura decida agir. Nenhum campo, tabela ou evento novo de domínio foi criado.
