# TASK-034 — Integrar webhook Telegram

Status: Concluída em 2026-08-07

## Objetivo

Receber e autenticar atualizações reais do Telegram por webhook HTTP,
traduzindo cada mensagem de texto em um `Intent` estruturado via
`TelegramIntentAdapter` (TASK-033), sem executar nenhuma ação de missão nem
responder ao usuário.

## Escopo

- Rota `POST /telegram/webhook`, fora de `/api/v1`, autenticada por segredo
  compartilhado (`X-Telegram-Bot-Api-Secret-Token` contra
  `AISHOPPING_TELEGRAM_WEBHOOK_SECRET`), com envelope de erro padrão no `401`.
- Atualizações sem mensagem de texto são reconhecidas e ignoradas com `204`.
- Falha de interpretação (cota, indisponibilidade, conteúdo inválido) depois
  de uma entrega autenticada nunca vira `500`: é registrada e a rota ainda
  responde `204`, sem reentrega pelo Telegram e sem mecanismo de retry, fila,
  resposta ao usuário ou execução de comando — isso pertence às TASK-035/036.
- Script manual `backend/scripts/register_telegram_webhook.py`
  (`set`/`delete`/`info`) contra a Bot API real, sem SDK.
- Nenhum SDK do Telegram, comando de missão, notificação ou preferência de
  usuário nesta tarefa.

## Critério de aceite

Escopo concluído, documentado em `docs/architecture/telegram-adapter.md` e
`docs/development/api-conventions.md`, e coberto por testes automatizados aprovados
(`scripts\check.cmd` completo em Python 3.14.6: 265 testes, 94,41% de
cobertura). Validado de ponta a ponta contra o Telegram real usando um túnel
`cloudflared`: mensagem real recebida, autenticada, traduzida e processada
pelo Gemini real (`ai_provider_attempt` com `outcome=succeeded`), resposta
`204`; requisições sem segredo válido rejeitadas com `401` sem chamar a IA.
Webhook removido e túnel encerrado ao final da validação.

