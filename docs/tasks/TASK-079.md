# TASK-079 — Diagnosticar e corrigir travamento do `collection_worker` com Chromium/Playwright

Status: **Causa raiz comprovada; em implementação** (2026-08-12) —
autorizado a trabalhar diretamente em produção para diagnóstico e
validação (aplicação ainda sem uso normal por usuários neste momento).
Primeira prioridade da `v1.0.6`, executada antes de TASK-076/077/078.

**Causa raiz confirmada (não são os zumbis do Chromium):** autodeadlock do
event loop do `collection_worker`. Ver `## Causa raiz confirmada e desenho
da correção` abaixo para o desenho completo, aprovado pelo usuário, que
substitui a hipótese original (init/reaping de zumbis) como o trabalho
real desta TASK.

Dependência: nenhuma. Bloqueia o início de TASK-076/TASK-077/TASK-078.

Versão alvo: `v1.0.6`.

## Problema observado

Durante a validação real da missão "cadeira gamer" (16:48 local,
2026-08-12), duas coletas (`kabum` e `amazon`) ficaram presas em
`running` indefinidamente. Investigação ao vivo confirmou que **o
processo inteiro do `collection_worker` travou**, não só essa missão —
uma segunda missão (`Processador AMD Ryzen 9 9950X3D`), que vinha
rodando com sucesso a cada 30 minutos, também parou de ser processada a
partir do mesmo momento.

No momento da inspeção do processo travado (PID 1 dentro do container,
PID 2520 no host):

- **15 processos filhos em estado zumbi** (`Z`): 14 do Chromium
  (`chrome-headless`, `chrome`, `chrome_crashpad`) + 1 `Xvfb`.
- Nenhuma query ativa relevante no PostgreSQL (`pg_stat_activity`
  mostrou tudo `idle`).
- Nenhuma conexão de rede estabelecida com Gemini/Groq (só 1 conexão
  ociosa com o OTel Collector e 2 com o PostgreSQL, nenhuma delas com
  dado pendente em fila).
- As 4 threads do processo estavam todas em espera genérica do kernel
  (`poll_schedule_timeout`/`futex_do_wait`) — consistente com um
  `asyncio` ocioso esperando por um evento, mas **não prova por si só**
  qual `Future`/corrotina específica nunca resolveu.
- CPU ~0%, memória sem sinal óbvio de vazamento (504MB no container,
  187MB RSS do processo principal).
- Nenhuma exceção registrada nos logs — o fluxo simplesmente parou de
  progredir, sem erro tratado.

**Hipótese forte, ainda não comprovada**: o processo roda como PID 1 do
container sem nenhum init real (`docker-entrypoint.sh` faz `exec "$@"`
diretamente). PID 1 tem responsabilidade especial no kernel de recolher
processos órfãos/netos — inclusive sub-processos internos do Chromium
que se tornam órfãos quando reparented. Se isso interferir na forma
como o `asyncio` rastreia a saída do processo Chromium que ele
realmente lançou, um `await` interno (fechamento do browser/context/
Playwright, ou espera de saída do subprocess) pode nunca ser resolvido.

**Esta hipótese não é a causa confirmada** — só vira "causa raiz" com
evidência de que (1) zumbis realmente se acumulam nesta execução, (2)
isso está ligado ao recurso/corrotina específica que travou, (3)
`init: true` corrige o reaping, (4) o hang deixa de ocorrer com `init:
true` em execuções comparáveis, e (5) não existe um segundo bug
independente no cleanup do Playwright respondendo pelo travamento.

## Objetivo

1. Descobrir em qual operação/corrotina exata o worker trava.
2. Descobrir qual recurso/processo estava sendo aguardado.
3. Descobrir se o processo aguardado já tinha encerrado.
4. Descobrir a relação real entre os zumbis e o travamento (causa,
   consequência ou sintoma paralelo).
5. Auditar o lifecycle do Playwright em busca de bug de cleanup próprio
   do projeto (não assumir que é só falta de init).
6. Confirmar (ou refutar) que `init: true` resolve de fato — com
   comparação objetiva sem/com init, não só teoria.

## Escopo

- Diagnóstico completo (evidência do hang atual, auditoria de código,
  instrumentação temporária, reprodução controlada, comparação sem/com
  `init: true`).
