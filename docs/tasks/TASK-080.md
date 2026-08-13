# TASK-080 — Transação síncrona aberta durante envio Telegram no `telegram_notifier`

Status: **CONCLUÍDA E VALIDADA EM RUNTIME (2026-08-13)** — opção A
aprovada pelo usuário (solução estrutural, mesmo padrão Fase A/B/C da
TASK-079). Pipeline local completo (942 testes não-integração, 90,65%
cobertura, ruff limpo), suíte de integração PostgreSQL real dedicada
(2/2), regressão da suíte de integração completa (mesmas 6 falhas
pré-existentes e não relacionadas, confirmadas idênticas contra o código
anterior via `git stash`) **e validação real em produção** (imagem
recriada via `docker compose build`/`up -d --no-deps`, `api`/
`collection_worker`/`telegram_notifier` `healthy`, evento real de login
do Telegram processado pelo `telegram_notifier` com `claimed=1
succeeded=1`, zero `idle in transaction` observado durante o envio) —
ver "Registro de implementação" e "Validação em runtime" no final deste
documento. Sem push.

Dependência: nenhuma. Relacionada à TASK-079 (mesma classe estrutural de
problema — transação de banco atravessando `await` de I/O externo), mas
**não é uma reabertura da TASK-079**: o mecanismo de autodeadlock real
comprovado na TASK-079 (bloqueio síncrono de lock na thread do event
loop, com duas claims concorrentes) não se reproduz aqui pelos motivos
descritos em "Por que não é o mesmo autodeadlock".

Versão alvo: a definir pelo usuário (candidata a `v1.0.6` ou adiada).

## Problema

Auditoria sistemática (grep de `Session`/`AsyncSession`/`FOR UPDATE`/
`await` em todo `backend/app`, disparada pela revisão da TASK-079)
encontrou que `telegram_notifier` — o terceiro serviço com histórico
dessa classe de bug (os outros dois, `collection_worker` e o webhook da
`api`, já foram corrigidos pela TASK-079 e sua extensão) — ainda usa o
padrão antigo: transação síncrona (`Session`) aberta durante `await` de
I/O externo (envio Telegram).

`backend/app/telegram/worker.py::_process_batch` (linhas 145-164):

```python
async def _process_batch(session_factory, *, settings, limit, processor):
    with session_factory.begin() as session:      # Session síncrona
        return await processor(session, ...)       # await por dentro
```

`processor` é `process_telegram_notifications`/
`process_telegram_authentication_notifications`/
`process_telegram_prelist_notifications`
(`backend/app/telegram/notifications.py`), que:

1. Reivindica eventos com `claim_unconsumed_events` — `SELECT ... FOR
   UPDATE SKIP LOCKED` (`backend/app/events/consumption.py:79`),
   síncrono, executado direto na thread do event loop.
2. Para cada evento, dentro do `for`, dá `await send_message(...)`
   (`backend/app/telegram/bot_api.py`) — chamada HTTP real à API do
   Telegram — **com a mesma transação/sessão ainda aberta**.
3. Registra o resultado (`record_consumption_attempt`) e só sai do
   `with session_factory.begin()` (commit) depois de todos os eventos do
   lote serem processados.

O docstring de `process_telegram_notifications` já documenta essa
escolha como deliberada: *"Entrega um lote mantendo locks e tentativas
na transação do chamador."*

## Por que não é o mesmo autodeadlock da TASK-079

Verificado por leitura de código, não suposição:

1. **Sem concorrência interna.** `run_worker` chama os três processadores
   **sequencialmente** (não há `asyncio.gather`); dentro de cada um, os
   eventos do lote também são processados um a um, em `for` sequencial.
   Não existe uma segunda claim disputando o mesmo recurso dentro do
   mesmo processo — condição necessária para o mecanismo real da
   TASK-079 (uma claim bloqueando a thread inteira do event loop
   enquanto outra claim, no mesmo processo, nunca recebe tempo de CPU).
2. **A query de reivindicação usa `SKIP LOCKED`.** `with_for_update(skip_locked=True, of=Event)`
   nunca fica esperando um lock já detido por outra transação — se a
   linha já está travada, a query simplesmente a ignora e segue para a
   próxima. Isso elimina especificamente o mecanismo "chamada síncrona
   bloqueada esperando lock alheio" que foi a causa raiz comprovada na
   TASK-079.

Ou seja: **não há evidência de que este código trave o event loop hoje**
— a auditoria não encontrou reprodução nem indício em produção
(`docker compose logs` e `pg_stat_activity` durante a validação da
rodada anterior não mostraram `idle in transaction` neste serviço).
Nenhuma correção deve ser inventada para um defeito não comprovado.

## Riscos residuais reais (evidenciados, não hipotéticos)

