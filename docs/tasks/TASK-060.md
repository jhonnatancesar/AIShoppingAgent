# TASK-060 — Perfil de IA por papel, cadastro inicial e placeholder de upgrade

Status: Concluída

## Objetivo

1. O webhook do Telegram passa a escolher o perfil de IA (`USER` vs
   `ADMIN`/`DEV`) a partir de `User.role` do usuário resolvido, em vez de
   usar `USER` fixo sempre — permitindo que usuários `ADMIN`/`DEV` reais
   usem a cascata Gemini premium/Groq (TASK-059) nas próprias interações
   pelo Telegram.
2. O usuário do próprio dono do projeto é elevado manualmente para `ADMIN`,
   como ação pontual — não uma capacidade de autopromoção geral.
3. Um fluxo de cadastro inicial via Telegram, capturando nome de usuário,
   e-mail, lojas favoritas (entre as quatro selecionáveis da V1) e
   preferências de categoria (ex.: games, móveis). Sem senha nem
   autenticação real — ver TASK-061 (`DEC-019`).
4. Um comando ou opção de "mudar de usuário/perfil" visível ao usuário, mas
   **inativo** — reservado para uma futura oferta de upgrade (gratuita por
   enquanto, potencialmente paga na V2), sem nenhuma lógica funcional real
   ainda.

## Contexto

Registrada em `DEC-018` a partir de um pedido do usuário logo após decidir
executar a TASK-058. Motivação explícita: hoje `backend/app/telegram/router.py`
nunca lê `User.role` — toda interação real do Telegram usa `IntentInterpreter`
com o perfil `USER` fixo (só Gemini gratuito), então simplesmente marcar o
usuário como `ADMIN` no banco não teria nenhum efeito sem essa mudança no
despacho do webhook.

## Escopo

- Determinar o perfil de IA (`UserRole`) a partir do `User.role` já
  resolvido pelo webhook (`get_or_create_telegram_user`, TASK-056), e
  passá-lo para `IntentInterpreter.interpret(..., profile=...)` (parâmetro
  já existente desde a TASK-059).
- Construir o `AIProviderManager` correto por requisição conforme o papel
  resolvido: `build_user_ai_provider_manager()` para `USER`,
  `build_admin_dev_ai_provider_manager()` para `ADMIN`/`DEV`.
- Elevar manualmente o `User` do dono do projeto para `ADMIN` (ação pontual,
  identificando o registro pelo `telegram_user_id` já vinculado).
- Implementar o fluxo de cadastro inicial via Telegram (comando dedicado,
  ex.: `/cadastro`, com os passos necessários para capturar cada campo),
  persistindo nome de usuário, e-mail, lojas favoritas e preferências de
  categoria, sem duplicar a resolução de identidade da TASK-056.
- Registrar um comando dedicado do Telegram (ex.: `/upgrade`) que responde
  apenas que a função está "em breve" — **não executa nenhuma ação real**.

## Fora de escopo

- Não implementa autopromoção: nenhum usuário comum pode virar `ADMIN`/`DEV`
  sozinho por este fluxo; a elevação do dono do projeto é manual e pontual.
- Não implementa cobrança, plano pago, créditos ou qualquer lógica
  funcional de upgrade — o placeholder fica sempre inativo
  (`docs/OUT_OF_SCOPE.md`: "Plano PLUS", "Usuário pago" ficam para a V2).
- Não implementa senha nem autenticação real — ver TASK-061 (`DEC-019`).
- Não altera o vocabulário fechado de `IntentKind`/`IntentParameters`/
  `MissionCommand`.
- Não é a TASK-036 (notificações) nem a TASK-037 (preferências de notificação)
  nem a TASK-058 (confirmação de intenção antes de executar).

## Critério de aceite

Uma mensagem real de um usuário `ADMIN`/`DEV` pelo Telegram resulta em uma
chamada de IA através da cascata `ADMIN/DEV` (validado de ponta a ponta,
incluindo o caso real do usuário do dono do projeto). O cadastro inicial
funciona de ponta a ponta com os campos definidos. A opção de "mudar de
usuário/perfil" aparece para o usuário mas não executa nenhuma ação.
`scripts\check.cmd` completo aprovado.

## Resultado da validação real (2026-08-08)

Ambiente real: API rodando no host (`uvicorn`, lendo `backend/.env`),
PostgreSQL via Docker Compose, túnel público via `cloudflared`, webhook e
comandos (`/cadastro`, `/upgrade`) registrados contra a Bot API real.

- Mensagem real de um usuário recém-criado (`role=USER`) resolvida e
  interpretada com sucesso via Gemini — confirma que o reordenamento
  (resolver `User` antes de interpretar) não quebrou o caminho `USER`
  existente.
- Usuário elevado manualmente para `ADMIN` (`UPDATE` direto,
  identificado pelo `telegram_user_id` resolvido na mensagem anterior).
- Mensagem seguinte do mesmo usuário, agora `ADMIN`: o Gemini premium
  respondeu `429` (cota excedida) e a cascata caiu para o Groq real com
  sucesso — a mesma cascata da TASK-059, agora acionada por uma interação
  real do Telegram, não só por ferramenta de validação manual.
- `/cadastro` completo validado de ponta a ponta: `username`, `email`,
  `favorite_stores` (múltiplas lojas) e `preferred_categories` (múltiplas
  categorias com espaços) persistidos corretamente, `registration_step`
  voltou a `null` ao final — nenhuma chamada de IA disparada durante o
  fluxo.
- `/upgrade` validado: resposta estática confirmada pelo usuário, nenhuma
  chamada de IA disparada.
- `scripts\check.cmd` completo aprovado: 345 testes, 94,98% de cobertura.
