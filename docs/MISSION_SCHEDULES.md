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

A TASK-062 passou a consumir esta agenda com claim transacional curto, sem
manter row lock durante Playwright/HTTP. Novas missões recebem agenda atômica;
missões ativas antigas recebem backfill idempotente, sem reativar agendas
explicitamente desabilitadas. Runs abandonados são terminalizados após o TTL
operacional e resultados tardios são descartados.

## Intervalo: alvo da V1 vs. implantação temporária de 8 GB (`DEC-046`)

`interval_minutes` vem sempre de `Settings.collection_schedule_interval_minutes`
(`AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES`), configurável e idêntico
para todas as missões — não há intervalo por missão em produção, embora a
coluna suporte o valor por linha.

- **Alvo pretendido da V1:** 15 minutos, assim que o servidor tiver 16 GB de
  RAM.
- **Implantação atual, temporária, com 8 GB de RAM:** 30 minutos, só para
  reduzir o pico de Chromium/RAM até o upgrade. Não é decisão arquitetural
  permanente.

**Importante:** `advance_schedule` usa `schedule.interval_minutes`, o valor já
gravado na linha do banco — nunca uma leitura ao vivo de `Settings`. Trocar a
env var de 30 para 15 no futuro **não afeta agendas já persistidas**; só
missões criadas ou recuperadas (backfill) depois da troca recebem o novo
valor. O procedimento para migrar agendas existentes está em
`docs/OPERATIONS.md`.

## Stagger (distribuição entre missões)

`staggered_next_run_at` aplica um deslocamento aleatório único, limitado por
`Settings.collection_schedule_stagger_seconds`
(`AISHOPPING_COLLECTION_SCHEDULE_STAGGER_SECONDS`, padrão 300s = 5 min), para
que missões com o mesmo intervalo não fiquem sincronizadas no mesmo instante.
Aplicado **somente** na criação (`create_mission_from_criteria`) e no backfill
(`ensure_missing_schedules`) — nunca em `advance_schedule`, que preserva a
cadência fixa nas execuções seguintes. Uma agenda já persistida nunca tem seu
`next_run_at` recalculado por um restart do worker.

## Backoff por bloqueio externo (pendente — `DEC-046`)

Hoje, um resultado `provider_blocked` (401/403/429) não afeta `next_run_at`:
`advance_schedule` sempre avança pelo intervalo fixo, independente do
resultado. Um backoff persistente por fonte foi proposto e **rejeitado no
nível da `MissionSchedule` inteira** (atrasaria as quatro lojas de uma missão
por causa de uma só bloqueada). A modelagem mínima por `(mission_id, source)`
ainda não foi aprovada nem implementada; até lá, o único comportamento
existente é o corte do ciclo de fallback de disponibilidade em
401/403/429 (`backend/app/collection/providers/base.py`), que é por
execução, não persistente.
