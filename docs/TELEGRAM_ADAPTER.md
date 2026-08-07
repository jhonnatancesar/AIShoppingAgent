# Adaptador Telegram

A camada `app.telegram` representa a fronteira de entrada do canal Telegram.
Ela conhece apenas o formato bruto de uma mensagem do Telegram e o
`IntentInterpreter` já existente (TASK-032); não conhece domínio de missão,
não decide nem executa nada.

## Contrato

- `TelegramMessage` é imutável e representa uma mensagem recebida: `chat_id`
  e `user_id` (identificadores brutos do Telegram, sem resolução para o
  `User` interno), `text` e `received_at` (sempre com fuso horário).
- `TelegramIntentAdapter` recebe um `IntentInterpreter` já configurado e
  expõe `interpret(message)`, que encaminha `text` e `received_at` ao
  `IntentInterpreter.interpret` e devolve o `Intent` resultante sem
  inspecionar `kind` ou `command`.

Toda a interpretação de linguagem natural continua exclusivamente no
`IntentInterpreter` (`docs/INTENT_INTERPRETATION.md`), agnóstico de canal;
este módulo apenas adapta o formato de entrada do Telegram para ele.

## Limites

Este módulo não abre webhook, não usa nenhum SDK do Telegram, não define
comandos ou teclado, não envia notificações e não resolve preferências de
usuário. Essas responsabilidades pertencem, respectivamente, à TASK-034
(webhook), TASK-035 (comandos de missão), TASK-036 (notificações) e
TASK-037 (preferências de usuário).
