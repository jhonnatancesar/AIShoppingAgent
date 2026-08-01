# TASK-008 — Criar logging estruturado

Status: Concluída

## Objetivo

Planejar e executar, quando solicitada, a etapa “Criar logging estruturado”.

## Escopo

Executar somente o objetivo desta tarefa, conforme AGENTS.md, CLAUDE.md e a documentação em docs/.

## Critério de aceite

Logging JSON configurável em `stdout` para aplicação e Uvicorn, com eventos HTTP contendo método, caminho, status e duração sem dados sensíveis. Contrato documentado, coberto por testes e validado no Docker Compose.

