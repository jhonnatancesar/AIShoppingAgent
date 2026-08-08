# ADR-009 — Autenticar a identidade mínima do canal Telegram

## Status

Aceita em 2026-08-08 pela TASK-046.

## Contexto

O webhook já autenticava a entrega por segredo e vinculava
`message.from.id` a um `User`, mas aceitava operações vindas de qualquer tipo
de conversa e não bloqueava contas desativadas. Ao mesmo tempo, usuário/senha,
sessão e recuperação pertencem à TASK-061 e autorização por papel à TASK-047.

## Decisão

O segredo compartilhado, comparado em tempo constante, continua autenticando o
transporte. Somente depois dessa verificação a aplicação aceita identidade de
usuário em chat `private` quando `chat.id == message.from.id`. A resolução
get-or-create acontece após essas duas condições, e `User.is_active=false`
interrompe o fluxo antes de qualquer IA, domínio, cadastro, preferência,
resposta ou atualização do destino de notificação.

Recusas de identidade retornam `204` para encerrar a entrega e registram apenas
um motivo de catálogo fechado. IDs, texto e payload não entram nesse log.

## Consequências

- grupos, supergrupos, canais e identidades divergentes não provisionam usuário;
- conta inativa não produz efeitos e não causa retry do Telegram;
- o identificador não é aceito de texto ou de payload de domínio;
- senha, JWT, sessão, MFA e recuperação continuam exclusivamente na TASK-061;
- permissões por papel continuam exclusivamente na TASK-047.
