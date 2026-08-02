# Agenda de missões

`MissionSchedule` representa a recorrência operacional de uma missão. A TASK-022
implementa persistência e seleção de agendas vencidas, mas não cria worker nem
executa coleta.

## Contrato

- `id`: UUID gerado pela aplicação;
- `mission_id`: missão obrigatória e única, protegida por `RESTRICT`;
- `interval_minutes`: intervalo fixo positivo em minutos;
- `next_run_at`: próximo instante elegível em UTC;
- `last_run_at`: instante opcional da última execução iniciada;
- `is_enabled`: controle administrativo da agenda, verdadeiro por padrão;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

Uma missão pode ser permanente ou possuir `expires_at`. A agenda não altera o
ciclo de vida: apenas missões `active`, não expiradas, com agenda habilitada e
`next_run_at` vencido são retornadas para execução futura.

## Concorrência e progressão

`find_due_schedules` ordena por `(next_run_at, id)`, limita o lote e usa
`FOR UPDATE SKIP LOCKED`. Dois workers concorrentes não recebem a mesma agenda
dentro de suas transações.

`advance_schedule` registra `last_run_at` e move `next_run_at` para o primeiro
intervalo posterior ao início aceito. Intervalos perdidos não geram uma fila
retroativa de execuções, evitando tempestade de coletas após indisponibilidade.

## Limites

A frequência é um intervalo fixo; cron, calendário complexo e regras por fuso
local não pertencem ao MVP atual. A TASK-022 não cria processo em segundo plano,
`collection_runs`, Store Providers, API, eventos ou notificações.