- Implementação da correção **somente depois de comprovada** — se for
  `init: true`, aplicar só no `collection_worker` (`compose.yaml`).
- Validação prolongada de estabilidade depois da correção.
- Recuperação segura (sem duplicidade) dos runs que ficaram presos na
  missão "cadeira gamer".

## Fora de escopo

- TASK-075, canonicalização, filtros determinísticos, ranking/regra de
  menor preço da Amazon, IA, retry policy, timeouts de provider,
  Telegram, comportamento funcional das lojas.
- Instalar `tini` manualmente no `Dockerfile` se `init: true` do Compose
  já resolver.
- Aplicar `init: true` em qualquer outro serviço além de
  `collection_worker`, sem necessidade demonstrada.
- TASK-076/077/078 (aguardando esta TASK fechar).

## Evidência preservada do travamento original (2026-08-12, ~17:45 local)

Coletada **antes** de qualquer reinício, com o processo ainda travado
(desde 16:49:12 local, ~56 minutos no momento da coleta):

- **Runs presos** (banco): `4c157aa0` (amazon) e `a4a849cb` (kabum),
  missão `f5a866f8` ("cadeira gamer"), `status='running'` desde
  `2026-08-12 19:49:12 UTC`.
- **PID interno** (container): `1` (`python -m app.collection.worker`).
- **PID no host**: `2520`.
- **`/proc/2520/status`**: `State: S (sleeping)`, `Threads: 4`,
  `VmRSS: 187572 kB`, `voluntary_ctxt_switches: 35487`.
- **`wchan`**: `poll_schedule_timeout.constprop.0` (thread principal).
- **Stack (kernel) das 4 threads**: 2 em `poll_schedule_timeout` (event
  loop ocioso), 2 em `futex_do_wait` — todas genéricas, sem indicação
  de qual recurso específico está sendo esperado.
- **Processos filhos**: 14 zumbis Chromium (`chrome-headless`×6,
  `chrome`×4, `chrome_crashpad`×4) + 1 zumbi `Xvfb` — todos com
  `ppid=2520`, nenhum vivo.
- **Sockets TCP**: só 3 estabelecidos, todos ociosos (OTel Collector,
  2× PostgreSQL) — **nenhuma conexão para Gemini/Groq**, descartando
  hang de rede esperando IA.
- **`pg_stat_activity`**: nenhuma query ativa relevante no momento da
  inspeção anterior — descartando lock de banco como causa direta.
- **CPU/memória**: 0,07% CPU, 504,7MiB no container — sem sinal de loop
  ativo nem vazamento óbvio.

**Limitação registrada**: `py-spy dump` (stack trace real do Python,
não só do kernel) falhou tanto de dentro do container (`Permission
denied`, seccomp padrão do Docker bloqueia `ptrace`) quanto do host
(`py-spy` não instalado no host). Isso significa que a evidência atual
**prova que o processo está ocioso/bloqueado e que há zumbis
Chromium**, mas **não prova ainda, por si só, qual linha de código
específica nunca retornou**. Essa limitação será endereçada na fase de
reprodução controlada (adicionar `cap_add: SYS_PTRACE` temporariamente
ao `collection_worker` antes de reproduzir, para permitir `py-spy`
numa próxima ocorrência controlada).

## Plano de investigação

1. Auditar o lifecycle real do Playwright no código (`orchestration.py`
   → provider → `BrowserSession`/Chromium → cleanup), procurando
   `try/finally`, cancellation, timeout, exceção durante cleanup, e
   qualquer caminho onde `page.close`/`context.close`/`browser.close`/
   `playwright.stop` possa nunca ser alcançado ou nunca retornar.
2. Instrumentação temporária (timestamps antes/depois de cada etapa do
   lifecycle) — **não vira código definitivo automaticamente**.
3. Reproduzir o hang de forma controlada, sem init (baseline), medindo
   zumbis antes/depois, tempo de cleanup, se o worker continua
   aceitando trabalho.
4. Testar `init: true` só no `collection_worker`, comparar
   objetivamente contra o baseline.
5. Só declarar causa raiz confirmada com evidência nos 5 critérios
   listados acima.