1. **Nenhum airbag de banco nesta conexão.** `create_database_engine`/
   `create_session_factory` (`backend/app/database/session.py:38-62`),
   usados pelo `telegram_notifier`, não aplicam `lock_timeout`/
   `statement_timeout`/`idle_in_transaction_session_timeout` — esses
   três só existem nos engines assíncronos dedicados criados pela
   TASK-079 (`create_collection_async_database_engine`,
   `create_telegram_async_database_engine`). Se `send_message` travar
   além do esperado por um motivo não previsto (bug de biblioteca,
   partição de rede que escape ao timeout do `httpx`), a transação pode
   ficar `idle in transaction` **sem nenhum limite automático**,
   segurando o lock da linha do evento reivindicado indefinidamente.
2. **Padrão arquiteturalmente inconsistente.** O restante do projeto,
   depois da TASK-079, estabeleceu a regra "nenhuma transação aberta
   durante I/O externo" como o desenho correto (Fase A/B/C). Este
   caminho é a única exceção sobrevivente a essa regra.
3. **Frágil a mudanças futuras.** Qualquer alteração futura que
   paralelize este worker (ex.: processar eventos do lote com
   `asyncio.gather` para reduzir latência, ou lotes maiores) reintroduz
   exatamente o mecanismo de autodeadlock já corrigido em outro lugar,
   porque o padrão-base (sessão síncrona + `await` por dentro) continua
   presente.
4. **Retenção de lock mais longa que o necessário.** Mesmo sem deadlock,
   a linha do `Event` (e a leitura implícita de `User`/`Mission`/`Offer`
   feita por `_prepare_notification` dentro da mesma transação) fica
   travada pelo tempo inteiro do envio HTTP (até `external_http_timeout_seconds`,
   hoje 10s, por tentativa) em vez de só o tempo de banco.

## Testes existentes vs. ausentes

- **Existentes** (`tests/test_telegram_worker.py`, 3 testes;
  `tests/test_telegram_notifications.py`): cobrem sucesso/commit de um
  lote, rollback fora da transação com falha, e a lógica de
  entrega/retry/dead-letter em si — não a duração/comportamento do lock
  nem a responsividade do event loop durante o envio.
- **Ausentes**: nenhum teste equivalente aos criados pela TASK-079 para
  `collection_worker`/webhook (heartbeat do event loop durante I/O
  externo lento, ausência de `idle in transaction` medida via
  `pg_stat_activity` real, comportamento sob `send_message` lento).

## Opções de tratamento (para decisão do usuário, nenhuma escolhida ainda)

**A. Aplicar o mesmo padrão Fase A/B/C da TASK-079** — migrar
`telegram_notifier` para `AsyncSession` dedicada, separar reivindicação
(Fase A, curta) de envio (Fase B, sem transação) de registro do
resultado (Fase C, curta). Mais consistente, maior escopo de mudança.

**B. Só adicionar o airbag ausente** — aplicar
`lock_timeout`/`statement_timeout`/`idle_in_transaction_session_timeout`
à conexão síncrona deste worker (reaproveitando `_connect_options`, já
genérico), sem mudar a arquitetura de transação. Resolve o risco 1 sem
tocar no restante; não resolve os riscos 2-4.

**C. Aceitar o risco documentado e não implementar nada agora** — válido
se o usuário concluir que a ausência de concorrência interna e o
`SKIP LOCKED` já são proteção suficiente para o volume atual, adiando
para quando (se algum dia) o worker precisar de paralelismo.

Esta TASK **não escolhe** entre as três — registra o achado e as opções
para aprovação explícita antes de qualquer implementação, conforme
instrução do usuário para esta rodada.

## Fora de escopo

- Reabrir ou modificar a correção já validada da TASK-079
  (`collection_worker`, webhook Telegram).
- Qualquer mudança na regra de negócio de notificações (preferências,
  tipos de evento, formatação de mensagem).
- Zumbis do Chromium/Playwright (TASK-079, seção própria — problema
  paralelo, não relacionado).

## Critérios de aceite (quando a opção escolhida for implementada)

1. Causa/risco documentado nesta TASK confirmado ou refutado com
   evidência antes de qualquer código, se a opção escolhida for A.
2. Nenhuma mudança de comportamento funcional de entrega/retry/
   dead-letter observável pelos testes existentes.
3. Se opção A: teste de integração com PostgreSQL real provando ausência
   de `idle in transaction` durante um envio Telegram artificialmente
   lento, nos mesmos moldes dos testes já escritos para a TASK-079.
4. Se opção B: teste confirmando que a conexão do `telegram_notifier`
   aplica os três timeouts, e que uma transação anormalmente longa é
   encerrada pelo Postgres em vez de ficar presa.
5. Pipeline oficial completo aprovado antes de qualquer commit.

## Impacto em banco/migration

Nenhum esperado em qualquer uma das três opções — mudança de
infraestrutura de conexão/transação, não de schema.

