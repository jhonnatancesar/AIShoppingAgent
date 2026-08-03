# TASK-028 — Definir contrato AI Provider Manager

Status: Concluída em 2026-08-02

## Objetivo

Definir a porta única e agnóstica para requisições e respostas de IA.

## Escopo

- Definir mensagens, requisição e resposta imutáveis e validadas.
- Definir protocolos assíncronos do manager e dos adaptadores internos.
- Definir erros sanitizados com semântica de nova tentativa.
- Não integrar provedores, escolher modelos, configurar chaves ou telemetria.

## Critério de aceite

Contratos neutros cobrem `USER`, `ADMIN` e `DEV`, preservam correlação e impedem
que detalhes ou falhas brutas de provedores atravessem a fronteira da aplicação.