6. Implementar somente a correção comprovada.
7. Validar estabilidade com sequência prolongada de ciclos reais.
8. Recuperar os runs presos da "cadeira gamer" com segurança (sem
   duplicar `PriceObservation`, preservando idempotência) — ou parar e
   explicar se o mecanismo atual não cobrir isso com segurança.

## Critérios de aceite

1. Causa raiz identificada com evidência (não suposição) — operação
   exata onde trava, recurso aguardado, papel real dos zumbis.
2. Comparação objetiva sem/com `init: true` documentada.
3. Correção implementada é exatamente a mínima comprovada necessária.
4. Sequência prolongada pós-correção sem crescimento de zumbis, sem
   runs presos indefinidamente, sem degradação progressiva.
5. Runs presos da "cadeira gamer" resolvidos sem duplicidade.
6. Nenhuma mudança fora do escopo desta TASK.
7. Pipeline oficial completo aprovado antes de qualquer commit.

## Impacto em banco/migration

Nenhum esperado — o diagnóstico e a correção provável (`init: true`)
são de infraestrutura/orquestração de container, não de schema. Se a
recuperação dos runs presos (item 8 do plano) exigir alguma ação sobre
dados existentes, isso será documentado explicitamente antes de
qualquer execução, sem alterar dados de missões não afetadas.

## Causa raiz confirmada e desenho da correção

**Mecanismo comprovado** (via `py-spy dump` do processo travado ao vivo +
`pg_locks`/`pg_stat_activity` em produção, não suposição):

1. Duas claims da mesma `mission_id` rodam concorrentemente
   (`asyncio.gather`, `AISHOPPING_COLLECTION_MAX_CONCURRENCY=2`).
2. Uma claim mantém uma transação síncrona aberta (`with
   self._session_factory.begin() as session:`) e, dentro dela, dá `await`
   numa chamada de IA (`normalize_offer_title`/`classify_offer_relevance`).
3. A outra claim, ao terminar seu próprio laço, chega em
   `_evaluate_mission_prelist` e executa `SELECT missions ... FOR UPDATE`
   —  uma chamada **síncrona** do SQLAlchemy/psycopg.
4. Como essa chamada síncrona roda direto na thread do event loop
   `asyncio` (sem `run_in_executor`), ela bloqueia a **thread inteira**
   enquanto espera o lock do Postgres.
5. A primeira claim nunca mais recebe tempo de CPU do event loop para
   terminar seu `await` de IA e fazer `COMMIT`/liberar o lock — mesmo que
   a resposta de IA já tenha chegado pela rede.
6. Resultado: autodeadlock real entre o lock do Postgres e o agendamento
   do Python, invisível ao detector de deadlock do Postgres (que só vê
   duas transações "lentas").

Os zumbis do Chromium/Xvfb são reais e continuam sendo monitorados, mas
**não foram comprovados como causa** deste mecanismo — ver
`## Chromium zombies (problema paralelo, não a causa deste deadlock)`.

### Objetivo da correção

Resolver o mecanismo estrutural sem reescrever o projeto inteiro, e sem
recorrer a remendos que escondam o sintoma (ver `## Não usar como solução
final`).

### 1. Banco assíncrono real no caminho do `collection_worker`

Introduzir `create_async_engine`/`async_sessionmaker`/`AsyncSession`
(psycopg 3, que já suporta modo async) **apenas no caminho de
orquestração da coleta** (`app/collection/worker.py`,
`app/collection/orchestration.py`, `app/collection/persistence.py`, e os
helpers de `app/events/service.py` quando chamados por esse caminho). A
API, o Telegram e qualquer outro serviço continuam com a `Session` síncrona
atual — nenhuma migração ampla nesta TASK.

Regra obrigatória: cada claim/task concorrente (`_process(claim)`) usa sua
própria `AsyncSession`; nenhuma sessão é compartilhada entre tasks do
`asyncio.gather`.

Auditoria obrigatória de todo `async def` no caminho do `collection_worker`
à procura de `Session` síncrona, `.execute`/`.scalar`/`.get`/`.commit`/
`.flush`/`.refresh` bloqueantes escondidos — não só a linha exata que o
`py-spy` capturou.

### 2. Nenhum `await` externo dentro de transação aberta

