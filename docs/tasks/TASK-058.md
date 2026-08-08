# TASK-058 — Confirmar a intenção interpretada antes de executar

Status: Pendente

## Objetivo

Antes de criar, consultar ou comandar qualquer missão a partir de um
`Intent` interpretado pela IA (`IntentInterpreter`, TASK-032), o webhook do
Telegram deve devolver ao usuário o texto da intenção interpretada e pedir
confirmação explícita de que é aquilo que a pessoa quis dizer, só executando
o comando de missão após a confirmação.

## Contexto

Registrada em `DEC-015` a partir de um pedido do usuário durante a execução
da TASK-057: além de a IA interpretar texto livre sem exigir um padrão de
escrita (escopo da TASK-057), o usuário quer que a IA devolva o texto
interpretado pedindo confirmação antes de agir. Essa mudança altera o fluxo
de despacho do webhook definido nas TASKs 033 a 035 e por isso está fora do
escopo da TASK-057.

## Escopo (a definir em detalhe na validação desta TASK)

- Alterar o despacho do webhook (`backend/app/telegram/`) para responder
  primeiro com a intenção interpretada e um pedido de confirmação, antes de
  executar `MissionCommand`.
- Definir como o estado de "aguardando confirmação" é mantido entre a
  mensagem do usuário e a resposta de confirmação (ex.: nova tabela, campo
  temporário, ou reinterpretação da próxima mensagem como confirmação).
- Definir o vocabulário de confirmação/cancelamento reconhecido pelo
  `IntentInterpreter` sem violar seu vocabulário fechado atual.

## Fora de escopo

- Não altera o vocabulário fechado de `IntentKind`, `IntentParameters` ou
  `MissionCommand` definido em `docs/INTENT_INTERPRETATION.md` além do
  estritamente necessário para reconhecer confirmação/cancelamento.
- Não é a TASK-036 (notificações proativas orientadas a evento) nem a
  TASK-037 (preferências de usuário).

## Critério de aceite

A definir na validação desta TASK, quando solicitada explicitamente.
