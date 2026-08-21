# Administração de missões

Procedimentos manuais sobre `missions` e tabelas relacionadas. Para o
procedimento genérico de acessar o `psql`, veja
[PostgreSQL](../database/postgresql.md).

Estados reais (`missions.status`, enum PostgreSQL `mission_status`):
`draft`, `active`, `paused`, `completed`, `cancelled`, `expired`. Comandos
reais de transição (`mission_transitions.command`): `activate`, `pause`,
`resume`, `complete`, `cancel`, `expire`. `completed`, `cancelled` e
`expired` são estados **terminais** — não têm saída.

O caminho seguro e suportado para mudar o estado de uma missão é sempre
pelos comandos do bot no Telegram (`/listar_missoes`, `/editar_missao`,
`/cancelar_missao`) — eles chamam `transition_mission`, que grava o
histórico em `mission_transitions` e incrementa `state_version` na mesma
transação. As consultas abaixo são para **inspeção**; a seção "Correção
manual avançada" no fim desta página é para quando não há alternativa pelo
bot, e exige cuidado extra.

## Listar missões

Todas, mais recentes primeiro:

```sql
SELECT id, user_id, title, status, expires_at, created_at
FROM missions
ORDER BY created_at DESC
LIMIT 20;
```

Por status:

```sql
SELECT id, user_id, title, status, created_at
FROM missions
WHERE status = 'active'
ORDER BY created_at DESC;
```

## Encontrar uma missão específica

Por ID:

```sql
SELECT id, user_id, title, status, expires_at, state_version, created_at, updated_at
FROM missions
WHERE id = '<UUID_DA_MISSAO>';
```

Por usuário:

```sql
SELECT id, title, status, created_at
FROM missions
WHERE user_id = '<UUID_DO_USUARIO>'
ORDER BY created_at DESC;
```

## Ver critério (busca e preço-alvo) de uma missão

```sql
SELECT mc.search_query, mc.target_amount, mc.target_currency
FROM mission_criteria mc
WHERE mc.mission_id = '<UUID_DA_MISSAO>';
```

## Ver lojas selecionadas por uma missão

```sql
SELECT s.code, s.name
FROM mission_sources ms
JOIN stores s ON s.id = ms.store_id
WHERE ms.mission_id = '<UUID_DA_MISSAO>';
```

## Ver histórico de transições de uma missão

```sql
SELECT from_status, to_status, command, actor_type, reason, transitioned_at
FROM mission_transitions
WHERE mission_id = '<UUID_DA_MISSAO>'
ORDER BY transitioned_at ASC;
```

`mission_transitions` é append-only (trigger bloqueia `UPDATE`/`DELETE`) —
é sempre a fonte confiável do histórico real de estado, mesmo que uma
correção manual tenha sido feita diretamente em `missions.status`.

## Ver agendamento de coleta de uma missão

```sql
SELECT interval_minutes, next_run_at, last_run_at, is_enabled
FROM mission_schedules
WHERE mission_id = '<UUID_DA_MISSAO>';
```

`is_enabled` só deve ficar `true` para missões em `active`; missões
`cancelled`, `completed` ou `expired` devem ter o schedule desabilitado
para não gerar coleta desnecessária.

## Correção manual avançada (último recurso)

Use apenas quando o bot não oferece o caminho (ex.: usuário sem acesso ao
Telegram, correção de uma inconsistência já identificada) e você entende o
impacto. Uma alteração direta em `missions.status` **não** aciona o que o
serviço de transição faz automaticamente — para preservar a integridade do
histórico, registre a transição manualmente na mesma operação:

```sql
-- 1. Confirme o estado atual e a versão
SELECT id, status, state_version FROM missions WHERE id = '<UUID_DA_MISSAO>';

-- 2. Dentro de uma transação, atualize a missão e registre a transição
BEGIN;

UPDATE missions
SET status = '<NOVO_STATUS>', state_version = state_version + 1, updated_at = now()
WHERE id = '<UUID_DA_MISSAO>' AND status = '<STATUS_ANTERIOR_ESPERADO>';

INSERT INTO mission_transitions (id, mission_id, from_status, to_status, command, actor_type, reason, transitioned_at)
VALUES (gen_random_uuid(), '<UUID_DA_MISSAO>', '<STATUS_ANTERIOR_ESPERADO>', '<NOVO_STATUS>', '<comando_correspondente>', 'manual_admin', '<motivo_sem_dado_pessoal>', now());

-- Se o novo status não for 'active', desabilite o schedule também:
UPDATE mission_schedules SET is_enabled = false, updated_at = now() WHERE mission_id = '<UUID_DA_MISSAO>';

COMMIT;

-- 3. Confirme o resultado
SELECT status, state_version FROM missions WHERE id = '<UUID_DA_MISSAO>';
```

- O `WHERE status = '<STATUS_ANTERIOR_ESPERADO>'` na `UPDATE` evita
  sobrescrever uma mudança concorrente — se `0` linhas forem afetadas, pare
  e releia o estado atual antes de repetir.
- `command` deve ser um dos valores reais (`activate`, `pause`, `resume`,
  `complete`, `cancel`, `expire`) mesmo numa correção manual, para manter o
  histórico consistente com o que o sistema normalmente grava.
- Não use `ROLLBACK` depois de já ter confirmado o resultado a terceiros —
  se algo parecer errado antes do `COMMIT`, prefira `ROLLBACK` e recomeçar
  a investigação.
- Nunca faça essa correção em um estado terminal (`completed`, `cancelled`,
  `expired`) sem certeza absoluta — eles são terminais por design.
