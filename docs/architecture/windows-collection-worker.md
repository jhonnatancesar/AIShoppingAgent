# Runtime Windows nativo do collection_worker

TASK-109 tirou o `collection_worker` do Docker/Linux (Chromium via
Playwright) e passou a rodá-lo como processo Windows nativo, controlando
um Microsoft Edge real via CDP loopback. Este documento descreve essa
arquitetura -- em produção desde `v1.2.1`/`v1.2.2` (`DEC-103`/`DEC-104`),
na mesma máquina Windows Server que roda os demais serviços em Docker.

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

Em produção, o worker roda num virtualenv dedicado,
`C:\App\AIShoppingAgent-runtime\worker\.venv`, isolado do Python usado
pelos utilitários de dev (`manage_secrets.py` etc.) -- é o caminho que
`scripts\manage_collection_worker_task.ps1 -PythonPath` aponta para a
Scheduled Task.

```powershell
C:\App\AIShoppingAgent-runtime\worker\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

`playwright` (o pacote Python) é necessário -- o worker usa
`Playwright.chromium.connect_over_cdp()` contra o Edge supervisionado.
**Nenhum binário de Chromium é baixado, instalado ou executado em
nenhuma parte deste projeto** -- produção e suíte de testes local usam o
mesmo Microsoft Edge instalado no sistema (ver
`docs/architecture/playwright.md`).

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

O worker roda como uma tarefa agendada, não como Windows Service: rodar
em Session 0 (Windows Service) isolaria o Edge da sessão interativa que
ele precisa -- um Windows Service roda sem estação de janela real,
GUI/CDP não funcionam de forma confiável nesse contexto.

**Requisito de sessão -- não ambíguo, resolvido por prova ao vivo (ver
`docs/tasks/TASK-109.md`, FASE 1):** o servidor precisa manter a sessão
Windows **sempre logada** via auto-logon; a **tela pode ficar
bloqueada** (bloquear não é o mesmo que deslogar -- a sessão e a estação
de janela continuam existindo). Essa combinação foi comprovada ao vivo
com a tela bloqueada o tempo todo: kill externo do processo → detecção
pelo Ops Agent → restart → Edge/CDP funcional depois (navegação real,
título extraído). **Sessão efetivamente deslogada nunca foi testada e
não é suportada** -- não configurar a tarefa para depender disso. Por
essa razão, o Principal da tarefa usa `-LogonType Interactive` amarrado
ao usuário do auto-logon (nunca `ServiceAccount`/`S4U`/`Password`, que
rodam sem sessão interativa real e provavelmente quebrariam o Edge).

**Instalação/atualização reproduzível:** `scripts\manage_collection_worker_task.ps1`
(idempotente, `-Action Install|Update|Status|Enable|Disable|Remove`,
suporta `-WhatIf` e `-StartDisabled` para preparar a infraestrutura antes
da janela de manutenção sem disparar coleta real -- ver cabeçalho do
script para o contrato completo de parâmetros). Substitui qualquer
construção manual da tarefa durante o deploy.

**Achado importante:** o reinício automático nativo do Task Scheduler
(`RestartCount`/`RestartInterval`) não funciona para um processo morto
externamente (confirmado ao vivo: `Stop-Process -Force` não disparou
reinício) -- por isso o script fixa `RestartCount 0` de propósito. A
tarefa em si só inicia o worker no logon/manualmente -- a detecção de
queda e o reinício são responsabilidade do Windows Ops Agent, abaixo.

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
`http://host.docker.internal:8021`. A resolução automática de
`host.docker.internal` pelo Docker Desktop **não é garantida** -- neste
servidor ela está bloqueada porque `daemon.json` fixa `dns: ["1.1.1.1",
"8.8.8.8"]` (fix do incidente Tailscale/MagicDNS, ver `DEC-103`), o que
faz o resolvedor embutido dos containers encaminhar também
`*.docker.internal` para os servidores externos (NXDOMAIN). Por isso o
serviço `ops_controller` do `compose.yaml` declara
`extra_hosts: ["host.docker.internal:host-gateway"]`, escopado só nele
(único consumidor de `WINDOWS_OPS_AGENT_URL`) -- `host-gateway` é
resolvido pelo Docker Engine, não pelo proxy DNS do Docker Desktop, e
continua alcançando o Ops Agent em loopback do host. Requer um
**segundo** secret, cópia manual do valor gerado acima:

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

## Configuração do worker (DEC-104 -- padronizado, `v1.2.2`)

