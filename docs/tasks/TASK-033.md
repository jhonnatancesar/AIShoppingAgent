# TASK-033 — Definir adaptação Telegram

Status: Concluída em 2026-08-07

## Objetivo

Definir a fronteira de entrada que traduz uma mensagem bruta do Telegram em
uma intenção estruturada, sem lógica de domínio, reaproveitando
exclusivamente o `IntentInterpreter` da TASK-032.

## Escopo

- Contrato imutável `TelegramMessage` (`chat_id`, `user_id`, `text`,
  `received_at`), sem resolução para o `User` interno.
- `TelegramIntentAdapter`, que encaminha a mensagem ao `IntentInterpreter`
  já existente e devolve o `Intent` resultante sem interpretá-lo.
- Nenhum SDK do Telegram, webhook, comando, teclado, notificação ou
  preferência de notificação (TASK-034 a TASK-037).

## Critério de aceite

Escopo concluído, documentado em `docs/TELEGRAM_ADAPTER.md` e coberto por
testes automatizados aprovados (`ruff check`, `ruff format --check` e
`pytest` com cobertura, executados em Python 3.14.6 via `scripts\check.cmd`).
Sem integração externa nova, não houve validação manual adicional além da
já realizada para o `IntentInterpreter` na TASK-032.

