# TASK-030 — Implementar perfis ADMIN e DEV

Status: Concluída

## Objetivo

Implementar uma política única de IA para os perfis ADMIN e DEV.

## Escopo

- ADMIN e DEV compartilham o mesmo manager, sem políticas separadas.
- Tentar primeiro `gemini-3.1-pro-preview`, o melhor Gemini geral atual.
- Em quota, exigência de crédito ou indisponibilidade do premium, usar
  `gemini-3.6-flash`, disponível no nível gratuito.
- USER comum continua usando somente o Gemini gratuito.
- Usuário pago, OpenAI e Claude pertencem à V2 e não são integrados na V1.

## Critério de aceite

ADMIN e DEV usam a mesma política; sucesso premium é preservado e falhas
recuperáveis acionam o Gemini gratuito. USER nunca tenta o modelo pago. O fluxo é
validado com testes automatizados e chamadas reais aplicáveis ao nível gratuito.

