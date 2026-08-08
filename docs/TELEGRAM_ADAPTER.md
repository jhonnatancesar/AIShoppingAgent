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
  expõe `interpret(message)`, que encaminha `text` ao
  `IntentInterpreter.interpret` e devolve o `Intent` resultante sem
  inspecionar `kind` ou `command`. `requested_at` usa o relógio local no
  instante da chamada, não `message.received_at` (TASK-058): o horário do
  Telegram vem de outra fonte que `finished_at` da resposta do provedor, e
  comparar os dois faz `validate_provider_response` falhar sob qualquer
  desalinhamento entre os relógios.

Toda a interpretação de linguagem natural continua exclusivamente no
`IntentInterpreter` (`docs/INTENT_INTERPRETATION.md`), agnóstico de canal;
este módulo apenas adapta o formato de entrada do Telegram para ele.

## Webhook real (TASK-034)

`POST /telegram/webhook` (fora de `/api/v1`, como `/health`) recebe atualizações
reais do Telegram. Cada requisição é autenticada comparando o cabeçalho
`X-Telegram-Bot-Api-Secret-Token` com `AISHOPPING_TELEGRAM_WEBHOOK_SECRET` por
comparação de tempo constante; sem correspondência, a resposta é `401` no
envelope de erro padrão do `docs/API_CONVENTIONS.md`. Atualizações sem mensagem
de texto (foto, callback, mensagem editada) são reconhecidas e ignoradas com
`204`, sem erro.

Depois de autenticar o transporte, a TASK-046 valida a identidade da pessoa:
operações só seguem em chat `private` com `chat.id == message.from.id`, após
resolver um `User` ativo. Grupo, supergrupo, canal, divergência ou conta inativa
retornam `204` sem IA, domínio, resposta ou mutação; o log contém apenas um
motivo fechado e sanitizado. Usuário/senha e sessão pertencem à TASK-061.

Uma vez autenticadas entrega e identidade, o processamento por IA é
tratados como falhas independentes: se a interpretação falhar (`AIProviderError`
por cota ou indisponibilidade, ou `TelegramContractError` por conteúdo inválido),
a falha é registrada — a telemetria sanitizada da TASK-031 já grava o resultado
de cada tentativa de IA — e a rota ainda responde `204`, sem transformar uma
falha de IA em falha de transporte que faria o Telegram reentregar a mesma
atualização. `500` fica reservado a falhas internas verdadeiramente inesperadas.

`backend/scripts/register_telegram_webhook.py` registra (`--action set --url`),
consulta (`--action info`) e remove (`--action delete`) o webhook na Bot API real,
usado durante a validação manual com um túnel HTTPS local (`cloudflared`).

## Comandos de missão e resposta (TASK-035)

A partir da TASK-035, a rota também resolve a identidade do usuário
(`get_or_create_telegram_user`, TASK-056), executa a ação de missão
correspondente ao `Intent` (criar, consultar ou comandar) e responde ao
Telegram com `backend/app/telegram/bot_api.py` (`send_message`, via
`asyncio.to_thread`, sem bloquear o loop de eventos e sem SDK novo). O
despacho por `IntentKind`, a resolução de missão por texto e o limite entre
`204` e `500` estão detalhados em `docs/MISSION_COMMANDS.md`.

## Destino privado para notificações (TASK-036)

O `TelegramMessage` também transporta `chat_type`. Depois que a identidade do
canal foi aceita e o usuário ativo foi resolvido, o webhook persiste `chat_id`
da conversa privada direta. Esse passo não envia notificação e não contém
lógica de alerta; apenas mantém um destino seguro para o consumidor desacoplado.

## Limites

O adaptador de intenção não usa SDK, não define teclado interativo e não resolve
preferências. A TASK-037 adicionou `/preferencias` diretamente no roteador,
antes da IA, por `app.telegram.preferences`; isso não altera esta fronteira. A
entrega proativa fica em `app.telegram.notifications`.