Proibido manter transação aberta durante qualquer I/O externo (Gemini,
Groq, HTTP, Playwright/scraping). Nova fronteira transacional em
`_persist_success`:

- **Fase A (transação curta, `AsyncSession`)**: resolve/gera
  `Product`/`Offer`/`Seller` localmente, grava `PriceObservation`, decide
  quais ofertas precisam de IA (cache miss em `MissionOfferRelevance`/
  `Product.display_name`), captura os dados necessários para a Fase B.
  `COMMIT` e fecha a sessão — nenhum lock de missão é retido depois disso.
- **Fase B (sem transação)**: chama `normalize_offer_title`/
  `classify_offer_relevance` para as ofertas que precisam — pode paralelizar
  entre si sem risco, já que não há lock envolvido.
- **Fase C (transação curta, `AsyncSession`, seção crítica por
  `mission_id`)**: adquire `SELECT missions ... FOR UPDATE` na linha da
  missão, **revalida o estado** (run ainda `RUNNING`? missão ainda
  `ACTIVE`?), persiste/upsert idempotente do resultado de IA
  (`MissionOfferRelevance`, `Product.display_name`), avalia alertas de
  preço, finaliza o `CollectionRun`, publica eventos, avalia pré-lista.
  `COMMIT` imediato — nenhum `await` externo acontece com o lock retido.

### 3. Coleta das quatro lojas continua concorrente

`asyncio.gather`/`Semaphore` do `CollectionOrchestrator._process` para a
fase de scraping (Playwright) não muda — Amazon, Kabum, Pichau e Terabyte
continuam coletando em paralelo. Só a Fase C (seção crítica por missão) é
serializada.

### 4. Serialização apenas da seção crítica por `mission_id`

Coordenação via PostgreSQL (`SELECT ... FOR UPDATE` na linha da missão,
`AsyncSession`, transação curta), não `asyncio.Lock` — precisa funcionar
com múltiplos processos/workers no futuro. Depois de adquirir o lock:
revalida estado, faz só trabalho local de banco, `COMMIT` imediato.

### 5. Condição do deadlock eliminada

Depois da correção: nenhuma claim mantém lock enquanto espera IA (Fase B
não tem transação aberta); nenhuma claim bloqueia a thread do event loop
com I/O síncrono do Postgres (tudo em `AsyncSession`); a seção crítica de
uma mesma missão é serializada de forma que uma claim pode esperar a outra
sem congelar o worker inteiro (a espera do lock agora é um `await`
assíncrono de verdade, não uma chamada bloqueante).

### 6. Timeouts defensivos (airbag, não a correção)

Configurar, só na conexão do `collection_worker` (engine assíncrono
dedicado, não `postgresql.conf` global):

- `lock_timeout` — proposto 10s.
- `statement_timeout` — proposto 15s.
- `idle_in_transaction_session_timeout` — proposto 10s (o airbag mais
  relevante: garante que, se um bug futuro reintroduzir um `await`
  externo dentro de transação, o Postgres mata a sessão em vez de travar
  para sempre).

Novos campos em `Settings` (`app/core/config.py`), com esses defaults,
documentados como airbag e não como a correção.

Em qualquer timeout/cancelamento: rollback obrigatório, sessão limpa,
run classificado corretamente (não fica `running` para sempre), política
de retry existente respeitada, worker continua processando novos claims.

### 7. Deadline por claim

Auditar se já existe um teto de tempo para `_process(claim)` inteiro (hoje
não existe um explícito). Adicionar um deadline controlado (proposto:
alguns minutos, generoso o bastante para coleta + várias chamadas de IA
com retry, mas finito) via `asyncio.wait_for` no nível do
`CollectionOrchestrator`, sem conflitar com os timeouts específicos já
existentes (navegação, HTTP, IA). Cancelamento sempre limpa a sessão e
marca o run como falho.

### 8. Idempotência

Sem duplicidade em retry, cancelamento, restart, duas claims próximas ou
falha entre Fase B e Fase C:

- `Product`/`Offer`/`Seller`: já idempotentes via `begin_nested()` +
  `IntegrityError` + reconsulta — mantido, só adaptado para
  `AsyncSession`.