**Nunca `backend\.env` em produção.** O worker Windows nativo é
configurado exclusivamente por (1) variáveis de ambiente de **Máquina**
do Windows, para valores não secretos, e (2) referências `*_FILE` --
também variáveis de Máquina -- apontando para arquivos já existentes em
`C:\App\AIShoppingAgent\.secrets\`, o mesmo mecanismo `_SECRET_FILE_FIELDS`
que `app/core/config.py` já usa para os containers Docker. Nenhuma chave
secreta é aceita como valor direto de variável de ambiente, nunca vai
para `.env`, nunca vai para o Git -- só o caminho do arquivo.

**Fonte canônica dos secrets:** `C:\App\AIShoppingAgent\.secrets\` -- os
MESMOS arquivos que os containers Docker montam via `secrets:` no
`compose.yaml`. Nenhuma cópia/duplicação para outro diretório.

**ACL esperada de `.secrets\`** (diretório e todo arquivo filho, sem
exceção): somente `CESAR-SERVER\Administrator`, `BUILTIN\Administrators`
e `NT AUTHORITY\SYSTEM`, todos `FullControl`, herança bloqueada acima
desse diretório. Nunca `BUILTIN\Users`, `Authenticated Users` ou
`Everyone`. Cobre os três consumidores reais: Docker Desktop (bind mount
dos secrets dos containers, roda como `Administrator` nesta máquina),
`AIShoppingAgentOpsAgent` (roda como `LocalSystem`) e a Scheduled Task do
worker (`LogonType Interactive`, usuário `Administrator`).

**Settings realmente consumidos pelo worker** (auditado em
`app/collection/worker.py` + `app/ai_provider/manager.py`
`build_admin_dev_ai_provider_manager` + `app/collection/shared_collection.py`
+ `app/market_research/`, idêntico entre `v1.2.1` e `v1.2.2` -- só
`compose.yaml` mudou entre as duas):

| Setting | Obrigatório? | Secreto? | Variável |
|---|---|---|---|
| `database_password` | sim, sem default -- crasha no startup sem ele | sim | `AISHOPPING_DATABASE_PASSWORD_FILE` |
| `cesar-core-client-dev` | sim, sem default -- AI/Search falham fechados sem ele | sim | `AISHOPPING_CESAR_CORE_API_KEY_FILE` |
| `firecrawl_api_key` | não -- sem ele, enriquecimento `/v2/scrape` fica desligado | sim | `AISHOPPING_FIRECRAWL_API_KEY_FILE` |
| `edge_cdp_url` | não -- sem ela, Magalu/MercadoLivre/Terabyte falham isolados (sem fallback Playwright); Amazon/Kabum/Pichau caem para Playwright puro | não | `AISHOPPING_EDGE_CDP_URL` |
| `database_host` | não, default `localhost` -- produção exige `127.0.0.1` explícito | não | `AISHOPPING_DATABASE_HOST` |
| `database_port` | não, default já bate (`5432`) -- explícito por determinismo | não | `AISHOPPING_DATABASE_PORT` |
| `database_name`, `database_user` | não, defaults já batem (`aishoppingagent`/`aishoppingagent`) -- **não sobrepostos**, evita duplicação desnecessária | não | -- |
| `telegram_bot_token`, `telegram_webhook_secret`, `ops_controller_secret`, `windows_ops_agent_secret` | **não usados pelo worker** (outros serviços) | -- | nunca configurar aqui |
| demais (poll/batch/retry/circuit/cadence/fan-out/...) | não, defaults documentados em [Configuração](../installation/configuration.md) | não | não sobrepostos |

Valores de produção: `AISHOPPING_DATABASE_HOST=127.0.0.1`,
`AISHOPPING_DATABASE_PORT=5432`, `AISHOPPING_EDGE_CDP_URL=http://127.0.0.1:9223`.

**Provisionamento/reprovisionamento -- reproduzível via
`scripts\manage_collection_worker_config.ps1`** (companheiro de
`manage_collection_worker_task.ps1`, mesmo padrão operacional):

```powershell
# Auditar o estado atual (nunca imprime conteúdo de secret):
powershell -File scripts\manage_collection_worker_config.ps1 -Action Status

# Aplicar/reaplicar (idempotente, seguro rodar quantas vezes for preciso):
powershell -File scripts\manage_collection_worker_config.ps1 -Action Install

# Ver o que seria alterado, sem gravar nada:
powershell -File scripts\manage_collection_worker_config.ps1 -Action Install -WhatIf

# Reprovisionar uma máquina nova: instalar Python/venv dedicado, rodar
# manage_secrets.py para os secrets em .secrets\ (se ainda não existirem
# nesta máquina), corrigir a ACL (ver acima) e rodar -Action Install --
# o script falha explícito e não inventa valor se algum secret
# obrigatório estiver faltando.
powershell -File scripts\manage_collection_worker_config.ps1 -Action Remove  # decomissionar
```

O script faz preflight antes de gravar qualquer variável: valida
`ProjectRoot`/`SecretsDir` existem, caminhos são absolutos, `.secrets`
está coberto por `.gitignore`, a ACL está correta, e que todo secret
obrigatório existe como arquivo -- se faltar algum, para e reporta sem
gerar valor artificial.

**Variáveis de Máquina e Task Scheduler -- comprovado ao vivo, sem
depender de reboot:** gravar em `[Environment]::SetEnvironmentVariable(...,
"Machine")` não atualiza o ambiente de processos-filho de uma sessão
shell já aberta (herança de bloco de ambiente do processo pai, comum a
qualquer shell no Windows) -- mas o Task Scheduler não sofre dessa
limitação: ele monta o ambiente do processo do zero, a partir do
registro, a cada disparo. Comprovado nesta PROD em 2026-08-28: as
variáveis foram gravadas e, na mesma sessão já logada, um
`Start-ScheduledTask` imediato (via Windows Ops Agent, sem logoff/reboot/
restart de serviço) já iniciou o worker com a configuração nova,
conectou no Postgres e no Gemini ADMIN/DEV, e coletou de verdade
(`collection_runs` reais com `status=succeeded`). Por isso nenhum
launcher/wrapper intermediário foi criado -- provou-se desnecessário.

**Comportamento após trocar de tag/release:** como a configuração vive em
variáveis de Máquina do Windows (fora do checkout Git), trocar de tag
(`git checkout vX.Y.Z`) **não afeta** a configuração já aplicada -- ela
persiste entre releases. Rodar `-Action Status` após cada troca de tag
para confirmar que nada mudou nos settings consumidos pelo worker (a
tabela acima) antes de reiniciar o worker; se a nova tag adicionar/remover
algum setting, atualizar este documento e o script na mesma release.

## O que NÃO mudou

Parser, normalização, matching, circuit-breaker, retry, agendamento de
missões, persistência -- nada disso conhece Windows/Edge/Docker. A
mudança é inteiramente de infraestrutura de transporte/execução.
