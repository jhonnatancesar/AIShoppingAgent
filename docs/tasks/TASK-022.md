# TASK-022 — Criar agenda de missões

Status: Concluída em 2026-08-02

## Objetivo

Criar a agenda persistente e recorrente que determina quando uma missão ativa está pronta para iniciar novo ciclo.

## Escopo

- Uma agenda editável e opcional por missão, protegida por `RESTRICT`.
- Intervalo fixo positivo em minutos, próxima execução, última execução iniciada e ativação lógica.
- Consulta determinística apenas de agendas habilitadas, vencidas e pertencentes a missões ativas não expiradas.
- Bloqueio concorrente com `FOR UPDATE SKIP LOCKED` e avanço sem criar backlog retroativo.
- Migration reversível, testes e documentação, sem worker, coleta, provider ou API.

## Critério de aceite

Agenda persistente e concorrente validada em PostgreSQL real, sem antecipar a execução de coleta das tarefas seguintes.

