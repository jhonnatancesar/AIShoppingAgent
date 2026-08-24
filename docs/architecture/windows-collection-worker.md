# Runtime Windows nativo do collection_worker

TASK-109 tirou o `collection_worker` do Docker/Linux (Chromium via
Playwright) e passou a rodá-lo como processo Windows nativo, controlando
um Microsoft Edge real via CDP loopback. Este documento descreve essa
arquitetura -- válida hoje na máquina DEV; a migração de produção
(`docs/installation/windows-server.md`, ainda os sete serviços em Docker)
é um passo posterior, fora do escopo deste documento.

API, PostgreSQL, Telegram notifier, `ops_controller` e observabilidade
continuam em Docker (`compose.yaml`) -- só o `collection_worker` roda
fora dele.

## Visão geral dos componentes

```
Task Scheduler (logon trigger)
  -> python -m app.collection.worker (processo nativo Windows)
       -> asyncpg -> PostgreSQL (Docker, 127.0.0.1:5432)
       -> EdgeCdpSupervisor -> Microsoft Edge dedicado (CDP loopback)

AIShoppingAgentOpsAgent (Windows Service, pywin32)
  -> monitora o estado da Task Scheduler a cada 15s
  -> aciona Start-ScheduledTask se o worker cair
  -> expõe HTTP loopback assinado (status/start/restart)

ops_controller (Docker) -> WindowsOpsAgentAdapter -> Ops Agent (host, 8021)
```

## Python e dependências

O worker roda no Python oficial da máquina (mesmo interpretador usado
pelos utilitários de dev, ex. `manage_secrets.py`), com
`backend/requirements.txt` instalado diretamente -- **sem virtualenv
dedicado nesta migração**. Um venv próprio para o worker é recomendável
antes de levar isso a produção, mas não foi criado nesta rodada (nada
neste ponto depende disso; documentado aqui como pendência conhecida,
não como decisão definitiva).

```powershell
python -m pip install -r backend/requirements.txt
```

`playwright` (o pacote Python) é necessário -- o worker usa
`Playwright.chromium.connect_over_cdp()`, nunca `chromium.launch()`. O
binário do Chromium **não é necessário** para o worker em si (só para
rodar a suíte de testes local, ver `docs/architecture/playwright.md`).

## Banco de dados: asyncpg, não psycopg

O worker nativo Windows usa `asyncpg` como driver assíncrono do
PostgreSQL, diferente de `psycopg` (usado por API/Telegram/migrations,
inalterado). Motivo: `psycopg` em modo assíncrono exige
`SelectorEventLoop`; `asyncio.create_subprocess_exec` -- usado tanto pelo
`EdgeCdpSupervisor` quanto internamente pelo driver do Playwright, que
sobe seu processo Node.js a cada `async_playwright()`/`connect_over_cdp()`
-- exige `ProactorEventLoop`, incompatível com `SelectorEventLoop` no
Windows. `app/database/session.py` centraliza a escolha
(`async_driver`, padrão `psycopg`); `run_worker` (`app/collection/worker.py`)
seleciona `asyncpg` só quando `sys.platform == "win32"`.

O PostgreSQL continua em Docker, exposto em `127.0.0.1` (padrão
`POSTGRES_BIND_ADDRESS`) -- o worker, rodando na mesma máquina, conecta
via:

```
AISHOPPING_DATABASE_HOST=127.0.0.1
AISHOPPING_DATABASE_PORT=5432
```

(`localhost` resolve para `::1` antes de `127.0.0.1` no Windows, e o
Postgres do Docker só publica em IPv4 -- usar o literal `127.0.0.1`,
nunca `localhost`, evita ~130s de espera por conexão até cair pro IPv4.)

## Edge dedicado: `EdgeCdpSupervisor`, lifecycle sob demanda

`EdgeCdpSupervisor` (`app/collection/providers/edge_cdp_supervisor.py`)
mantém um Microsoft Edge normal, com perfil próprio (nunca o perfil
pessoal do usuário), exposto em CDP loopback. Lifecycle **sob demanda**
(TASK-109, fechamento): o Edge só é lançado no primeiro uso real
(`lease()`, chamado internamente por `EdgeCdpTransport`/
`CdpMagaluSearchTransport`/`StoreProductIdentityResolver` -- nunca pelos
providers diretamente); enquanto houver uso ativo, um monitor recupera o
Edge automaticamente se o processo morrer; quando a última lease é
liberada, um timer de ociosidade começa -- sem uso novo antes do timeout,
o Edge é encerrado normalmente e só volta a subir na próxima necessidade
real (fechar o Edge manualmente enquanto ocioso nunca causa
relançamento).

Configuração (todas opcionais, com defaults de fábrica):

| Variável | Default | Descrição |
|---|---|---|
| `AISHOPPING_EDGE_CDP_URL` | nenhum (obrigatório para o worker real) | Endpoint HTTP loopback (`127.0.0.1`/`localhost`/`::1`, porta explícita) |
| `AISHOPPING_EDGE_EXECUTABLE` | descoberta automática (Program Files) | Caminho do `msedge.exe` |
| `AISHOPPING_EDGE_PROFILE_DIR` | `%TEMP%\aishoppingagent-magalu-edge-profile` | Perfil dedicado, exclusivo do AIShoppingAgent |
| `AISHOPPING_EDGE_STARTUP_TIMEOUT_SECONDS` | 30 | Espera pelo CDP ficar pronto após lançar |
| `AISHOPPING_EDGE_PROBE_INTERVAL_SECONDS` | 1 | Intervalo do monitor de recuperação |
| `AISHOPPING_EDGE_IDLE_TIMEOUT_SECONDS` | 180 | Tempo sem nenhuma lease ativa antes de encerrar o Edge |

