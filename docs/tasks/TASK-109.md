# TASK-109 — Migrar o collection_worker para Windows nativo com Edge

Status: **FASE 1 concluída e aprovada no DEV (Ops Agent + supervisão do
worker via Task Scheduler). FASE 2 concluída no DEV (worker nativo com
Edge, ciclo real de coleta da Magalu provado ponta a ponta, incluindo
recovery). FASE 3 concluída no DEV: 6 lojas + enriquecimento de detalhe
+ `StoreProductIdentityResolver` (TASK-083) via Edge/CDP; lifecycle sob
demanda do Edge (lease/idle-timeout). FASE 4 (fechamento da migração)
concluída no DEV: `BrowserSession`/Chromium removidos de todo caminho
real de coleta (nenhum provider abre Chromium gerenciado, nunca mais
como fallback -- falha explícita, `EdgeCdpTransportError`);
`BrowserSession` permanece só como infraestrutura de teste hermética
(HTML parsing local, sem rede); `collection_worker` removido do
`compose.yaml`; Dockerfile parou de instalar Chromium/Xvfb;
`ops_controller` integrado ao Windows Ops Agent
(`WindowsOpsAgentAdapter`). Documentação nova/atualizada:
`docs/architecture/windows-collection-worker.md`,
`docs/architecture/playwright.md`,
`docs/architecture/service-operations.md`. Nenhum deploy feito --
produção continua nos sete serviços Docker.**

## Objetivo

Tirar o `collection_worker` do container Docker/Linux (Chromium via
Playwright) e rodá-lo como processo nativo do Windows, controlando um
Microsoft Edge real instalado na máquina — mesmo padrão já validado para
Magalu (TASK-104A) e Terabyte (TASK-105), agora generalizado para todas
as lojas que dependem de navegador. API, PostgreSQL, Telegram notifier e
os demais serviços de observabilidade continuam em Docker.

## Preflight — estado real levantado

### Deploy atual (`compose.yaml`, `Dockerfile`)

- Uma única imagem (`aishoppingagent-app:local`) serve `api`,
  `telegram_notifier`, `ops_controller` e `collection_worker` — o
  Dockerfile instala Chromium via Playwright
  (`python -m playwright install --with-deps chromium`) mesmo só o
  `collection_worker` usando.
- `collection_worker` roda `python -m app.collection.worker`, com
  `init: true` (TASK-081 — colhe processos zumbis do Chromium via
  `tini`), expõe métricas Prometheus em `:9464` (`/metrics`), e recebe só
  os secrets que precisa (`postgres_password`, `gemini_api_key_admin_dev`,
  `groq_api_key` — nunca o token do Telegram nem a chave Gemini USER).
- `database` (Postgres) já expõe `127.0.0.1:5432` por padrão
  (`POSTGRES_BIND_ADDRESS`); `api` expõe `127.0.0.1:8000`
  (`API_BIND_ADDRESS`) — ambos alcançáveis fora do Docker desde que o
  worker Windows rode na mesma máquina (a confirmar na prática se o
  Docker Desktop/WSL2 do servidor faz esse port-forward para o host
  Windows; é a suposição de trabalho deste documento).
- `ops_controller` roda **dentro** do Docker, numa rede interna
  (`docker_ops`, `internal: true`) e só fala com `docker-socket-proxy`
  (`DOCKER_HOST`) — não alcança processos do host Windows hoje.

### Abstração operacional já existente (favorável à migração)

- `app/admin/service_ops.py` já é runtime-neutro:
  `ServiceOps`/`ManagedService`/`ServiceState` são um contrato puro;
  `ControllerServiceOps` (usado pelo `admin_router.py`) só fala HTTP
  assinado (HMAC) com o `ops_controller` — o painel ADMIN **nunca** sabe
  que hoje é Docker por trás.
- `app/ops_controller.py` já isola Docker num único adapter
  (`DockerOpsAdapter`, docstring: "Único ponto que conhece Docker") --
  usa `docker-socket-proxy` para inspecionar/iniciar/reiniciar
  containers pelo label `com.docker.compose.service=<nome>`. Hoje só
  existe esse adapter, sem dispatch por serviço.
- **Consequência prática:** a mudança pro requisito 9 é *local* a
  `ops_controller.py` (adicionar um segundo adapter e despachar por
  `LogicalService`) — o painel ADMIN e `ControllerServiceOps` não
  precisam mudar nada.

### Transporte Edge/CDP já validado (reaproveitável)

- `app/collection/providers/magalu_edge_supervisor.py`
  (`MagaluEdgeSupervisor`) já implementa exatamente o padrão pedido:
  inicia um Edge dedicado com perfil próprio e CDP em loopback,
  `_monitor()`/`_ensure_running()` detectam e recuperam automaticamente
  se o processo morre, `wait_until_ready()` aguarda o CDP responder.
  Validado ao vivo (TASK-104A): matou 8 processos do perfil dedicado, o
  supervisor recuperou sozinho com novo PID.
- `app/collection/providers/cdp_fallback.py` (`CdpPageFallback`) e
  `app/collection/providers/magalu_transport.py`
  (`MagaluSearchTransport`/`CdpMagaluSearchTransport`) já separam
  transporte de parser — a Magalu e a Terabyte já usam Edge/CDP como
  transporte primário/único; o Mercado Livre já usa como fallback depois
  do Playwright falhar. **Esta TASK generaliza esse mesmo supervisor para
  ser único e compartilhado por todos os providers**, não mais
  Magalu-specific de nome.
- `BrowserSession`/`PlaywrightStoreProvider` (`app/collection/browser.py`,
  `app/collection/providers/base.py`) continuam sendo a abstração que os
  parsers conhecem — Amazon e Kabum hoje usam Playwright gerenciado
  headless direto (nunca CDP); Pichau usa Playwright gerenciado headed.
  Nenhum desses três já foi validado contra Edge/CDP.