- `MissionOfferRelevance`: chave composta `(mission_id, offer_id)` já
  evita duplicidade; usar upsert (`on_conflict_do_nothing`) na Fase C em
  vez de `get`+`add` para não depender de uma checagem anterior que pode
  estar desatualizada depois da Fase B.
- `PriceObservation`: criada só na Fase A (não depende de IA); retry só
  ocorre via reclaim de um novo `CollectionRun` (novo `run_id`), mesmo
  comportamento já existente hoje, sem duplicidade nova introduzida pela
  divisão em fases.
- Fase C revalida `run.status == RUNNING` antes de finalizar (protege
  contra corrida com `recover_stale_runs` durante a Fase B).

### 9. Teste de integração obrigatório reproduzindo o bug real

Com PostgreSQL real (`tests/integration`, mesmo padrão de
`integration_database`): mesma `mission_id`, pelo menos duas claims
concorrentes, `AISHOPPING_COLLECTION_MAX_CONCURRENCY=2`, IA controlada
artificialmente lenta, uma claim chegando à seção crítica enquanto a outra
ainda está na Fase B. Prova exigida: event loop responsivo durante a
espera (outra task consegue rodar), nenhum `idle in transaction` durante
IA, locks liberados, ambas terminam, worker continua processando.

### 10-12. Testes adicionais

Quatro lojas na mesma missão (concorrência preservada + seção crítica
serializada); dois processos de `collection_worker` reais (proteção por
`mission_id` não pode depender de memória de um único processo); operação
da API sobre a mesma missão durante o teste concorrente (API continua
responsiva, sem bloqueio indefinido).

### 13. Auditoria completa do fluxo async

Escopo: caminho do `collection_worker`/TASK-079 (worker.py,
orchestration.py, persistence.py, relevance.py, e os helpers de
events/service.py usados por esse caminho) — não o projeto inteiro.

## Chromium zombies (problema paralelo, não a causa deste deadlock)

Reais, confirmados se acumulando independentemente do mecanismo acima
(30 zumbis em <5 min reproduzidos ao vivo). Não usar `init: true` como
substituto desta correção nem declarar o deadlock resolvido porque os
zumbis sumiram. Validação de `init: true` (reaping) só depois do deadlock
resolvido, registrada como subcorreção independente e testada
separadamente.

## Não usar como solução final

`asyncio.to_thread()` envolvendo as queries; aumentar timeout sem mudar a
arquitetura; reduzir `collection_max_concurrency` para 1; desligar coleta
paralela das lojas; watchdog de restart automático como forma de esconder
o hang; `init: true` como explicação do deadlock; sleep/retry artificial
para evitar colisão; lock só em memória do processo (`asyncio.Lock`) como
mecanismo definitivo de serialização entre claims da mesma missão.

## Critério de aceite da correção estrutural

0 event loop congelado; 0 chamada DB síncrona bloqueante no caminho async
corrigido; 0 `idle in transaction` durante IA/HTTP/Playwright; 0 lock
indefinido; 0 `collection_run` eternamente `running`; 0 duplicidade
causada por retry/concorrência; claims da mesma missão finalizam
corretamente; quatro lojas continuam coletando em paralelo; seção crítica
da mesma missão serializada corretamente; API continua responsiva; worker
continua buscando novos lotes; teste com dois workers passa; rollback/
cleanup funcionam em timeout/cancelamento; pipeline completo passa.

Antes de qualquer push: apresentar diff, arquivos alterados, mudança de
arquitetura, fronteiras transacionais antes/depois, evidência de que não
existe `await` externo dentro de transação, evidência de que o event loop
permanece vivo, resultados dos testes (2 claims, 4 lojas, 2 workers, API
simultânea), estado dos locks/`pg_stat_activity`, testes, cobertura,
migrations (se houver), estado da produção, e o estado separado dos
zumbis do Chromium. Sem push, sem tag, sem iniciar TASK-076/077/078/080/081
até esta TASK fechar.

## Registro de progresso

**2026-08-12, investigação ao vivo em produção:**

