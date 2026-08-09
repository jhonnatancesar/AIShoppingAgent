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

## Backoff persistente por fonte (`DEC-047`)

`advance_schedule`/`next_run_at` continuam intocados por falha: a missão
sempre avança pelo intervalo fixo, independente do resultado. O backoff
persistente foi implementado num nível mais fino, deliberadamente **não**
na `MissionSchedule` inteira (que atrasaria as quatro lojas de uma missão
por causa de uma só bloqueada), e sim em `MissionSource` — já a entidade
natural por `(mission_id, store_id)` — com duas colunas novas
(migração `20260809_0003`):

- `next_eligible_at: timestamptz | None` — `NULL` significa "sem backoff,
  segue a cadência normal da missão"; um valor futuro significa "só essa
  fonte específica fica de fora do claim até esse instante".
- `consecutive_blocks: int` (`>= 0`, padrão 0) — conta bloqueios
  confirmados consecutivos daquela fonte.

**Gatilho — só bloqueio externo confirmado.** `_is_confirmed_external_block`
(`backend/app/collection/orchestration.py`) só considera
`ProviderBlockedError` com `status` em `{401, 403, 429}`. O mesmo erro com
outro status (seletor ausente/oferta vazia, possível mudança de markup, não
bloqueio confirmado), timeout, erro de rede, erro de parsing/normalização,
erro interno ou falha ao gravar o resultado **nunca** acionam este backoff —
só o corte por execução que já existe em
`app.collection.providers.base` (`401/403/429` interrompe o fallback de
disponibilidade daquele ciclo, sem persistir nada).

**Fórmula** (`next_source_backoff`, `app/missions/schedule.py`), calculada
sobre o `interval_minutes` da própria `MissionSchedule` da missão — nunca um
valor fixo no código:

```
delay = min(interval_minutes * 2**consecutive_blocks, 360)  # teto 6h
```

O contador para de crescer assim que o teto é atingido pela primeira vez —
bloqueios seguintes mantêm o mesmo delay e o mesmo contador, em vez de
`2**consecutive_blocks` crescer indefinidamente. Com o intervalo temporário
de 30 min: 60 → 120 → 240 → 360 (a partir do 4º). Com o alvo de 15 min depois
do upgrade: 30 → 60 → 120 → 240 → 360 (a partir do 5º).

**Claim.** `claim_due_collections` (`orchestration.py`) filtra fonte por
fonte: `MissionSource.next_eligible_at IS NULL OR next_eligible_at <=
effective_now`. As demais fontes da mesma missão continuam sendo
reivindicadas normalmente; a agenda da missão não muda.

**Todas as fontes em backoff.** `advance_schedule` só é chamado quando pelo
menos uma fonte foi de fato reivindicada (`if mission_claims:`) — já era o
comportamento existente do claim, não uma mudança para o backoff. Se todas
as fontes selecionadas estiverem em backoff, nenhum `CollectionRun` é
criado, nenhum erro é gerado, e a agenda da missão **permanece due**: ela é
reexaminada no próximo poll (`collection_poll_seconds`, padrão 15s) em vez
de esperar um intervalo inteiro, então uma fonte que se torna elegível logo
após não fica presa até o próximo ciclo completo da missão.

**Reset.** No primeiro sucesso daquela mesma `(mission_id, store_id)`,
`_reset_source_backoff` zera `consecutive_blocks` e `next_eligible_at`. Só a
fonte que teve sucesso é resetada; as demais fontes da mesma missão, e
outras missões, ficam intocadas.