### Estado por loja (headed/headless hoje, `_HEADED_SOURCES` em `worker.py`)

| Loja | Transporte hoje | Validado em Edge/CDP? |
|---|---|---|
| Magalu | Edge/CDP único | Sim (TASK-104A) |
| Terabyte | Edge/CDP único | Sim (TASK-105) |
| Mercado Livre | Playwright headed, Edge/CDP só fallback | Só o fallback (TASK-104B) |
| Pichau | Playwright headed | Não |
| Amazon | Playwright headless | Não |
| Kabum | Playwright headless | Não |

## Arquitetura proposta

### 1. `EdgeCdpSupervisor` único e compartilhado (generaliza `MagaluEdgeSupervisor`)

**Feito na FASE 3.** `MagaluEdgeSupervisor` → `EdgeCdpSupervisor`
(`app/collection/providers/edge_cdp_supervisor.py`); `CdpPageFallback` →
`EdgeCdpTransport` (`app/collection/providers/edge_cdp_transport.py`);
`validate_loopback_cdp_endpoint` extraído para
`edge_cdp_endpoint.py` (deixou de depender de `magalu_transport.py`).
Settings do supervisor renomeadas (`edge_executable`, `edge_profile_dir`,
`edge_startup_timeout_seconds`, `edge_probe_interval_seconds`), nomes
antigos `AISHOPPING_MAGALU_EDGE_*` continuam aceitos por compatibilidade
(mesmo padrão já usado por `edge_cdp_url`/`AISHOPPING_MAGALU_CDP_URL`
desde a TASK-105). Nenhuma lógica de loja entrou no supervisor/transporte
-- ambos continuam sem saber o que é Magalu/Terabyte/Mercado Livre.
Comportamento de Magalu e Terabyte preservado (revalidado ao vivo depois
da mudança).

**Achado colateral (pré-existente, não causado por esta mudança) --
corrigido:** `_is_dedicated_browser()` (usava `Browser.getBrowserCommandLine`
via CDP para decidir se adota um Edge já rodando de antes do restart do
worker) começou a falhar nesta máquina com `Command line not returned
because --enable-automation not set` -- confirmado que a lógica era
idêntica à original (sem diff), então é uma restrição do próprio Edge
neste ambiente atual; fato comprovado por teste direto, sem causa raiz
assumida (não confirmado se veio de atualização do navegador ou outro
motivo -- não afirmamos isso sem evidência). Efeito antes da correção:
se o worker reiniciava enquanto um Edge já supervisionado continuava
vivo de antes, a adoção falhava e a supervisão de recuperação daquele
Edge ficava inativa.

**Correção:** `_is_dedicated_browser()` (CDP) substituído por
`_find_dedicated_edge_process()` -- identificação determinística via
inspeção nativa de processos do Windows (`psutil`, sem shell genérico,
sem heurística só pelo nome `msedge.exe`). Adoção exige processo
`msedge.exe` sem `--type=` (exclui filhos renderer/GPU, que herdam os
mesmos flags e dariam falso positivo) com `--remote-debugging-port=` e
`--user-data-dir=` batendo exatamente (comparação por argumento inteiro
da lista de `cmdline`, não substring) com a porta/perfil configurados.
Se qualquer condição falhar, não adota e não mata o processo
desconhecido -- só retorna conflito de ownership.

Testado ao vivo, com a tela bloqueada: (A) reinício só do worker com
Edge vivo -- adotou o mesmo PID, Magalu real funcionou depois; (B) kill
do Edge adotado -- supervisor detectou e relançou (~5s), nova coleta
real (Terabyte) funcionou; (C) porta CDP ocupada por um Edge de perfil
diferente (simulado de propósito) -- supervisor recusou adotar, **não**
matou o processo estranho, worker seguiu vivo e estável sem Edge. Sem
processos duplicados/órfãos em nenhum dos três casos.

Renomear/generalizar o supervisor já existente para um único Edge
dedicado por processo `collection_worker`, usado por **todos** os
providers que precisarem de navegador — não mais um conceito
"Magalu-only". Continua: perfil dedicado próprio do AIShoppingAgent
(nunca perfil pessoal), CDP restrito a loopback, abre sob demanda,
reutiliza durante um batch quando fizer sentido (mesma conexão CDP para
claims sequenciais do mesmo ciclo), encerra por idle configurável
(timeout novo), recupera automaticamente se cair — tudo já comprovado
pelo supervisor atual, só deixando de ser específico da Magalu.

### 2. Transporte segue desacoplado do parser (requisito 3)

Nenhum provider passa a importar Edge/CDP diretamente. Pichau, Amazon e
Kabum ganham a mesma opção que Magalu/Terabyte/ML já têm — receber um
`cdp_transport`/`edge_fallback` opcional injetado pelo `worker.py`
(exatamente o padrão já usado por `TerabyteProvider`/`MercadoLivreProvider`,
TASK-105/104B) — o parser (`extract()`, normalização, seletor de
resultado) nunca muda.

### 3. Sem Browser Bridge (requisito 1)

Como o `collection_worker` passa a rodar **na própria máquina Windows**
onde o Edge existe, ele controla o Edge via CDP loopback diretamente —
não há necessidade de um processo intermediário/bridge de rede só para
alcançar o navegador. Um bridge só faria sentido se worker e Edge
estivessem em hosts diferentes, o que não é o caso aqui.

### 4. Execução nativa Windows (requisitos 7-8)

- **Task Scheduler, não Windows Service** (confirmado pelo pedido): o
  servidor já faz login automático e bloqueia a tela depois — rodar como
  serviço (Session 0) isolaria o Edge da sessão interativa, que ele
  pode precisar. Uma tarefa agendada configurada para "Executar estando
  o usuário conectado ou não" + gatilho "ao fazer logon" roda na sessão
  interativa mesmo com a tela bloqueada (bloquear ≠ encerrar sessão).
