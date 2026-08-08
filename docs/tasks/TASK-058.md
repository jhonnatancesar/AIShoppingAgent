# TASK-058 — Confirmar a intenção interpretada antes de executar

Status: Concluída

## Objetivo

Antes de criar ou comandar qualquer missão a partir de um `Intent`
interpretado pela IA (`IntentInterpreter`, TASK-032), o webhook do Telegram
devolve ao usuário o texto da intenção interpretada e pede confirmação
explícita de que é aquilo que a pessoa quis dizer, só executando a ação
após a confirmação. `query_mission` (somente leitura) não exige
confirmação — decisão tomada na validação desta TASK para não adicionar
fricção onde não há risco.

## Contexto

Registrada em `DEC-015` a partir de um pedido do usuário durante a execução
da TASK-057: além de a IA interpretar texto livre sem exigir um padrão de
escrita (escopo da TASK-057), o usuário quis que a IA devolva o texto
interpretado pedindo confirmação antes de agir. Essa mudança altera o fluxo
de despacho do webhook definido nas TASKs 033 a 035 e por isso ficou fora
do escopo da TASK-057.

## Escopo

- `create_mission` e `mission_command` ficam "encenados" — `User`
  (`backend/app/users/models.py`) ganha `pending_intent` (JSONB opcional,
  migração `20260808_0002`) guardando só os dados mínimos para executar a
  ação (nunca o `Intent` bruto nem texto livre) — e só executam depois de
  confirmados. `query_mission` e `unknown` continuam imediatos.
- O webhook (`backend/app/telegram/router.py`) checa uma confirmação
  pendente **antes** de qualquer outro processamento (comandos
  `/cadastro`/`/upgrade`, cadastro em andamento, interpretação por IA).
- Confirmar/cancelar (`backend/app/telegram/confirmation.py`) é
  classificado pelo `AIProviderManager` do próprio perfil do usuário, com
  propósito e prompt dedicados (`interpret_confirmation_reply`) — decisão
  revista em campo: a primeira versão usava só palavra exata fora da IA, e
  a validação real mostrou que isso não reconhecia respostas informais
  nem erros de português, então o usuário pediu para passar pela IA também
  aqui, sempre (sem atalho por palavra exata, para não voltar ao problema
  original).
- Não altera o vocabulário fechado de `IntentKind`/`IntentParameters`/
  `MissionCommand` — o vocabulário de confirmação (`confirm`/`cancel`/
  `unclear`) é novo e exclusivo do módulo de confirmação.

## Fora de escopo

- Não é a TASK-036 (notificações proativas orientadas a evento) nem a
  TASK-037 (preferências de usuário).
- Não expõe teclado interativo nem qualquer UI além de texto.

## Critério de aceite

Uma mensagem real de `create_mission` ou `mission_command` pelo Telegram
resulta em confirmação pendente, descrita em português, sem executar a
ação; confirmar executa a ação; cancelar descarta; resposta não reconhecida
mantém a confirmação pendente. A classificação reconhece respostas
informais e erros de português, não só palavra exata. `scripts\check.cmd`
completo aprovado.

## Resultado da validação real (2026-08-08)

Mesmo ambiente real das TASKs 059/060 (API no host, PostgreSQL via Docker
Compose, túnel `cloudflared`, webhook e comandos registrados contra a Bot
API real).

- Criar missão especificando loja, confirmar com "sim" → missão criada
  `active`, `pending_intent` limpo.
- Comandar missão (pausar, depois concluir) com confirmações informais
  ("confirmo") → `transition_mission` executado corretamente, refletido no
  banco (`paused` → `completed`).
- Criar missão **sem** citar loja → encenada com as quatro fontes-padrão da
  V1, confirmação descrita corretamente.
- Cancelar com frase informal ("cancela essa aí, quero mais não") →
  reconhecido corretamente como cancelamento pelo classificador de IA, sem
  nenhuma palavra da lista original bater literalmente.
- `scripts\check.cmd` completo aprovado: 368 testes, 95,29% de cobertura.

Duas correções reais de infraestrutura, encontradas durante essa validação
e fora do escopo original da TASK, mas bloqueando-a — ver
`docs/CHANGELOG.md` e `docs/AI_PROVIDER_MANAGER.md` para detalhes:

1. Chave Gemini separada por perfil (`AISHOPPING_GEMINI_API_KEY_USER` /
   `AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`), pedida pelo usuário.
2. `validate_provider_response` estava fora do `try/except` nos dois
   managers, mascarando telemetria; a causa raiz real (comparar o relógio
   do Telegram com o relógio local, sem sincronia NTP) foi removida do
   `TelegramIntentAdapter`.
