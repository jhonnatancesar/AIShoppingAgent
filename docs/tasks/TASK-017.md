# TASK-017 — Criar consultas de histórico

Status: Concluída em 2026-08-02

## Objetivo

Criar consultas internas, paginadas, filtráveis e determinísticas sobre o
histórico imutável de preços.

## Escopo

- Listar observações por oferta, intervalo e disponibilidade.
- Obter a observação mais recente de uma oferta.
- Validar paginação e horários de filtro.
- Não criar API HTTP, alertas, gráficos, análises ou comparações.

## Ordem e dependências

Executar após a TASK-015, quando as observações históricas já estiverem
persistidas.

## Critério de aceite

Consultas somente leitura preservam a precisão monetária e retornam resultados
com paginação, total filtrado e ordenação estável por `observed_at` e `id`.

