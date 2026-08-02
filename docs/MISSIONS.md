# Missões persistentes

`Mission` representa uma intenção de compra e mantém a fonte de verdade para o
estado atual. A persistência básica foi introduzida na TASK-019; o ciclo de vida
funcional permanece definido em `docs/MISSION_SYSTEM.md`.

## Contrato

- `id`: UUID gerado pela aplicação;
- `user_id`: proprietário obrigatório, protegido por `RESTRICT`;
- `title`: título obrigatório, com até 200 caracteres e não vazio;
- `status`: enum PostgreSQL `mission_status`, iniciado em `draft`;
- `expires_at`: prazo opcional; ausência representa missão permanente;
- `state_version`: versão concorrente não negativa, iniciada em zero;
- `created_at` e `updated_at`: timestamps obrigatórios em UTC.

Os únicos estados persistidos são `draft`, `active`, `paused`, `completed`,
`cancelled` e `expired`. Quando informado, `expires_at` precisa ser posterior a
`created_at`.

## Consultas previstas

O índice `(user_id, status, created_at desc)` atende listagens recentes por
proprietário. Um índice parcial em `(status, expires_at)` contém apenas missões
com prazo e estado não terminal, apoiando a futura verificação de expiração.

## Limites

Comandos e mudanças de estado foram implementados na TASK-021, conforme
`docs/MISSION_TRANSITIONS.md`, e a agenda na TASK-022, conforme
`docs/MISSION_SCHEDULES.md`. Critérios pertencem à TASK-020; coleta, API, eventos
e alertas permanecem nas respectivas tarefas.