## Registro de implementação (2026-08-13)

**Opção A implementada** conforme decisão do usuário. Arquivos alterados:

- `backend/app/events/consumption.py`: siblings assíncronos
  (`claim_unconsumed_events_async`, `record_consumption_attempt_async`,
  `count_failed_attempts_async`), mesmo padrão de `publish_event`/
  `publish_event_async` (TASK-079) — lógica de consulta/validação
  extraída para helpers privados sem I/O, compartilhados entre a versão
  síncrona (ainda usada por outros chamadores) e a assíncrona.
- `backend/app/authentication/notifications.py`: sibling assíncrono
  `publish_due_authentication_notifications_async`.
- `backend/app/telegram/notifications.py`: `process_telegram_notifications`
  e variantes (`_authentication`/`_prelist`) reestruturadas em três fases
  — `_claim_and_prepare` (Fase A, `AsyncSession`, transação única e curta:
  reivindica + prepara texto/destinatário; eventos que falham a
  preparação já são resolvidos aqui, sem I/O externo) → envio via
  `send_message` por item preparado (Fase B, sem transação) →
  `_send_and_record` (Fase C, uma transação curta **por evento**, nunca
  em lote). Toda a cadeia de helpers privados de renderização
  (`_prepare_notification_async`, `_resolve_offer_context_async`,
  `_prepare_prelist_notification_async`, `_prepare_authentication_
  notification_async`, `_render_prelist_ready_async`, `_render_prelist_
  block_async`, `_load_offer_context_async`) convertida para
  `AsyncSession`.
- `backend/app/telegram/worker.py`: troca para
  `create_telegram_async_database_engine`/`create_async_session_factory`
  (reaproveita o engine já criado pela TASK-079 para o webhook, mesmos
  timeouts defensivos já existentes em `Settings` — nenhum campo novo de
  configuração). `_process_batch` simplificado: delega direto ao
  `processor`, que já gerencia suas próprias transações por fase.

**Requisitos obrigatórios do usuário, confirmados um a um:**

1. **Fase A materializa tudo antes do `COMMIT`** — `_PreparedNotification`
   (dataclass congelado com `event_id: UUID`, `chat_id: int`, `text: str`)
   carrega só tipos primitivos entre as fases; nenhum objeto ORM
   atravessa a fronteira Fase A → Fase B. A Fase C **refaz** a consulta
   do `Event` (`session.get(Event, prepared.event_id)`) na sua própria
   transação nova — nunca reaproveita o objeto ORM de uma sessão já
   fechada.
2. **Semântica de entrega/idempotência preservada**: sucesso só é
   registrado depois de `send_message` retornar sem exceção (Fase C
   roda só depois da Fase B terminar); falha de envio continua no mesmo
   fluxo de retry/dead-letter (`_decide_and_record_outcome`,
   compartilhado entre falha de preparação e falha de envio, para as
   duas nunca divergirem de critério); evento nunca é reconhecido como
   consumido antes da entrega (a Fase A não grava `SUCCEEDED` para
   nenhum item que ainda vai ser enviado). **Janela de crash
   documentada explicitamente no docstring de `process_telegram_
   notifications`**: se o processo morrer entre `send_message` retornar
   sucesso e o `COMMIT` da Fase C daquele evento, a mensagem já foi
   entregue mas o consumo não fica registrado — o próximo lote reivindica
   o mesmo evento e reenvia (semântica "ao menos uma vez", idêntica em
   espírito ao código anterior; a diferença real é que agora essa janela
   é por evento, não mais por lote inteiro — um crash não desfaz mais o
   registro de eventos já commitados antes dele).
3. **Múltiplas réplicas não implementado** — registrado explicitamente
   como premissa (processamento sequencial, uma única réplica no
   `compose.yaml` atual) e risco futuro, sem advisory lock, sem migration
   nem arquitetura nova adicionada.
4. **Teste PostgreSQL real dedicado**
   (`tests/integration/test_telegram_notifications.py`, novo arquivo):
   - `test_slow_send_message_never_holds_a_transaction_open_and_event_loop_stays_responsive`:
     `send_message` artificialmente lento (`asyncio.sleep(0.3)`),
     heartbeat de 10ms monitorado durante o envio, `pg_stat_activity`
     amostrado ao vivo a cada 20ms numa thread separada (mesmo padrão
     `_poll_idle_in_transaction` da TASK-079). Prova obtida: heartbeat
     nunca para, nenhuma conexão do `telegram_notifier` aparece `idle in
     transaction` por mais que o jitter normal, e a Fase C registra
     `SUCCEEDED` corretamente depois do envio liberar.
   - `test_batch_of_three_events_second_success_persists_despite_third_failure`:
     3 eventos reais, 2 sucessos + 1 falha (`TelegramBotAPIError(500)`).
     Prova obtida: os registros dos eventos 1 e 2 ficam `SUCCEEDED` no
     banco independentemente do resultado do evento 3; o evento 3 recebe
     `FAILED` com `next_retry_at` preenchido (nunca dead-letter imediato,
     política de retry existente inalterada).
   - Achado incidental do ambiente (não um bug de código): `psycopg` em
     modo assíncrono não suporta o `ProactorEventLoop` padrão do asyncio
     no Windows — é a primeira vez que testes de integração assíncronos
     rodam localmente neste Windows (antes só validados no servidor
     Ubuntu, TASK-079). Corrigido só no arquivo de teste novo
     (`asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`,
     guardado por `sys.platform == "win32"`), sem tocar `conftest.py` nem
     nenhum outro teste.