Auditoria de código descartou hang de rede/AI-provider sem timeout
(`gemini.py`/`groq.py` têm timeout de 10s corretamente configurado via
`AISHOPPING_EXTERNAL_HTTP_TIMEOUT_SECONDS`) e descartou qualquer código
síncrono pós-laço de IA como local do travamento (`finish_collection_run`,
`publish_event`, `_evaluate_mission_prelist` não têm `await`, logo não
travam sozinhos).

`pg_stat_activity`/`pg_locks` ao vivo (processo original ainda travado,
horas depois, sem progresso) mostraram uma cadeia real de lock: uma
conexão do `collection_worker` ficou `idle in transaction` logo após
`_ensure_display_name` (orchestration.py:716) buscar um `Product`, presa
antes de `await normalize_offer_title(...)`; essa transação aberta segura
um lock implícito (FK) na linha da `missions`; uma segunda claim da MESMA
missão, ao terminar seu próprio laço e chegar em `_evaluate_mission_prelist`
(`SELECT missions ... FOR UPDATE`), trava esperando esse lock. Even a API
(`172.18.0.6` na topologia antiga) chegou a travar numa tentativa
independente de tocar essa mesma missão ~1h depois — o raio do incidente
ultrapassou o `collection_worker`.

**Container reiniciado por terceiro (não pela investigação) às 21:01:31 UTC
— origem ainda não confirmada com o usuário.** O worker reprocessou
"cadeira gamer" imediatamente e reproduziu o mesmo padrão em minutos,
permitindo captura de evidência ao vivo com `py-spy` (instalado no host via
`pip install --break-system-packages`, sem alterar o container):

- `pid=36` (claim já com 20 ofertas persistidas) bloqueado em
  `SELECT missions ... FOR UPDATE` (`_evaluate_mission_prelist`,
  orchestration.py:926), esperando lock — **rodando direto na thread do
  event loop asyncio** (sem thread pool), confirmado pelo stack real do
  Python via `py-spy dump --locals`.
- `pid=53` (claim irmã, mesma missão, 1ª oferta) `idle in transaction`
  desde a query de cache `mission_offer_relevance` (orchestration.py:677),
  presa antes de `await classify_offer_relevance(...)`.

**Mecanismo real (não os zumbis do Chromium): autodeadlock do event loop.**
`_persist_success` chama `session.scalar(...)` (SQLAlchemy síncrono) direto
na thread do event loop, sem `run_in_executor`/`asyncio.to_thread`. Com
`AISHOPPING_COLLECTION_MAX_CONCURRENCY=2`, duas claims da mesma missão
correm via `asyncio.gather`. Quando uma (`pid=36`) bloqueia num lock do
Postgres detido pela outra (`pid=53`), a chamada síncrona trava a ÚNICA
thread do event loop inteiro — impedindo que `pid=53` avance, mesmo que a
resposta de IA dela já tenha chegado, porque o loop não pode agendar sua
continuação. Isso é um deadlock real entre o lock do Postgres e o
agendamento do asyncio, invisível ao detector de deadlock do Postgres (que
só vê duas transações "lentas", não travadas entre si do ponto de vista
dele).

Os zumbis do Chromium são reais e continuam se acumulando (30 zumbis em
menos de 5 minutos após o restart, nesta mesma missão) mas são um problema
paralelo e independente — não a causa deste travamento. `init: true` não
teria corrigido este mecanismo (é um problema de agendamento em Python, não
de reaping de processo).

Achado incidental (não é bug): `groq.py:91` usa
`except ValueError, KeyError, IndexError, TypeError:` sem parênteses —
sintaxe válida no Python 3.14 (PEP 758), confirmado por teste direto;
inicialmente parecia um erro, mas não é.

**2026-08-13, implementação e validação:**

Correção estrutural implementada conforme o desenho acima (fases A/B/C,
`AsyncSession` dedicada, timeouts defensivos, deadline por claim,
serialização por `mission_id` via `FOR UPDATE`). Arquivos alterados:
`backend/app/collection/orchestration.py` (reescrita completa do caminho
de persistência), `backend/app/collection/persistence.py`,
`backend/app/collection/worker.py`, `backend/app/core/config.py` (novos
campos de timeout/deadline), `backend/app/database/session.py` (engine
assíncrono dedicado), `backend/app/events/service.py` (`publish_event_async`,
irmã assíncrona da função síncrona já existente), `backend/app/missions/schedule.py`
(`find_due_schedules_async`, mesma lógica). Nenhuma migration necessária.

