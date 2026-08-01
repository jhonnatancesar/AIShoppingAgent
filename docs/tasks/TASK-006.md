# TASK-006 — Criar módulo de saúde

Status: Concluída

## Objetivo

Planejar e executar, quando solicitada, a etapa “Criar módulo de saúde”.

## Escopo

Executar somente o objetivo desta tarefa, conforme AGENTS.md, CLAUDE.md e a documentação em docs/.

## Critério de aceite

Módulo de saúde expõe `GET /health` com resposta estável `{"status":"ok"}`, possui testes automatizados e é usado pelo healthcheck do contêiner da API. Endpoint validado localmente e por HTTP no Docker Compose.