Sem `AISHOPPING_EDGE_CDP_URL` configurada, todos os seis Store Providers
falham explícito (`EdgeCdpTransportError`) -- nunca abrem Chromium
gerenciado como fallback (ver `docs/architecture/playwright.md`).

## Task Scheduler: `AIShoppingAgent-CollectionWorker`

O worker roda como uma tarefa agendada, não como Windows Service: o
servidor faz login automático e bloqueia a tela (não desconecta) --
rodar em Session 0 (Windows Service) isolaria o Edge da sessão
interativa que ele precisa. A tarefa está configurada para "executar
estando o usuário conectado ou não", com gatilho de logon.

**Achado importante:** o reinício automático nativo do Task Scheduler
(`RestartCount`/`RestartInterval`) não funciona para um processo morto
externamente (confirmado ao vivo: `Stop-Process -Force` não disparou
reinício). Por isso a tarefa em si só inicia o worker no logon/manualmente
-- a detecção de queda e o reinício são responsabilidade do Windows Ops
Agent, abaixo.

## Windows Ops Agent: supervisão e integração com `ops_controller`

`ops_agent/collection_worker_ops_agent.py` -- Windows Service real
(pywin32, nome `AIShoppingAgentOpsAgent`), roda em Session 0, com
recuperação nativa própria via SCM (`sc failure`). **Não abre nem
controla o Edge** -- só supervisiona o estado nativo da Scheduled Task.

- A cada 15s, consulta `Get-ScheduledTask` (estado em inglês, enum .NET
  -- não `schtasks /query`, que sai localizado no idioma do SO); se a
  task caiu, aciona `Start-ScheduledTask` com backoff crescente
  (5/15/30/60/120s) e teto de 5 tentativas por janela de 10 min
  (evita restart loop).
- Expõe uma API HTTP mínima, restrita a loopback
  (`127.0.0.1:8021`): `POST /v1/collection_worker/status|start|restart`,
  assinada com o mesmo esquema HMAC+timestamp+nonce já usado por
  `app/ops_controller.py`.
- Segredo compartilhado: gerado automaticamente no primeiro start em
  `C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret`, ACL restrita
  a `SYSTEM` + `BUILTIN\Administrators` (SID fixo `S-1-5-32-544`, não o
  nome localizado) via `icacls`. Fora do Git, nunca logado.

Instalar/gerenciar o serviço (como Administrador):

```powershell
python ops_agent\collection_worker_ops_agent.py install
python ops_agent\collection_worker_ops_agent.py start
python ops_agent\collection_worker_ops_agent.py stop
python ops_agent\collection_worker_ops_agent.py remove
```

### Integração com o `ops_controller` (Docker)

`ops_controller.py` despacha por `LogicalService`:
`telegram_notifier` continua no `DockerOpsAdapter`; `collection_worker`
passa pelo `WindowsOpsAgentAdapter`, que assina e chama a API acima via
`http://host.docker.internal:8021` (Docker Desktop for Windows resolve
`host.docker.internal` para portas em loopback do host, sem
`extra_hosts`/rede nova). Requer um **segundo** secret, cópia manual do
valor gerado acima:

```
WINDOWS_OPS_AGENT_URL=http://host.docker.internal:8021   # compose.yaml, já default
WINDOWS_OPS_AGENT_SECRET_FILE=/run/secrets/windows_ops_agent_secret
```

O arquivo `.secrets/windows_ops_agent_secret` (Docker) precisa conter
**exatamente** o mesmo valor de
`C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret` (Windows) --
os dois lados não sincronizam sozinhos; copiar manualmente, nunca por
chat/Git. Ver `docs/architecture/service-operations.md` para o contrato
completo dos dois adapters.

## Variáveis de ambiente do worker (`.env` do host Windows)

Além das variáveis já documentadas em
[Configuração](../installation/configuration.md) (banco, IA, retry,
circuito, agendamento), o worker Windows precisa especificamente:

- `AISHOPPING_DATABASE_HOST=127.0.0.1` (nunca `localhost`, nunca o nome
  do serviço Docker `database`);
- `AISHOPPING_EDGE_CDP_URL` (obrigatória -- sem ela, toda coleta falha
  explícito, ver acima);
- as demais `AISHOPPING_EDGE_*` acima, se os defaults não servirem;
- secrets do worker (senha do Postgres, chave Gemini ADMIN/DEV, chave
  Groq) em arquivo local Windows, fora do Git/chat -- mesmo princípio já
  usado pelos `*_FILE` do Docker, mecanismo de armazenamento local ainda
  não padronizado nesta rodada.

## O que NÃO mudou

Parser, normalização, matching, circuit-breaker, retry, agendamento de
missões, persistência -- nada disso conhece Windows/Edge/Docker. A
mudança é inteiramente de infraestrutura de transporte/execução.
