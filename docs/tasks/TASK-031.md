# TASK-031 — Adicionar telemetria de IA

Status: Concluída em 2026-08-02

## Objetivo

Registrar telemetria sanitizada de cada tentativa de IA pelo AI Provider
Manager, sem persistir conteúdo sensível.

## Escopo

- Registrar de forma sanitizada provedor e modelo efetivamente usados, inclusive
  quando ADMIN/DEV recorrerem ao fallback gratuito.
- Preservar dos erros de quota somente o tempo ou horário de reset informado pelo
  provedor, sem resposta bruta, prompt, token ou credencial.
- Disponibilizar um aviso seguro para os futuros canais informarem que a cota
  acabou e quando o chat poderá ser usado novamente; se o provedor não informar
  reset, declarar explicitamente que o prazo é desconhecido.
- Não implementar o canal Telegram nem persistência de telemetria fora do escopo
  definido para esta tarefa.

## Critério de aceite

Telemetria diferencia uso premium e fallback, não expõe conteúdo sensível e
representa retomada de quota sem inventar prazo ausente. Validado o fluxo
real ADMIN/DEV: o premium retornou `429` com reset informado, a telemetria
registrou a falha e o fallback gratuito respondeu com sucesso.

