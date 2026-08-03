# TASK-031 — Adicionar telemetria de IA

Status: Pendente

## Objetivo

Planejar e executar, quando solicitada, a etapa “Adicionar telemetria de IA”.

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
representa retomada de quota sem inventar prazo ausente.

