# TASK-057 — Melhorar a robustez da interpretação de intenção

Status: Pendente

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
