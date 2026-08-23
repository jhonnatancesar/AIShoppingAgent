# TASK-108 — Fila justa e controle de carga

Status: **Formalizada (planejamento); aguardando aprovação. Nenhum código escrito.**

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

## Configuração proposta

Mesmo padrão de `Settings` já usado (`Field(default=..., ge=..., le=...)`,
env `AISHOPPING_`):

```python
max_concurrent_user_batches: int = Field(default=1, ge=1, le=4)
user_cooldown_min_seconds: float = Field(default=600.0, gt=0, le=3600)
user_cooldown_max_seconds: float = Field(default=900.0, gt=0, le=3600)
```

(`user_cooldown_min/max` = 10–15 min do pedido original, em segundos para
consistência com as demais configs de tempo já existentes.)

## Arquitetura proposta

- **Fila FIFO/round-robin por `user_id`** — não por missão nem por claim
  individual. Proposta: manter/derivar a ordem a partir do
  agendamento já existente (`find_due_schedules_async`), agrupando as
  missões due por `user_id` em vez de tratá-las soltas.
- **`max_concurrent_user_batches = 1`**: só as claims de **um** usuário
  são processadas por vez (mesmo semáforo/gather de hoje, mas escopado ao
  lote do usuário da vez, não a todas as claims due do sistema).
- **Cooldown é por usuário, não global**: ao terminar o lote de um
  usuário, esse usuário específico só volta a ficar elegível depois de um
  jitter aleatório entre `user_cooldown_min` e `user_cooldown_max`
  (10–15 min) — evita sincronização em massa (thundering herd) se vários
  usuários tiverem o mesmo intervalo de agendamento. **Outros usuários não
  esperam esse cooldown** — o sistema passa para o próximo usuário
  elegível da fila imediatamente. Interpretação assumida para "usuário não
  monopoliza"; se a intenção for outra (cooldown também pausando a fila
  inteira), precisa de confirmação antes de implementar.
- **`coupon_worker` (TASK-106) fica inteiramente fora dessa fila** — já é
  processo/loop separado por decisão da `DEC-093`; "baixa prioridade" aqui
  significa nunca competir pelo mesmo semáforo/slot de concorrência do
  `collection_worker`, não uma prioridade dentro do mesmo scheduler.
- **Limites por provider permanecem intocados** — circuit breaker por
  `source_code`, backoff de `MissionSource`, timeouts por loja: nada disso
  muda.

## Pesquisa pela Web (rate limit, não fila de processamento)

A pesquisa (`search_router.py`) é síncrona e read-only sobre dados já
persistidos — não compete pelo mesmo recurso que a coleta em background
(Playwright/Edge-CDP) e não precisa de fila de processamento própria.
"Fila/rate limit controlado" aqui é entendido como: aplicar o mesmo
`max_daily_searches` da TASK-107 nesse endpoint, evitando que um usuário
sozinho gere volume desproporcional de leitura. Se a intenção original for
outra (ex.: enfileirar de fato as requisições de busca, não só limitar por
dia), precisa de confirmação — a leitura atual do pedido não indica isso
claramente.

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

## Pontos em aberto (não implementar sem decidir)

- Confirmar a interpretação do cooldown (por usuário, não bloqueando a
  fila inteira) — ver seção "Arquitetura proposta" acima;
- confirmar se "fila" de pesquisa web é rate-limit (interpretação atual)
  ou enfileiramento real de requisições;
- mecanismo exato de fila persistida (nova tabela/estado vs. derivar tudo
  de `Mission`/agendamento já existente a cada `run_batch`);
- mecanismo exato de feedback de espera ao usuário (reaproveitar
  polling/estado já existente vs. novo endpoint).

## Validação mínima futura

- só um usuário por vez tem claims processadas quando
  `max_concurrent_user_batches = 1`;
- usuário recém-processado não é escolhido de novo antes do cooldown
  (10–15 min, com jitter real, não fixo);
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