- Inicialização automática após login (gatilho de logon do Task
  Scheduler); recuperação automática se o worker cair (Task Scheduler
  suporta reinício automático em falha — `schtasks`/`Register-ScheduledTask`
  com `-RestartCount`/`-RestartInterval`); recuperação automática do
  Edge já é responsabilidade do `EdgeSupervisor` (item 1), independente
  do Task Scheduler.
- `compose.yaml`: remover o serviço `collection_worker` (requisito 8) —
  API, PostgreSQL, Telegram notifier, `ops_controller`,
  `docker-socket-proxy`, observabilidade continuam.

### 5. `ops_controller`/ADMIN sem acoplamento Docker (requisito 9)

- Novo adapter em `app/ops_controller.py` (ex.: `WindowsWorkerOpsAdapter`),
  ao lado do `DockerOpsAdapter` já existente — dispatch por
  `LogicalService` (`collection_worker` → adapter Windows,
  `telegram_notifier` → adapter Docker, sem mudar).
- **Ponto em aberto (decisão de implementação):** como o `ops_controller`
  roda dentro do Docker/WSL2, sem visão direta de processos do host
  Windows, o novo adapter precisa de um canal próprio para status/start/
  restart — candidatos: (a) reaproveitar o `/metrics` já exposto pelo
  worker (`AISHOPPING_WORKER_METRICS_PORT`) alcançável do WSL2 via IP do
  host Windows, só para status; start/restart via um pequeno agente
  local (ex.: endpoint HTTP restrito a loopback, chamado através do
  mesmo IP de host, que dispare `schtasks /Run`/`/End`) — ou (b) o worker
  Windows escrever seu próprio heartbeat/status num arquivo ou tabela
  que o `ops_controller`/`api` já alcançam via Postgres, sem precisar de
  outro canal de rede. Decisão final fica para a implementação, não para
  este documento — nenhuma das duas exige um "Browser Bridge" (isso é
  controle operacional, não controle do navegador).
- `admin_router.py`/painel Web **não mudam nada** — continuam só
  chamando `ControllerServiceOps`.

### 6. Banco/API para o worker Windows (requisito 10)

- Postgres já exposto em `127.0.0.1:5432` — o worker Windows nativo (na
  mesma máquina) conecta via `AISHOPPING_DATABASE_HOST=127.0.0.1`, sem
  expor a porta além do loopback (já é o padrão hoje,
  `POSTGRES_BIND_ADDRESS` default `127.0.0.1`).
- Secrets do worker (senha do Postgres, chave Gemini ADMIN/DEV, chave
  Groq) passam a vir de arquivo local no Windows (fora do Git/chat, mesmo
  princípio já usado pelos `*_FILE` do Docker) em vez de Docker secrets —
  mecanismo exato (pasta protegida por ACL do Windows vs. outro
  cofre) é decisão de implementação.
- Nenhuma porta nova precisa ser aberta para fora do loopback — o worker
  roda na mesma máquina que já expõe Postgres/API só em `127.0.0.1`.

## Estratégia de validação por loja antes de remover Chromium (requisito 5)

Ordem sugerida (já validadas primeiro, novas depois), cada uma usando
`scripts/validate_store_providers.py --edge-cdp-url` (já existe, TASK-105)
antes de qualquer mudança de default:

1. Magalu, Terabyte — já comprovadas em Edge/CDP; só confirmar que
   continuam funcionando a partir do worker Windows nativo (requisito 6:
   sem regressão).
2. Mercado Livre — hoje só usa Edge/CDP como fallback; validar como
   transporte primário/único (mesmo grau de teste já usado no fallback,
   TASK-104B).
3. Amazon, Kabum, Pichau — nunca testadas contra Edge/CDP; precisam da
   mesma auditoria real (sem evasão, sem stealth) já usada nas demais
   antes de qualquer decisão de trocar o transporte.

**Chromium só sai do `collection_worker`** (do Dockerfile/imagem, se
aplicável a essa altura) depois que as seis lojas estiverem comprovadamente
funcionando via Edge — nunca antes, e nunca por loja isolada sem as
outras cinco confirmadas.

## Riscos identificados

- **Suposição não confirmada:** Postgres/API expostos em `127.0.0.1`
  dentro do WSL2/Docker Desktop realmente aparecem em `127.0.0.1` do
  Windows host — precisa validação real no servidor antes de assumir
  como fato.
- **Canal de status/controle do `ops_controller` para o worker Windows**
  ainda não tem um único caminho öbvio (WSL2 → host Windows) — maior
  incerteza de implementação deste plano (ver "Ponto em aberto" acima).
- **Sessão interativa bloqueada:** Task Scheduler "executar estando
  conectado ou não" com tela bloqueada precisa ser validado ao vivo no
  servidor real — comportamento de Edge/CDP com sessão bloqueada (não
  desconectada) não está documentado neste projeto ainda.
- **Zumbis de processo Chromium/Edge:** o Docker resolvia isso com
  `init: true`/`tini` (TASK-081); um processo Windows nativo precisa de
  equivalente (o próprio `EdgeSupervisor` já mata/recupera o processo
  dedicado, mas renderers/crashpad órfãos do Edge no Windows têm
  comportamento diferente do Linux — validar).
