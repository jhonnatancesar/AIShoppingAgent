# TASK-036 — Criar notificações Telegram

Status: Concluída em 2026-08-08

## Objetivo

Entregar proativamente pelo Telegram os alertas de preço publicados como
eventos duráveis, com destino privado, consumo rastreável e execução contínua
compatível com Docker e Ubuntu Server.

## Escopo

- Persistir `User.telegram_chat_id` somente a partir de mensagens do tipo
  `private`; grupos, supergrupos e canais nunca viram destino automático.
- Consumir exclusivamente `price.decreased.v1` e
  `price.target_reached.v1` pelo consumidor `telegram_price_alerts_v1`.
- Formatar mensagens mínimas em português, sem IA e sem expor payload bruto,
  credenciais ou dados pessoais.
- Registrar cada entrega como `succeeded` ou `failed` no histórico append-only
  da TASK-044; falha permanece elegível para retry at-least-once.
- Disponibilizar processo contínuo `python -m app.telegram.worker` e o serviço
  Docker Compose `telegram_notifier`.
- Não implementar preferências (TASK-037), backoff/dead-letter queue
  (resiliência futura), e-mail, scheduler de coleta ou novos produtores de
  eventos.

## Critério de aceite

- Destino privado persistido pela migração reversível `20260808_0005`, com
  unicidade e constraint que o vincula à identidade Telegram da pessoa.
- Alertas válidos chegam à Bot API real e geram tentativa durável de sucesso.
- Destino ausente/inativo, payload inválido e rejeição da Bot API geram códigos
  sanitizados de falha, sem falso sucesso.
- Processo executa no Linux headless dentro do Docker e mantém o contrato
  transacional/concorrente da TASK-044.
- Suíte automatizada, PostgreSQL real, Telegram real e Docker aprovados.

## Implementação

- `app.telegram.notifications`: roteamento, formatação e consumo dos alertas.
- `app.telegram.worker`: polling contínuo ou execução única com `--once`.
- `app.telegram.bot_api.send_message`: rejeições `ok=false` agora levantam
  `TelegramBotAPIError` sanitizado.
- `app.events.claim_unconsumed_events`: filtro opcional por tipos de evento,
  permitindo que o consumidor não reivindique fatos fora de seu escopo.
- `compose.yaml`: serviço contínuo `telegram_notifier`, usando a mesma imagem
  do monólito e o PostgreSQL compartilhado.

## Validação

- 411 testes automatizados aprovados; cobertura total de 94,89%.
- Migração validada em PostgreSQL 18 descartável no ciclo
  upgrade → downgrade → upgrade, sem resíduos.
- Teste real criou missão/evento temporários, enviou a notificação de alvo
  atingido ao Telegram, confirmou `succeeded` em
  `event_consumption_attempts` e fez rollback integral dos dados de teste.
- Imagem Linux construída e `telegram_notifier --once` executado com sucesso
  no Docker headless, equivalente ao modo operacional do Ubuntu Server.

## Próxima tarefa no fluxo

TASK-037 — Criar preferências de usuário.