**Resultados do pipeline:**

- Suíte não-integração: 942 testes, 90,65% cobertura (mínimo 90%), ruff
  check e format limpos.
- Suíte de integração dedicada desta TASK: 2/2 aprovados.
- Suíte de integração completa: 21/27 aprovados; as mesmas 6 falhas
  (`test_mission_edit.py` × 5, `test_existing_domain_integrations.py` × 1)
  **confirmadas pré-existentes e não relacionadas** — reproduzidas
  também contra o código anterior à TASK-080 via `git stash`/`git stash
  pop` (mesmo resultado exato, `edit_mission_criteria` chamado sem
  `await` — débito não migrado pela TASK-079 original, fora do escopo
  desta TASK).
- A checagem `alembic check` do runner de integração continua com o
  mesmo drift pré-existente já documentado na TASK-079 (constraints de
  enum em `mission_transitions`/`stores`/`users`) — validado com a mesma
  cópia do runner que pula só esse sub-passo, mantendo migração real,
  guard e execução do pytest intactos.

**Riscos residuais conhecidos, não resolvidos por esta TASK:**

- Múltiplas réplicas do `telegram_notifier` (item 3 acima) exigiriam
  exclusão adicional (ex.: advisory lock por evento) antes de serem
  habilitadas com segurança.
- Janela de crash "ao menos uma vez" entre envio e registro (item 2
  acima) — aceita como comportamento existente, não uma regressão.
- As 6 falhas de integração pré-existentes (`edit_mission_criteria`)
  continuam sem correção — fora do escopo desta TASK, candidatas a TASK
  própria se o usuário quiser tratá-las.

## Validação em runtime (2026-08-13, servidor real)

Feita depois da aprovação técnica da implementação/testes, no ambiente
Docker real deste servidor (Windows Server 2025 + Docker Desktop),
seguindo exatamente o `compose.yaml` existente — sem migration, sem
alterar secrets, sem push.

1. **Rebuild**: `docker compose build` (imagem `aishoppingagent-app:local`
   compartilhada por `api`/`collection_worker`/`telegram_notifier`).
2. **Recriação seletiva**: `docker compose up -d --no-deps api
   collection_worker telegram_notifier` — `database`, `jaeger`,
   `otel-collector`, `prometheus` não tocados (uptime preservado).
3. **Estado pós-recriação**: os três `healthy` em ~15s,
   `RestartCount=0` nos três, `docker compose logs` sem `error`/
   `exception`/`critical`/`traceback` em nenhum dos três.
4. **Teste real controlado, especificamente do `telegram_notifier`**
   (não o webhook): usuário real enviou uma ação no Telegram que
   completou login (`/entrar`), gerando `authentication.completed.v1`
   (`event_id=a78dc0df-80c3-403a-b140-f18f0b5426ee`, `recorded_at
   22:58:25.440`). O webhook (`POST /telegram/webhook`, síncrono) só
   processa e responde a mensagem — quem efetivamente **envia** a
   confirmação "Login realizado com sucesso" é o `telegram_notifier`,
   de forma assíncrona, no próximo ciclo de poll — caminho exato
   alterado por esta TASK.
5. **`pg_stat_activity` monitorado ao vivo** durante a janela de
   processamento (8 amostras, 1s de intervalo, cobrindo o poll e o
   envio real): **nenhuma linha em `idle in transaction`** em nenhuma
   amostra.
6. **Confirmação de registro/consumo**: `event_consumption_attempts`
   mostra o evento como `outcome=succeeded`, `attempted_at
   22:58:28.849` (~3,4s depois do evento ser gravado — dentro do ciclo
   de poll padrão). Log do próprio `telegram_notifier` confirma o
   mesmo lote: `{"notification_claimed":1,"notification_succeeded":1,
   "notification_failed":0,"notification_dead_lettered":0}`.

**Conclusão**: comportamento em produção real consistente com a prova
de integração controlada (item 4 do "Registro de implementação") — sem
transação sustentada durante o envio, evento corretamente registrado
como consumido só depois da entrega confirmada.