- **Amazon/Kabum/Pichau sem validação prévia em Edge/CDP** — risco real
  de regressão se migrados sem repetir o mesmo processo empírico já
  usado nas outras três lojas (nunca assumir que "funcionou pra Magalu
  então funciona pra todas").
- **Migração de secrets** Docker → arquivo local Windows precisa do
  mesmo cuidado de nunca commitar nem expor no chat (guardrail já vigente
  do projeto).

## Fora de escopo

Implementação de código nesta rodada, deploy, remoção efetiva do
Chromium/Playwright do Dockerfile (só depois de todas as lojas
validadas), qualquer mudança na Terabyte/Magalu além de confirmar que
continuam funcionando (TASK-105/104A já concluídas, sem reabrir).

## FASE 1 — Ops Agent e supervisão do worker (concluída, aprovada no DEV)

**Achado decisivo:** o reinício automático nativo do Task Scheduler
(`RestartCount`/`RestartInterval`) **não funciona** para o processo do
worker morto externamente (testado ao vivo: `Stop-Process -Force`, 90s
de espera, sem reinício, `LastTaskResult=4294967295`). Por isso a tarefa
`AIShoppingAgent-CollectionWorker` ficou restrita a iniciar o worker no
logon/manualmente; a responsabilidade de detectar queda e reiniciar
passou para um componente novo e separado.

**Windows Ops Agent** (`ops_agent/collection_worker_ops_agent.py`):
Windows Service real (pywin32), Session 0, recuperação nativa própria
via SCM (`sc.exe failure`). Não abre nem controla o Edge. Liveness:
reaproveita o rastreamento nativo do próprio Task Scheduler
(`Get-ScheduledTask -TaskName ... | State`, enum .NET — não o texto de
`schtasks /query`, que sai localizado no idioma do SO e quebrou a
primeira tentativa) a cada 15s; ao detectar queda, aciona
`Start-ScheduledTask` com backoff crescente (5/15/30/60/120s) e teto de
5 tentativas por janela de 10min (`RestartGovernor`), evitando restart
loop. Expõe API HTTP restrita (status/start/restart) em loopback
(`127.0.0.1:8021`), assinada por HMAC (mesmo esquema timestamp+nonce já
usado por `app/ops_controller.py`).

**Prova ao vivo (tela bloqueada o tempo todo):** kill externo do processo
→ detecção em ~1.5s → restart acionado (com backoff de 5s) → worker de
volta rodando em ~12-13s → Edge/CDP comprovadamente funcional depois
(navegação real, título extraído). Testado tanto pelo loop interno de
monitoramento quanto pela API HTTP (`/v1/collection_worker/restart`).
HMAC válido aceito, inválido rejeitado, timestamp expirado rejeitado,
replay de nonce rejeitado — todos confirmados via chamadas reais.

**Segredo do Ops Agent:** local definitivo
`C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret`, ACL restrita
a SYSTEM + `BUILTIN\Administrators` (SID fixo `S-1-5-32-544`, não o nome
localizado) via `icacls`. Fora do Git, nunca logado (só o caminho é
logado, nunca o valor). No local antigo (fora do ProgramData, sem ACL
dedicada) o arquivo era removido em menos de ~2min nesta máquina DEV
(Kaspersky) — no local novo, ficou estável por toda a janela de teste
observada (~2m30s). **Pendência explícita:** a persistência definitiva do
segredo em produção (Windows Server, sem Kaspersky) ainda não foi
validada e faz parte obrigatória do preflight de PROD antes do deploy
desta TASK — DEV e PROD são ambientes de antivírus diferentes e não se
pode assumir o mesmo comportamento.

## FASE 3 — Amazon, KaBuM! e Mercado Livre primário (em andamento)

**Amazon e KaBuM!:** ganharam `cdp_transport: EdgeCdpTransport | None`
igual à Terabyte, mas com fallback -- diferente da Terabyte (Playwright
comprovadamente bloqueado), o Chromium gerenciado continua funcionando
nas duas, então sem `edge_cdp_url` configurado `_collect_once` usa
Playwright normalmente. `extract()`/parser 100% preservados. Validado
ao vivo, pipeline completo (missão real via `IntentInterpreter` real,
`source_codes` isolado): `CollectionRun succeeded`, 8 `PriceObservation`
reais cada, `collection.completed.v1` emitido, sem Edge
duplicado/órfão.

**Mercado Livre:** invertida a prioridade (TASK-104B tinha CDP só como
último recurso após o Playwright falhar) -- agora Edge/CDP é tentado
primeiro quando configurado; Playwright gerenciado (que continua
funcionando na ML) vira rede de segurança só se o CDP falhar, em vez de
ser removido. `_collect_once` (não mais `collect()`) decide, mesmo
padrão de Amazon/KaBuM/Terabyte; parâmetro do construtor renomeado de
`edge_fallback` para `cdp_transport` (consistência). Validado ao vivo:
pipeline completo, `CollectionRun succeeded`, 8 observações reais,
`collection.completed.v1`, sem Edge duplicado/órfão.

**Achado importante (não é bug, é escopo real do que "depender de
Chromium" significa):** mesmo com `_collect_once` migrado, Amazon e
KaBuM! continuam abrindo um `BrowserSession` (Chromium gerenciado)
separado durante o enriquecimento de detalhe (`enrich_offer_details` /
`resolve_marketplace_parties` / `resolve_offer_condition`), chamado
pelo orquestrador *depois* de `collect()` retornar -- essa navegação de
página individual nunca passou pelo `_collect_once` e não foi tocada
nesta TASK (fora do que foi pedido: só a busca). A Terabyte não tem essa
dependência residual porque não implementa nenhum desses hooks de
enriquecimento -- por isso ela, e só ela até agora, está genuinamente
livre de Chromium.

**Pichau — parado, não implementado ainda.** Diferente de
Amazon/KaBuM/Terabyte/ML, a Pichau sobrescreve `empty_result_locator`
(distingue "zero resultados legítimo" de bloqueio/erro) e
`navigation_wait_until = "commit"` -- regra de negócio real (TASK-075,
correção de timing). O padrão simples reaproveitado pelas outras quatro
(`EdgeCdpTransport.run()` com só `readiness_selector`) não tem essa
distinção -- aplicá-lo direto faria uma busca com zero resultados
legítimos virar falha técnica, mudando comportamento sem necessidade.
Correção mínima proposta (não implementada, aguardando decisão): dar ao
`EdgeCdpTransport` um método de nível mais baixo que só conecta+navega e
devolve a `Page` (sem fazer o wait/extract embutido), permitindo que a
Pichau reaproveise `PlaywrightStoreProvider._wait_for_results_or_empty`
já existente (mesma lógica usada hoje com Playwright gerenciado) contra
uma página CDP -- sem duplicar a lógica de distinção em código
Pichau-specific.

## FASE 3 (fechamento) — IdentityResolver + remoção dos fallbacks + audit final (concluída no DEV)

**`StoreProductIdentityResolver` (TASK-083) migrado, isolamento preservado:**
`_build_default_providers(edge_cdp_url)` ganha um `EdgeCdpTransport`
próprio (timeout/instância isolados, `_CDP_CONNECT_TIMEOUT_MS` dedicado)
quando `edge_cdp_url` é passado -- mesmo Edge supervisionado da coleta
normal (CDP aceita múltiplas conexões simultâneas, seguro reutilizar),
mas nunca a mesma `EdgeCdpTransport`/instâncias de provider da coleta
normal. `StoreProductIdentityResolver(edge_cdp_url=...)` propaga só para
os providers padrão -- quem passa `providers` explicitamente (testes)
continua no controle total. `worker.py` passa `settings.edge_cdp_url`.
Validado ao vivo: resolução real Kabum->Amazon (`7800X3D` resolvido via
Kabum, zero Chromium; modelos inexistentes no catálogo real retornam
`None` corretamente, comportamento pré-existente, não regressão).

**Fallback pra Chromium removido (Mercado Livre e Pichau):** essas duas
eram as únicas com `except EdgeCdpTransportError: return await
super()._collect_once(request)` -- Amazon e Kabum já não tinham essa
rede de segurança (implementadas diretamente sem ela, TASK-109 rodada
anterior). Removido: com `cdp_transport` configurado, uma falha do CDP
em si agora propaga como falha normal (retry/circuit-breaker existentes
decidem, nunca reabre o Chromium). Sem `cdp_transport` configurado
(dev/ambiente sem Edge), o comportamento é inalterado -- Playwright
continua sendo usado. Dois testes reescritos para a nova política
(`test_mercado_livre_cdp_failure_never_falls_back_to_playwright`,
`test_pichau_cdp_failure_never_falls_back_to_playwright`).

**Pacing centralizado e configurável:** `detail_request_min/max_delay_seconds`
viraram parâmetros de `PlaywrightStoreProvider.__init__` (defaults de
fábrica preservados: 0.6-1.6s) e campos de `Settings`
(`AISHOPPING_DETAIL_REQUEST_MIN/MAX_DELAY_SECONDS`), passados a todos os
providers via `build_collection_adapter`. Nenhum número mágico
duplicado -- um único ponto (`_pace_before_next_detail_request`) lê os
valores da instância.

**Auditoria final obrigatória** (busca completa por `async_playwright`,
`chromium.launch`, `launch(`, `PlaywrightStoreProvider` em todo o
backend):

| Ocorrência | Classificação |
|---|---|
| `browser.py:47` (`BrowserSession`, único `.chromium.launch(` do backend inteiro) | Ainda lança Chromium gerenciado -- mas só alcançável com `cdp_transport is None` (sem `edge_cdp_url` configurado) |
| `edge_cdp_supervisor.py` (`async_playwright()` x2) | Controla Edge/CDP -- inicia/fecha o Edge supervisionado, nunca Chromium |
| `edge_cdp_transport.py` (`async_playwright()`) | Controla Edge/CDP -- `open_blank_page`/`open_page` |
| `magalu_transport.py` (`async_playwright()`) | Controla Edge/CDP -- `CdpMagaluSearchTransport.fetch_html` |
| `scripts/*.py` | Nenhum launch direto -- tudo passa pela mesma abstração de provider já auditada |

Único caminho real restante que abre Chromium: `BrowserSession`, e só
quando `edge_cdp_url` não está configurado. Com a configuração real do
worker Windows nativo (`AISHOPPING_EDGE_CDP_URL` setado, que é a própria
premissa desta TASK), nenhum dos 6 providers nem o
`StoreProductIdentityResolver` chegam a essa linha -- zero Chromium em
operação normal. `BrowserSession`/Playwright biblioteca continuam
existindo (controla Edge via CDP em vários pontos, e é a rede de
segurança para ambientes sem Edge configurado) -- só o Chromium GERENCIADO
deixa de ser necessário.

## FASE 3 (continuação) — Pichau + enriquecimento de detalhe (concluída no DEV)

**Pichau:** ganhou `cdp_transport`, mesmo padrão de fallback de
Amazon/Kabum. Diferente delas, a distinção "zero resultados legítimo"
vs. "bloqueio/erro" (`empty_result_locator`) precisava ser preservada --
resolvido generalizando `EdgeCdpTransport` com um método de nível mais
baixo, `open_blank_page()`/`open_page()` (conecta+navega, devolve a
`Page` pronta, nenhuma decisão de negócio), permitindo que a Pichau
reaproveite `_wait_for_results_or_empty` (base class) sem duplicar
lógica. Validado ao vivo: busca com resultados reais persistidos, busca
legitimamente vazia (`zzzqxw...`) retorna coleção vazia válida sem
exceção, e um teste dedicado confirma que bloqueio/timeout continua
levantando `ProviderBlockedError` (nunca uma coleção vazia silenciosa)
-- 4 testes novos cobrindo os 3 cenários + fallback pro Playwright em
falha de transporte.

**Enriquecimento de detalhe migrado:** `enrich_marketplace_parties`/
`enrich_installment_options`/`enrich_offer_details` (base class,
compartilhados por todos os providers) trocaram `BrowserSession` fixo
por `_open_detail_page()` -- via Edge/CDP (`cdp_transport.open_blank_page()`)
quando configurado, senão o mesmo Playwright gerenciado de sempre.
`cdp_transport` centralizado no `__init__` de `PlaywrightStoreProvider`
em vez de cada subclasse guardar o próprio; hooks `resolve_*` (parser)
100% preservados, só a origem da `Page` muda. Validado ao vivo, ciclo
completo via missão real, com monitoramento contínuo de processos
durante toda a execução: Amazon (`resolve_marketplace_parties`), Kabum
(idem) e Mercado Livre (`resolve_marketplace_parties`+`resolve_offer_condition`)
com `seller_kind`/`fulfillment_kind` reais persistidos; Pichau
(`resolve_installment_options`) com parcelamento real da página
individual mesclado -- **nenhum `chrome.exe` apareceu em nenhum dos
quatro ciclos**, Edge seguiu único e estável.

**Pacing entre navegações de detalhe (pedido à parte, mesma rodada):**
`_pace_before_next_detail_request` -- intervalo curto e aleatório
(0.6–1.6s) entre navegações sequenciais dentro do mesmo lote de
enriquecimento (nunca antes da primeira), para não concentrar rajadas
de requisições idênticas contra a mesma origem, independente de o
navegador em si ser detectado como automatizado ou não. Não afeta o
intervalo entre missões/lojas (isso já é governado pelo agendamento).

**Achado do audit (bloqueador real para a remoção do Chromium):**
`app/collection/identity_resolution.py` (`StoreProductIdentityResolver`,
TASK-083) constrói instâncias PRÓPRIAS e dedicadas de `KabumProvider`/
`AmazonProvider` (`_build_default_providers()`) sem `cdp_transport` --
sempre Playwright gerenciado, independente de `edge_cdp_url` estar
configurado. É usado pelo `CollectionOrchestrator` real
(`identity_resolver=StoreProductIdentityResolver()` em `worker.py`)
para resolver identidade de produto (Kabum -> Amazon) em missões
`PRODUCT_FAMILY`. Não foi migrado nesta rodada -- não estava no escopo
pedido (hooks de enriquecimento), é um mecanismo genuinamente separado
(orçamento/timeout/circuit-breaker próprios, "nunca as instâncias da
coleta normal" por design), e mudá-lo exigiria decisão própria sobre
como injetar `cdp_transport` sem violar esse isolamento deliberado.
**Continua sendo um caminho real que abre Chromium gerenciado** -- por
isso o Chromium ainda não pode ser considerado desnecessário.

## FASE 3 (lifecycle sob demanda) — EdgeCdpSupervisor com lease/idle-timeout (concluída no DEV)

**Problema relatado pelo usuário:** o `EdgeCdpSupervisor` mantinha o Edge
permanentemente vivo desde o início do worker (lançado em `run_worker`,
nunca encerrado até o processo do worker terminar); fechar o Edge
manualmente, mesmo sem nenhuma coleta em andamento, disparava o monitor
de recuperação e reabria o processo. Comportamento final desejado:
lifecycle sob demanda -- Edge só sobe quando algum consumidor
(coleta/enriquecimento/`StoreProductIdentityResolver`) realmente
precisar, reaproveitado durante uso concorrente, encerrado normalmente
após um período configurável sem nenhum uso ativo, e sem relançar
sozinho depois de um encerramento por ociosidade.

**Implementação -- contagem de leases centralizada no `EdgeCdpSupervisor`:**
API pública nova, `lease()` (`@asynccontextmanager`), substitui o antigo
par `start()`/`stop()` eager. `_active_leases` (contador, nunca
negativo) é incrementado/decrementado sob `_lifecycle_lock`
(`asyncio.Lock`); só a transição 0→1 lança/adota o Edge e liga o
monitor de recuperação, só a transição 1→0 desliga o monitor e inicia o
timer de ociosidade (`_idle_timeout_watch`, `idle_timeout_seconds`,
default `DEFAULT_EDGE_IDLE_TIMEOUT_SECONDS = 180.0`, novo campo
`Settings.edge_idle_timeout_seconds`, sem número mágico solto). Se o
timeout expira sem nenhuma lease nova, o Edge é encerrado
(`_close_dedicated_browser`/`_terminate_launcher`) e o supervisor volta
a ficar dormente -- sem monitor, sem processo -- até a próxima `lease()`
real. Providers continuam sem conhecer lifecycle: `EdgeCdpTransport.open_blank_page()`
e `CdpMagaluSearchTransport.fetch_html()` adquirem/liberam a lease
internamente via `AsyncExitStack`, de forma transparente, quando um
`supervisor` é injetado (threading feito em `worker.py` --
`build_edge_supervisor` agora só constrói o objeto, nunca inicia o Edge
-- e em `identity_resolution.py`, que passa o mesmo supervisor do
worker para o `StoreProductIdentityResolver` sem alterar nenhum
orçamento/isolamento da TASK-083: cada resolução ainda usa instâncias
de provider próprias, só a lease do Edge compartilhado é comum).

**Duas condições de corrida identificadas e corrigidas durante o
design** (nenhuma delas chegou a se manifestar em teste -- evitadas por
raciocínio antes de escrever o teste que as provaria):
1. Cancelamento "fire-and-forget" do monitor/timer permitiria que um
   `_start_monitor()` subsequente visse a tarefa antiga ainda não
   `done()` e deixasse de criar uma nova. Corrigido tornando
   `_stop_monitor`/`_cancel_idle_timer` `async` e aguardando
   (`await task`, capturando `CancelledError`) antes de retornar.
2. Uma `lease()` nova chegando exatamente no instante em que o timer de
   ociosidade dispara poderia "adotar" um Edge que já está sendo
   fechado. Corrigido mantendo `_lifecycle_lock` preso durante TODA a
   sequência de encerramento por ociosidade (não só a checagem de
   estado) -- uma `lease()` concorrente espera o encerramento terminar
   antes de relançar, em vez de arriscar interferir nele.

**Validação -- sem coleta real, conforme pedido explicitamente:**
`tests/test_edge_cdp_supervisor.py` (6 testes novos, dublês para
`_ensure_running`/`_cdp_ready`/`_close_dedicated_browser`/
`_terminate_launcher`, timeouts curtos (dezenas de ms) em vez dos 180s
reais, nenhum Edge/processo/rede de verdade) cobre: lançamento sob
demanda na primeira lease; reuso sem relançar em leases
aninhadas/sequenciais dentro do mesmo lote; encerramento após o timeout
de ociosidade configurado; nenhum relançamento entre o encerramento por
ociosidade e a próxima lease real; fechamento manual durante ociosidade
(antes do timeout) não causa relançamento imediato; Edge "morto" durante
uso ativo é recuperado pelo monitor. Suíte focada
(`test_collection_worker.py`+`test_store_providers.py`+
`test_edge_cdp_supervisor.py`) -- 107 testes, sem regressão.

**Resultado (formato solicitado):**
- Edge abre sob demanda: sim
- reutiliza durante batch: sim
- fecha após 180s idle (configurável, `edge_idle_timeout_seconds`): sim
- fechado durante idle não reabre: sim
- morto durante uso recupera: sim

## FASE 2 — worker nativo com Edge (concluída no DEV)

Confirmado por leitura de código que a "generalização do supervisor
Magalu" já estava satisfeita desde a TASK-105 (`settings.edge_cdp_url`
já compartilhado por Magalu/Mercado Livre/Terabyte); nenhuma mudança
necessária em `collection/providers/`.

**Achado 1 -- `localhost` resolve para IPv6 primeiro:** no Windows,
`AISHOPPING_DATABASE_HOST=localhost` resolve para `::1` antes de
`127.0.0.1`; como o Postgres do Docker só publica em IPv4, cada conexão
nova travava ~130s até cair pro IPv4. Corrigido usando `127.0.0.1`
explícito em `backend/.env`/`.env.example` (conexão caiu para ~1.5s).

**Achado 2 -- conflito real de event loop (psycopg async vs. Playwright
no Windows):** `psycopg` em modo assíncrono exige `SelectorEventLoop`;
`asyncio.create_subprocess_exec` (usado tanto pelo `MagaluEdgeSupervisor`
quanto internamente pelo próprio driver do Playwright, que sobe seu
processo Node.js a cada `async_playwright()`/`connect_over_cdp()`) exige
`ProactorEventLoop` -- únicos e incompatíveis entre si no Windows. Não
dava pra isolar a correção só no supervisor: qualquer uso de Playwright
quebra sob `SelectorEventLoop`.

**Decisão (aceita explicitamente):** manter o `ProactorEventLoop` padrão
do Windows (Playwright continua funcionando sem mudança) e trocar,
**só na engine assíncrona do `collection_worker` nativo Windows**, o
driver do Postgres para `asyncpg` (sem essa restrição de loop).
`app/database/session.py` ganhou um parâmetro `async_driver` central
(`create_async_database_engine`/`create_collection_async_database_engine`,
padrão `psycopg`, inalterado para API/Telegram/scripts/migrations/Linux);
`run_worker` (`app/collection/worker.py`) só escolhe `asyncpg` quando
`sys.platform == "win32"`. Único ponto psycopg-specific encontrado no
caminho: `connect_args["options"]` (string libpq `-c lock_timeout=...`)
-- asyncpg usa `server_settings` (dict de GUCs, valores explícitos em
`ms`); a conversão segundos→ms ficou centralizada
(`_timeout_milliseconds`) para não duplicar entre os dois formatos.

**Validação do driver (isolada, antes do ciclo real):** os 3 GUCs
(`lock_timeout`/`statement_timeout`/`idle_in_transaction_session_timeout`)
conferidos via `SHOW` batem exatamente com a config (10s/15s/10s);
commit/rollback corretos; UUID, enum (`UserRole`) e datetime
(`tzinfo=UTC`) sem regressão de tipo via `asyncpg`.

**Prova ponta a ponta (worker nativo, `ProactorEventLoop` + `asyncpg` +
Playwright + Edge real, tela bloqueada):** missão real criada via
`create_mission_from_criteria` (mesmo serviço de domínio usado pela
API/Telegram) para a Magalu, claimed pelo worker no próprio ciclo de
poll, `CollectionRun` `succeeded`, 8 `PriceObservation` reais
persistidas, evento `collection.completed.v1` emitido. Kill externo do
processo -> Ops Agent detectou (~14s) e reiniciou (~24-38s) -- mesmo
mecanismo da FASE 1, sem regressão. Edge reaproveitado após o restart
(mesmo processo/perfil, sem duplicar). Nova missão criada depois do
recovery também foi coletada e persistida com sucesso (8 observações),
provando que o worker recuperado volta a coletar de verdade, não só que
o processo volta a existir.

## FASE 4 (fechamento da migração) — remoção do BrowserSession, compose, Ops Controller, docs (concluída no DEV)

**Auditoria do `BrowserSession` (`app/collection/browser.py`):** consumidores
reais mapeados em duas categorias, não uma:

1. **Produção (`app/collection/providers/base.py`/`stores.py`)** -- o único
   consumidor real de `chromium.launch()` no caminho de coleta. Removido
   por completo: `_open_detail_page()` agora só usa
   `self._cdp_transport.open_blank_page()`, e levanta
   `EdgeCdpTransportError(f"{source_code} CDP transport is not
   configured")` sem `cdp_transport`; a implementação base de
   `_collect_once()` (que abria `BrowserSession`) virou
   `raise NotImplementedError` (cada loja já implementa a própria via
   CDP); Pichau/Amazon/Kabum/Mercado Livre (Terabyte já estava correta
   desde a rodada anterior) trocaram
   `return await super()._collect_once(request)` por
   `raise EdgeCdpTransportError("<Loja> CDP transport is not
   configured")`. Resultado: **nenhum provider abre Chromium gerenciado
   em nenhuma circunstância**, configurado ou não -- sem CDP, falha
   explícita e isolada (tratada pelo retry/circuit-breaker normal, nunca
   reabre Chromium), nunca mais um fallback silencioso.
2. **Teste (`tests/test_store_providers.py`, `tests/test_playwright_browser.py`)**
   -- consumidor real, distinto e legítimo: ~40 testes usam
   `BrowserSession` só para obter um `Page` real e local
   (`page.set_content(html)`, sem rede) e exercitar `.extract()`/parsing
   de HTML estático -- nada a ver com a decisão Chromium-vs-CDP. Decisão:
   **`BrowserSession`/`BrowserSettings` não foram removidos** -- continuam
   existindo, intocados, só como infraestrutura de teste hermética.
   Cerca de 20 testes que monkeypatchavam
   `"app.collection.providers.base.BrowserSession"` para simular o
   fallback de enriquecimento (agora removido) foram reescritos para
   injetar um `cdp_transport` fake (`_FakeCdpTransport.open_blank_page`)
   em vez de um `BrowserSession` fake; os que só afirmavam "Chromium
   nunca deve abrir" tiveram o monkeypatch removido (a garantia agora é
   estrutural, não mais um mock que levanta exceção).

**Auditoria final repetida (mesmo método da rodada anterior -- busca
completa por `async_playwright`, `chromium.launch`, `launch(`):** zero
ocorrências de `chromium.launch()` fora de `app/collection/browser.py`
(a classe de teste em si). Nenhum caminho de coleta real -- configurado
ou não -- alcança Chromium.

**`compose.yaml`:** serviço `collection_worker` removido por completo
(build, environment, secrets, healthcheck). `database`, `api`,
`telegram_notifier`, `ops_controller`, `docker-socket-proxy`,
observabilidade inalterados.

**`Dockerfile`:** parou de instalar o binário do Chromium
(`playwright install --with-deps chromium`) e a infraestrutura de Xvfb
(`/tmp/.X11-unix`, `backend/docker-entrypoint.sh` removido, `ENTRYPOINT`
virou `CMD` direto) -- nenhum serviço Docker restante abre navegador. O
pacote Python `playwright` continua em `requirements.txt` (tipos
importados por `app.collection`, mesmo sem nunca lançar um browser). O
binário do Chromium continua necessário só para rodar a suíte de testes
local/CI, fora da imagem de produção (ver
`docs/architecture/playwright.md`).

**`ops_controller.py`:** novo `WindowsOpsAgentAdapter`, ao lado do
`DockerOpsAdapter` já existente -- dispatch por `LogicalService`
(`collection_worker` -> Windows Ops Agent via HTTP loopback assinado
em `http://host.docker.internal:8021`, configurável por
`WINDOWS_OPS_AGENT_URL`; `telegram_notifier` -> Docker, inalterado).
Resolve o "ponto em aberto" do preflight (`DEC-096`): mesmo esquema
HMAC+timestamp+nonce já usado pelo `ops_controller`, allowlist fechada
(3 rotas fixas do Ops Agent, sem shell/comando genérico), estado nativo
(`Get-ScheduledTask.State`) traduzido para o mesmo vocabulário que o
Docker já usava (`running`/`stopped`/`unavailable`/`unknown`). Novo
secret (`windows_ops_agent_secret`, `compose.yaml`) precisa do mesmo
valor gerado pelo Ops Agent em
`C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret` -- cópia
manual, os dois lados não sincronizam sozinhos.

**Documentação:** `docs/architecture/windows-collection-worker.md`
(novo -- arquitetura completa do runtime DEV: Python, asyncpg,
PostgreSQL via IPv4, Edge/perfil dedicado, Task Scheduler, Ops Agent,
secrets em ProgramData, variáveis, `edge_idle_timeout_seconds`);
`docs/architecture/playwright.md` reescrito (papel duplo de Playwright:
CDP em produção, Chromium só em teste); `docs/architecture/service-operations.md`
atualizado (dois adapters do `ops_controller`).
`docs/installation/windows-server.md` **intocado de propósito** -- ainda
descreve a produção real (sete serviços Docker, incluindo
`collection_worker`); atualizar esse guia é tarefa do deploy desta
migração, fora do escopo desta rodada.

**Validação:** só suíte focada (`test_store_providers.py`,
`test_store_provider_installments.py`, `test_collection_worker.py`,
`test_admin_operations.py`) + import/config -- sem nova coleta real, sem
suíte completa (pedido explícito do usuário).

**Limpeza do runtime DEV (pedido à parte, mesma janela):** Ops Agent
(serviço `AIShoppingAgentOpsAgent`) parado sem sucesso por falta de
elevação (fica pendente, comandos documentados acima); Task Scheduler
`AIShoppingAgent-CollectionWorker` parada/desabilitada/removida; processo
nativo do worker e toda a árvore do Edge dedicado encerrados. Pedido
explícito do usuário: **não reinstalar nem reiniciar nada disso agora**
-- a limpeza é definitiva até nova instrução.

**Ainda não feito (fora do pedido desta rodada):** deploy em produção;
atualização de `docs/installation/windows-server.md` para a nova
arquitetura; criação de um venv dedicado para o worker Windows (roda no
Python oficial da máquina, sem virtualenv, ver
`docs/architecture/windows-collection-worker.md`); padronização do
mecanismo de secrets locais do worker (hoje só "arquivo local, fora do
Git/chat", sem cofre/ACL formalizados como o do Ops Agent);
reinstalação do Ops Agent/Task Scheduler/worker/Edge na máquina DEV.
