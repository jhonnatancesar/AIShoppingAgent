# TASK-108 — Fila justa e controle de carga

Status: **Formalizada e corrigida (planejamento aprovado, 2026-08-22); pronta para implementação.**

## Objetivo

Garantir que nenhum USER monopolize a capacidade de coleta do sistema,
introduzindo processamento justo por usuário (fila/round-robin com
cooldown), sem afetar os limites e o pacing por loja que já existem, e
mantendo o `coupon_worker` (`DEC-093`, TASK-106) completamente separado e
de baixa prioridade.

## Estado real hoje (sem fila por usuário)

Levantado em `backend/app/collection/orchestration.py` e
`backend/app/collection/worker.py`:

- `run_batch(limit=25)` roda a cada `collection_poll_seconds` (default
  15s). `claim_due_collections` busca até `limit` **missões** due
  (`find_due_schedules_async`), e para **cada** missão due, itera **todas**
  as `MissionSource` elegíveis (ativas, sem backoff) criando um
  `CollectionRun` — uma claim por `(mission, store)`. Uma única chamada de
  `run_batch` já pode conter claims de vários usuários diferentes,
  misturados, sem nenhuma noção de "de quem" é cada claim para fins de
  ordenação.
- Todas as claims do batch resultante rodam em paralelo via
  `asyncio.gather`, limitadas só por `asyncio.Semaphore(max_concurrency)`
  (`collection_max_concurrency`, 1–4, default 4) — **um semáforo global do
  processo**, sem qualquer isolamento ou prioridade por usuário.
- O único agrupamento/isolamento que já existe é por `mission_id` (lock de
  seção crítica na Fase C) e por `(mission_id, store_id)` (backoff em
  `MissionSource` após bloqueio confirmado, `DEC-046`) — isso é o "pacing
  existente entre lojas" mencionado no pedido, e continua exatamente como
  está.
- **Não existe hoje**: fila por usuário, cooldown entre usuários,
  round-robin, nem qualquer feedback de "posição na fila" para o usuário.

## Configuração (corrigida, 2026-08-22)

Mesmo padrão de `Settings` já usado (`Field(default=..., ge=..., le=...)`,
env `AISHOPPING_`):

```python
max_concurrent_user_batches: int = Field(default=1, ge=1, le=4)
user_cooldown_min_seconds: float = Field(default=60.0, gt=0, le=600)
user_cooldown_max_seconds: float = Field(default=180.0, gt=0, le=600)
```

(Correção do usuário: **1–3 minutos**, não 10–15 minutos como na proposta
original — cooldown curto o bastante para não represar throughput, longo
o bastante para evitar que o mesmo usuário volte imediatamente ao topo da
fila.)

## Arquitetura (duas camadas de proteção, corrigida 2026-08-22)

Duas camadas independentes, cada uma protegendo uma coisa diferente:

### Camada 1 — por provider/loja (já existe, intocada)

Circuit breaker por `source_code`, backoff `next_eligible_at`/
`consecutive_blocks` em `MissionSource` após bloqueio confirmado
(`DEC-046`), timeouts por loja. Um provider em cooldown não trava os
demais — cada `(mission, store)` já é avaliado independentemente hoje.
Esta é a proteção **principal** contra excesso de requisições a uma
fonte externa; a TASK-108 não altera nada aqui.

### Camada 2 — fila justa por usuário (nova, esta TASK)

- **Fila FIFO/round-robin por `user_id`** — não por missão nem por claim
  individual. Deriva a ordem do agendamento já existente
  (`find_due_schedules_async`), agrupando as missões due por `user_id`.
- **`max_concurrent_user_batches = 1`**: só as claims de **um** usuário
  são processadas por vez (mesmo semáforo/gather de hoje, escopado ao
  lote do usuário da vez).
- **Regra de cooldown (confirmada pelo usuário):**
  1. termina o lote do USER A;
  2. A fica inelegível por 1–3 min (jitter, `user_cooldown_min/max`);
  3. A volta para o **fim da fila** (não fica parado esperando no topo);
  4. USER B/C/etc. executam imediatamente se elegíveis — **o cooldown de
     A nunca pausa a fila inteira**.
- **`coupon_worker` (TASK-106) fica inteiramente fora desta fila** — já é
  processo/loop separado por decisão da `DEC-093`; "baixa prioridade"
  significa nunca competir pelo mesmo semáforo/slot de concorrência do
  `collection_worker`, não uma prioridade dentro do mesmo scheduler.

## Pesquisa pela Web (confirmado: rate limit, não fila)

A pesquisa (`search_router.py`) é síncrona e read-only sobre dados já
persistidos — **não consome recursos de coleta/provider**, então fica
fora da fila da TASK-108. Reaproveita `max_daily_searches` (TASK-107),
já implementado. Se a busca Web um dia passar a disparar coleta em tempo
real, essa operação nova é que entraria numa fila própria — não esta.

## Feedback ao USER durante espera

Aplica-se principalmente à criação de missão → primeira coleta (não à
pesquisa read-only, que responde na hora). Estados mínimos a comunicar:

1. "Pesquisa/missão recebida" (confirmação imediata de que o pedido foi
   aceito);
2. "Aguardando processamento" (na fila, ainda não é a vez do usuário);
3. posição/estado quando disponível (ex.: "sua coleta deve iniciar em
   breve" — sem prometer horário exato, dado que a fila é dinâmica);
4. nunca deixar a experiência parecer travada — algum indicador de
   progresso sempre visível.

Mecanismo exato (polling do status da missão, já existente, vs. algo
novo) é decisão de implementação, não deste documento.

## Visão ADMIN

Extensão do painel já existente (TASK-102, `admin_router.py`) com uma
tela/endpoint de fila: usuário sendo processado agora, usuários
aguardando (ordem da fila), próximos horários estimados de elegibilidade
(considerando o cooldown por usuário). Reaproveita o padrão de
autorização/auditoria já usado nas demais rotas admin.

## Pontos em aberto (decisão de implementação, não bloqueiam início)

Os dois pontos arquiteturais (escopo do cooldown; pesquisa como
rate-limit, não fila) foram confirmados pelo usuário e já estão
refletidos acima. Restam só decisões de implementação, sem impacto no
comportamento observável:

- mecanismo exato de fila persistida (nova tabela/estado vs. derivar tudo
  de `Mission`/agendamento já existente a cada `run_batch`);
- mecanismo exato de feedback de espera ao usuário (reaproveitar
  polling/estado já existente vs. novo endpoint).

## Validação mínima futura

- só um usuário por vez tem claims processadas quando
  `max_concurrent_user_batches = 1`;
- usuário recém-processado não é escolhido de novo antes do cooldown
  (1–3 min, com jitter real, não fixo) e volta para o fim da fila;
- outro usuário elegível não espera pelo cooldown de um usuário diferente
  (teste explícito de "não monopolização");
- pacing por loja (backoff de `MissionSource`) e limites por provider
  continuam funcionando sem alteração;
- falha ou lentidão do `coupon_worker` nunca interfere na fila de
  usuários do `collection_worker`;
- ADMIN visualiza corretamente fila/usuário atual/aguardando/próximos
  horários.

## Fora de escopo

Implementação de código nesta rodada, qualquer alteração nos limites por
provider já existentes, sistema de planos (TASK-107/`DEC-073`).