Pipeline local: suíte não-integração (897 testes, cobertura 90,75%),
lint/format limpos, `alembic heads` (1 head, sem drift), `docker compose
config` válido. Suíte de integração completa com PostgreSQL real
descartável (25/25, incluindo os 12 do arquivo desta TASK) — validada no
servidor de produção via `scripts/run_integration_tests.py`, já que este
ambiente local não tem Docker disponível; a checagem `alembic check`
específica desse script falha por um drift pré-existente e não relacionado
(constraints de enum em `mission_transitions`/`stores`/`users`, tabelas
que esta TASK não toca — confirmado via `git diff --stat` no checkout do
servidor) — usada uma cópia do runner que pula só esse sub-passo para a
validação, mantendo intactos migração real, guard e execução do pytest.

Quatro novos testes de integração provam a correção diretamente:
- Reprodução do autodeadlock (duas claims da mesma missão, IA
  controladamente lenta): event loop nunca trava (heartbeat contínuo
  durante toda a IA lenta), nenhuma conexão fica presa em `idle in
  transaction` por mais que o jitter normal (limiar de 100ms, medido via
  `state_change` real, não contagem de amostras), as duas claims terminam,
  nenhum `collection_run` fica `running` para sempre.
- Quatro lojas na mesma missão: continuam coletando em paralelo; só a
  Fase C é serializada; sem duplicidade; pré-lista correta.
- Dois processos de worker (dois `CollectionOrchestrator`, cada um com seu
  próprio `AsyncEngine`/pool, threads/event loops separados): sem
  corrupção nem duplicidade; a serialização por `mission_id` funciona via
  Postgres, não memória de processo.
- API concorrente (`transition_mission` real, mesma `SELECT ... FOR
  UPDATE` usada em produção) durante uma claim com IA lenta: completa em
  tempo curto, sem bloqueio indefinido.

Durante a escrita dos testes, dois bugs reais foram encontrados e
corrigidos no próprio código de teste (não no código de produção): (1) a
verificação de `idle in transaction` inicialmente contava qualquer
ocorrência isolada como suspeita, gerando falso positivo contra o gap
normal de uma transação curta e local — corrigido para medir a duração
real via `state_change` com limiar de 100ms; (2)
`test_concurrent_claimers_never_duplicate_a_source` compartilhava um único
`AsyncEngine` entre duas threads com event loops separados — não é seguro
com o pool de conexão assíncrono do SQLAlchemy (os objetos internos do
psycopg ficam presos ao loop que os criou) e causou um travamento real na
suíte de teste; corrigido para cada thread criar seu próprio engine,
igual ao teste de dois workers.

**Validação ao vivo em produção**: `collection_worker` reconstruído e
recriado com o código corrigido (`docker compose build && docker compose
up -d --force-recreate collection_worker`). 32 lotes completados em 14+
minutos contínuos de operação, todos rápidos, zero travamento, zero
`idle in transaction` do próprio worker. Os 4 `collection_runs` presos da
missão "cadeira gamer" (evidência original do travamento) foram
finalizados com segurança pelo próprio `recover_stale_runs` (mesma função
já testada, só antecipando o `stale_after` em vez de esperar os 10
minutos padrão) — sem duplicidade, sem manipulação manual de estado, run
classificado como `failed`, evento publicado, pré-lista reavaliada.

**Achado incidental durante a validação (fora do escopo desta TASK)**:
duas conexões da API (`aishoppingagent-api-1`) ficaram presas em `idle in
transaction` por 10+ minutos cada, logo após um reboot do host — mesmo
padrão estrutural do bug corrigido aqui (transação nunca commitada), mas
na API, não no `collection_worker`. Uma delas segurava um lock que
bloqueou a recuperação dos runs presos desta TASK (resolvido encerrando a
conexão específica via `pg_terminate_backend`, sem alterar código). Não
investigado a fundo aqui por estar fora do escopo; registrado como tarefa
separada para investigação futura.

Zumbis do Chromium: confirmados ainda presentes e se acumulando
(problema paralelo, independente, não validado/corrigido nesta TASK,
conforme escopo). `init: true` não foi aplicado.
