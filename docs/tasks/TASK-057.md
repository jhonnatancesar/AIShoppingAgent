# TASK-057 — Melhorar a robustez da interpretação de intenção

Status: Em execução — pausada por impedimento de cota, validação real parcial

## Objetivo

Melhorar a robustez da classificação de `IntentKind` e da extração de
`IntentParameters` pelo `IntentInterpreter` (TASK-032) para diferentes
formas de escrita do usuário (informal, gírias, erros de digitação, ordens
de frase variadas), sem alterar o vocabulário fechado nem o contrato já
definido em `docs/INTENT_INTERPRETATION.md`.

## Contexto

Durante a validação manual real da TASK-035, uma mensagem real do usuário
foi classificada como `unknown` quando, na avaliação do usuário, deveria ter
sido reconhecida. O `IntentInterpreter` já funciona corretamente para as
formas de escrita testadas nas TASKs 032, 034 e 035 (validado contra o
Gemini real); esta tarefa trata da qualidade/robustez da classificação para
uma variedade maior de estilos de escrita, não de um defeito estrutural.

## Escopo

- Revisar e refinar o prompt de sistema usado por `IntentInterpreter`
  (`backend/app/intent/interpreter.py`), incluindo exemplos adicionais de
  variações de escrita, sem alterar o vocabulário fechado de `IntentKind`,
  `MissionCommand` reaproveitado ou os campos de `IntentParameters`.
- Ampliar a validação manual real (`backend/scripts/validate_intent_interpreter.py`)
  com um conjunto maior e mais diverso de mensagens reais, cobrindo diferentes
  estilos de escrita para os quatro valores de `IntentKind`.
- Atualizar os testes unitários existentes (`tests/test_intent_interpreter.py`)
  quando o comportamento coberto por eles mudar.

## Fora de escopo

- Não altera o vocabulário de `IntentKind`, `IntentParameters` ou
  `MissionCommand`.
- Não altera `AIProviderManager`, o perfil `USER` ou o modelo Gemini usado.
- Não altera `app.telegram` (adaptador, webhook ou despacho de missão —
  TASKs 033 a 035), nem cria teclado interativo, notificações (TASK-036) ou
  preferências de usuário (TASK-037).

## Critério de aceite

Um conjunto ampliado de mensagens reais, incluindo variações de escrita
informal, é validado contra o Gemini real com uma taxa de classificação
correta satisfatória para os quatro valores de `IntentKind`, sem invenção de
campo fora do vocabulário fechado. Testes automatizados aprovados.

## Impedimento conhecido (2026-08-08)

O prompt de sistema de `backend/app/intent/interpreter.py` foi refinado com
orientação explícita de robustez a escrita informal e exemplos few-shot
cobrindo os quatro `IntentKind`. O script `validate_intent_interpreter.py`
foi ampliado para 19 mensagens diversas (informal, gírias, erros de
digitação, ordens de frase variadas) cobrindo os quatro valores, com pacing
e retry respeitando `quota_reset_at`. `tests/test_intent_interpreter.py` foi
atualizado. `scripts\check.cmd` completo aprovado: 299 testes, 94,73% de
cobertura.

A validação real contra o Gemini ficou **incompleta**: apenas 3 das 19
mensagens (todas `create_mission`) foram validadas com sucesso antes da cota
gratuita do perfil `USER` se esgotar; as tentativas seguintes falharam com
`AIProviderQuotaExceeded` mesmo após esperas de até 60s em 5 tentativas,
sempre com `quota_reset_at` desconhecido (prazo de reset não informado pelo
provedor). Rotear a validação pelo Gemini premium ou por outro provedor (ex.:
a chave Anthropic presente em `backend/.env`) foi descartado por violar o
guardrail permanente do projeto: IA só é acessada via `AIProviderManager`, e
o perfil `USER` usa exclusivamente Gemini gratuito — testar com outro
provedor não validaria o comportamento real de produção.

**Próxima ação:** retomar a validação manual real (`python
backend/scripts/validate_intent_interpreter.py`) quando a cota gratuita do
Gemini for reposta, cobrindo as mensagens de `query_mission`,
`mission_command` e `unknown` ainda não validadas, antes de marcar esta TASK
como concluída.
