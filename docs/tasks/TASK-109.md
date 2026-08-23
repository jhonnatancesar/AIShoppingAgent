# TASK-109 — Migrar o collection_worker para Windows nativo com Edge

Status: **Formalizada (preflight + plano de migração); aguardando aprovação. Nenhum código escrito.**

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

### 1. `EdgeSupervisor` único e compartilhado (generaliza `MagaluEdgeSupervisor`)

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
