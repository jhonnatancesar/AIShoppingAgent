# ADR-013 — Desidentificação fail-closed e retenção operacional limitada

Status: Aceita

Data: 2026-08-08

## Contexto

A V1 precisa remover identificadores diretos sem destruir preços, eventos,
auditoria, transições e confirmações protegidos por imutabilidade e FKs
`RESTRICT`. Também precisa impedir que logs e telemetria cresçam ou preservem
PII sem limite.

## Decisão

- Tratar o procedimento como desidentificação/pseudonimização operacional,
  nunca como garantia de anonimização irreversível.
- Preservar o UUID interno e históricos necessários à integridade.
- Remover identificadores diretos, credenciais, sessões, tokens, preferências,
  intenção pendente e textos livres mutáveis em uma única transação.
- Auditar snapshots e fatos relacionados antes da mutação. PII em estrutura
  append-only interrompe a operação; triggers não são removidos ou contornados.
- Limitar logs Docker a `10m × 5`, Prometheus a `15d/2GB` e Jaeger a 10.000
  traces voláteis em container de 512 MB.
- Limpar manualmente action tokens após 24h e sessões após 30 dias; sem
  scheduler na V1.

## Consequências

Uma conta deixa de possuir vínculo direto com Telegram/perfil e não pode mais
operar. Históricos ainda são correlacionáveis pelo UUID técnico e podem conter
interesses comerciais desidentificados, portanto não se alega anonimização
jurídica. Conflitos históricos exigem avaliação específica futura em vez de
enfraquecer integridade.
