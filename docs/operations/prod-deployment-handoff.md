# Handoff de deploy para PROD — GG Oferta + César Core/OmniRoute + Coupon Worker

Documento autossuficiente para o Claude que vai executar este deploy
**diretamente no Windows Server de PROD**, sem acesso à conversa que
preparou este handoff. Tudo que você precisa para decidir e agir está
aqui ou nos documentos linkados — nenhum deles pressupõe contexto de
chat anterior.

**Escopo deste deploy:** publicar em PROD um conjunto de trabalho já
concluído, testado e aprovado em DEV, mas nunca antes implantado:
FASE E.1–E.3 (Fetch/Enrichment via César Core), F1/F2/F3 (bootstrap
histórico + avaliação de mercado), consumo de cupons no GG Oferta +
coleta real no Coupon Worker, FASE G (feature flags), a política real
de providers AI (`ai_profile`, César Core/OmniRoute) e a separação de
cota de missões USER/ADMIN_DEV + correção do Telegram.

**Este deploy foi explicitamente autorizado pelo usuário em 07/09/2026**
(`DEC-119`, `docs/internal/decision-log.md`). Nenhuma parte deste
documento deve ser lida como "aguardando autorização" — se você
encontrar texto assim em outro documento deste repositório, ele é
histórico (descreve um estado anterior a essa data), não o atual.

**PROD não clona nem builda o repositório `cesar-core`.** Tudo que este
deploy precisa do César Core/OmniRoute/Redis/SearXNG está em
`deploy/prod/` neste próprio repositório (bundle de Compose + runbook de
provisionamento), usando exclusivamente a imagem já publicada e
verificada no GHCR. Ver seção 5.

**Antes de fazer qualquer coisa:** leia a seção "Gate Gemini" perto do
fim deste documento. Se esse gate falhar por um motivo NÃO documentado
ali como aceito, pare e não force nada — reporte, não improvise.

---

## 1. Repositórios

| Componente | Repositório em PROD | Branch |
|---|---|---|
| GG Oferta | `https://github.com/jhonnatancesar/AIShoppingAgent` — **clonar/atualizar no servidor** | `main` |
| César Core | **Não clonar.** Só a imagem publicada (`ghcr.io/jhonnatancesar/cesar-core:1.2.1`) é usada, via `deploy/prod/cesar-core.compose.yaml` neste repositório GG Oferta | — |
| Coupon Worker | `https://github.com/jhonnatancesar/AIShoppingAgent-cupom.git` — **clonar/atualizar no servidor** | **`master`** (não é `main` — confirme antes de qualquer comando que assuma o nome da branch) |

**Tags de release reais existem agora — use-as, não hashes de commit
soltos** (confirme sempre com `git fetch --tags` no servidor; isto é
uma foto de um instante, não uma garantia de que nada mudou depois):

| Componente | Tag | Commit | Observação |
|---|---|---|---|
| GG Oferta | **`v1.3.7`** | `200a1f1` (`main`) | Substitui `v1.3.6` (dois blockers reais encontrados no deploy: `cost_policy` fixo em `free_only` bloqueava `admin_dev` com `403 ai_policy_denied` -- `DEC-124`; secrets `omniroute_ai`/`_search`/`_fetch` ilegíveis pelo UID de runtime do `cesar-core`, causando `503` sem o Core sequer chamar o OmniRoute -- `DEC-125`) |
| César Core | **`v1.2.1`** | `a5ba084` | Política real de providers AI (`ai_profile`, 4 connections, 2 combos) + saneamento documental. **Imagem já publicada e verificada no GHCR:** `ghcr.io/jhonnatancesar/cesar-core:1.2.1` (também `:1.2`, `:1`, `:latest`). **PROD não usa esta tag/repositório diretamente — só a imagem, via `deploy/prod/cesar-core.compose.yaml`.** |
| Coupon Worker | **`v1.0.0`** | `5f502e1` (`master`) | Primeira release — coleta real de cupons com persistência no Postgres do GG |

**`v1.2.0` do César Core existiu por um instante e falhou na
verificação do workflow** (`__version__` do pacote não batia com a tag —
corrigido na hora, tag não foi movida, ver `README.md` do `cesar-core`,
seção "Release privada"). **Não usar `v1.2.0` nem a imagem
`ghcr.io/jhonnatancesar/cesar-core:1.2.0`** — use `v1.2.1`/`1.2.1`.

Histórico relevante anterior a estas tags, para contexto:
- GG Oferta: `208b0bb` (consumo de cupons), `9fd5108` (FASE G:
  F1/F2/F3 + cupons sob flags), `fba5472` (correção de checkpoint de
  revisão), `cab1f98` (`ai_profile` no contrato com o Core).
- César Core: `83d3347` (política real de providers AI, primeira
  implementação).
- Coupon Worker: `caca098` (persistência no PostgreSQL do GG +
  refinamento de esgotamento).

**Para o deploy em si:** faça checkout das tags acima nos dois
repositórios que existem em PROD (`git checkout v1.3.7` — confirme que é
essa a mais recente com `git tag --sort=-creatordate` antes — no GG
Oferta, `v1.0.0` no Coupon Worker). O César Core **não tem repositório
em PROD**: use `deploy/prod/cesar-core.compose.yaml` (deste próprio
checkout do GG Oferta, já em `v1.3.7`), que já referencia `ghcr.io/
jhonnatancesar/cesar-core:1.2.1` como imagem padrão — nenhum `docker
build`, nenhum clone do repositório `cesar-core`.

## 2. O que fazer primeiro (antes de qualquer deploy)

**Só dois repositórios têm checkout git em PROD: GG Oferta e Coupon
Worker.** O César Core não tem — pule o preflight de git para ele; a
"instalação" dele em PROD é o bundle `deploy/prod/cesar-core.compose.yaml`
dentro do checkout do GG Oferta (nada a clonar/atualizar separadamente).

Para **cada um dos dois repositórios com checkout** (GG Oferta, Coupon
Worker), nesta ordem, sem pular etapas:

1. Identifique o diretório real do repositório no servidor (caminho
   pode diferir do usado em DEV — confirme, não assuma
   `C:\AIShoppingAgent\AIShoppingAgent` / `C:\AIShoppingAgenteCupom` sem
   checar; a topologia aprovada usa `C:\App\AIShoppingAgent` para
   deployments em PROD — `DEC-118` item 8, GG Oferta. O César Core não
   tem diretório próprio em PROD desde a correção desta rodada
   (`DEC-119`) — não crie um `C:\App\cesar-core` git checkout).
2. `git status` — leia com atenção. Se houver qualquer alteração local
   não commitada, **preserve-a** (não descarte, não faça `stash drop`,
   não faça `checkout .`/`restore .`) até entender o que é e confirmar
   com quem pode responder por isso.
3. `git branch --show-current` — confirme que está na branch certa
   (`main` para GG Oferta, `master` para Coupon Worker).
4. `git fetch origin --tags`.
5. Compare HEAD local com `origin/<branch>`
   (`git rev-parse HEAD` vs. `git rev-parse origin/<branch>`).
6. **Nunca** use `git reset --hard` automaticamente para "resolver" uma
   divergência — se HEAD local diverge de `origin` de um jeito que não
   seja um simples atraso (fast-forward possível), pare e reporte antes
   de decidir.
7. Só depois de entender o estado, faça o pull/fast-forward seguro
   (`git pull --ff-only`) e o checkout da tag da seção 1.

Repita para os dois repositórios antes de prosseguir para a seção 3.

## 3. Migrations necessárias (só GG Oferta)

Head esperado após o pull: `20260906_0002`. Duas migrations compõem
esse head, ambas já testadas em DEV:

- `20260906_0001_add_historical_bootstrap_retry` — retry/backoff
  exponencial e lease do `HistoricalBootstrap` (F1).
- `20260906_0002_add_coupons` — schema de `coupons`/`coupon_offer_links`.

**DEV já está no head `20260906_0002`. PROD ainda não teve nenhuma das
duas aplicada — confirme o head real de PROD antes de rodar qualquer
coisa:**

```powershell
docker compose run --rm api python -m alembic -c alembic.ini current
```

Se o head de PROD já for `20260906_0002` por algum motivo não
documentado aqui, **pare e reporte** — não é o estado esperado.
Aplicar:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
```

César Core e Coupon Worker não têm migration própria neste deploy
(Coupon Worker grava em tabelas já existentes do GG Oferta — a
migration acima precisa estar aplicada **antes** de configurar
`COUPONS_POSTGRES_DSN` nele).

## 4. Configuração necessária (nomes de variável — nunca valores aqui)

### GG Oferta — container `api` (`.env` do Compose + `.secrets/`, nunca `backend/.env`)

| Variável | Papel |
|---|---|
| `AISHOPPING_DEFAULT_MAX_ACTIVE_MISSIONS` | Cota de missões ativas USER — default de código `5`, não precisa de variável explícita a menos que se queira outro valor |
| `AISHOPPING_DEFAULT_MAX_ACTIVE_MISSIONS_ADMIN_DEV` | Cota de missões ativas ADMIN/DEV — **valor decidido: `50`**. Sem esta variável, ADMIN/DEV herda o default do USER (`5`) |
| `AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` | Flag F1 — default `false` |
| `AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` | Flag F3 — default `false` |
| `AISHOPPING_COUPONS_ENABLED` | Flag de consumo de cupons — default `false` |
| `AISHOPPING_CESAR_CORE_BASE_URL` | URL do César Core visto pelo container `api` -- **`http://host.docker.internal:8100`**, já é o default em `compose.yaml` desde `DEC-121` (ver "Portas/URLs aprovadas para PROD" abaixo; `127.0.0.1` não funciona daqui) |
| `AISHOPPING_CESAR_CORE_API_KEY_FILE` | Arquivo com o Bearer do GG Oferta → César Core -- já aponta para `/run/secrets/cesar_core_api_key` em `compose.yaml`; só é preciso gravar o arquivo host (`.secrets/cesar_core_api_key`, mesmo valor de `deploy/prod/cesar-core/.secrets/ggoferta-core-client`) |
| `AISHOPPING_TELEGRAM_BOT_TOKEN_FILE` / `AISHOPPING_TELEGRAM_WEBHOOK_SECRET_FILE` | Secrets do bot (já existentes, sem mudança nesta rodada) |
| `AISHOPPING_DATABASE_*` | PostgreSQL do GG Oferta (já existente) |

Detalhe completo de todas as variáveis (não só as novas):
[`docs/installation/configuration.md`](../installation/configuration.md).

### GG Oferta — worker nativo (`AIShoppingAgent-CollectionWorker`, Scheduled Task Windows)

**`DEC-104`/`DEC-122`, nunca `backend\.env` em produção para este
processo** (isso é diferente do container `api` acima -- são dois
mecanismos de configuração deliberadamente separados, ver
[`docs/architecture/windows-collection-worker.md`](../architecture/windows-collection-worker.md#configuração-do-worker-dec-104----padronizado-v122)).
O worker nativo é configurado exclusivamente por **variáveis de
ambiente de Máquina do Windows**, geridas de forma reproduzível e
idempotente por
[`scripts/manage_collection_worker_config.ps1`](../../scripts/manage_collection_worker_config.ps1):

```powershell
# Auditar sem gravar nada:
powershell -File scripts\manage_collection_worker_config.ps1 -Action Status

# Aplicar/reaplicar (idempotente):
powershell -File scripts\manage_collection_worker_config.ps1 -Action Install
```

O script já cobre (desde `DEC-122`) o wiring completo do César Core
para este processo -- nenhum parâmetro extra é necessário além dos
defaults, que já são os valores corretos de PROD:

| Variável de Máquina | Papel | Valor |
|---|---|---|
| `AISHOPPING_CESAR_CORE_BASE_URL` | URL do César Core visto pelo processo nativo -- **`http://127.0.0.1:8100`** (loopback -- transporte diferente do container `api`, que usa `host.docker.internal`; mesma arquitetura lógica, `DEC-121`) | já é o default do script, gravado explicitamente por determinismo |
| `AISHOPPING_CESAR_CORE_API_KEY_FILE` | Caminho **absoluto** para o mesmo Bearer GG→Core do container -- `C:\App\AIShoppingAgent\.secrets\cesar_core_api_key` | gravado automaticamente pelo script, apontando para o arquivo em `-SecretsDir` (default `C:\App\AIShoppingAgent\.secrets`) |

**Pré-requisito único:** o arquivo `C:\App\AIShoppingAgent\.secrets\cesar_core_api_key`
precisa existir (mesmo valor do secret `application`/`ggoferta-core-client`
do lado do César Core -- seção "Geração de secrets locais" abaixo) --
o script **falha explícito e não inventa valor** se estiver ausente
(preflight, `Assert-Preflight`). Nunca escreve nem imprime o conteúdo do
secret, só referencia o caminho do arquivo.

**Task Scheduler lê as variáveis de Máquina frescas a cada disparo**,
sem precisar de logoff/reboot/restart -- comprovado ao vivo em PROD em
2026-08-28 para este exato mecanismo (comentário no próprio script).
Não é necessário reiniciar a Scheduled Task manualmente depois de rodar
`-Action Install`; a próxima vez que ela disparar já lê a configuração
nova.

### César Core (`deploy/prod/.env` — lido pelo Compose por estar na mesma
pasta de `cesar-core.compose.yaml`, não é o `.env` de nenhum repositório
`cesar-core`, que não existe em PROD)

| Variável | Papel |
|---|---|
| `CESAR_CORE_AI_ENABLED` / `CESAR_CORE_SEARCH_ENABLED` | Opt-in — default `false`. Sem eles, `/v1/ai/generate`/`/v1/search` respondem "não configurado" mesmo com tudo mais no ar |
| `CESAR_CORE_AI_USER_MODEL` | Nome do combo OmniRoute para `ai_profile=user` — **`user-cascade`** |
| `CESAR_CORE_AI_ADMIN_DEV_MODEL` | Nome do combo OmniRoute para `ai_profile=admin_dev` — **`admin-dev-cascade`** |
| `CESAR_CORE_SECURITY_GG_OFERTA_API_KEY_FILE` | Caminho do arquivo de secret `application` (par do `AISHOPPING_CESAR_CORE_API_KEY_FILE` do GG) — default já aponta para `./cesar-core/.secrets/ggoferta-core-client`, só sobrescreva se usar outro caminho |
| `CESAR_CORE_OMNIROUTE_AI_API_KEY_FILE` / `_SEARCH_API_KEY_FILE` / `_FETCH_API_KEY_FILE` | Só controlam o caminho HOST dos secrets originais `omniroute_ai`/`omniroute_search`/`omniroute_fetch` (consumidor Core → OmniRoute, diferentes das 4 connections AI da seção 5.2) — o `cesar-core` em si lê as cópias corrigidas em `/run/secrets-fixed/...`, não esse caminho direto (`DEC-125`, ver seção 5.1) |
| `CESAR_CORE_SEARXNG_SECRET_FILE` | Idem, para o secret `searxng` |
| `CESAR_CORE_PUBLISHED_PORT` | Porta publicada em `127.0.0.1` — default `8100` |

Todas essas variáveis (e seus defaults) já estão explícitas em
`deploy/prod/cesar-core.compose.yaml` (`${VAR:-default}`) — só crie
`deploy/prod/.env` se precisar sobrescrever algum default; sem esse
arquivo, os defaults do próprio compose já bastam para AI/Search
desligados (estado inicial esperado, seção 7 passo 6).

### OmniRoute (dentro do volume do César Core, não é `.env`)

- Senha administrativa do painel (`/api/auth/login`) — arquivo
  `deploy/prod/cesar-core/.secrets/omniroute-admin.env`, gerado na
  primeira instalação (ver "Geração de secrets locais" abaixo).
- 4 chaves de provider AI: `gemini_user_api_key`,
  `gemini_admin_dev_api_key` (chave **diferente** da de USER),
  `groq_admin_dev_api_key`, `openrouter_admin_dev_api_key` — usadas só
  no provisionamento manual das 4 connections (seção 5.2); não são
  arquivo de secret montado pelo Compose.
- 3 credenciais consumidoras (`ggoferta-ai`, `ggoferta-search`,
  `ggoferta-fetch`) — **emitidas automaticamente pelo próprio
  OmniRoute** e gravadas direto nos arquivos de secret pelo script
  `bootstrap-omniroute-keys.ps1` (seção 5.1, etapa A). Procedimento
  fechado numa rodada anterior (antes era um gap documentado, "pare e
  reporte"): determinístico, idempotente, sem decisão manual, nenhum
  valor passa por log/output/Git — ver seção 5.1 para o comando e
  [`deploy/prod/cesar-core/bootstrap-omniroute-keys.js`](../../deploy/prod/cesar-core/bootstrap-omniroute-keys.js)
  para o mecanismo interno. **`DEC-125`:** os 3 arquivos ficam com o UID
  do container OmniRoute e `mode 0600` — ilegíveis pelo UID de runtime
  do `cesar-core` (10001:10001). O serviço `cesar-core-secrets-fix`
  (mesmo bundle, roda automaticamente antes do `cesar-core` via
  `depends_on`) corrige isso copiando os 3 arquivos para um volume
  interno com o dono/permissão certos — não precisa de nenhum passo
  manual extra, só `docker compose up -d` normal (etapa B abaixo).
- 1 credencial compartilhada GG↔Core (`ggoferta-core-client`) — mesmo
  valor usado em `AISHOPPING_CESAR_CORE_API_KEY_FILE` do lado do GG
  Oferta (decisão sua, gere um valor forte para os dois lados; não tem
  procedimento automático, ao contrário das 3 credenciais acima).
- 1 chave Firecrawl para a connection `firecrawl` (seção 5.3, `DEC-126`)
  — **não existe secret canônico a reaproveitar** (o antigo
  `firecrawl_api_key` do GG Oferta e seu único consumidor foram
  removidos do repositório na FASE E.1); é um valor novo, fornecido pelo
  operador na hora do provisionamento, igual às 4 chaves de provider AI
  acima.

**Provisionamento completo esperado do OmniRoute, nesta ordem** (nenhuma
das quatro categorias abaixo "já vem pronta" com o bundle — todas
precisam ser criadas do zero em cada ambiente novo, PROD incluído):
1. 4 connections de AI (seção 5.2);
2. 2 combos de AI (`user-cascade`/`admin-dev-cascade`, seção 5.2);
3. 1 connection de Search (`searxng-search`, seção 5.3);
4. 1 connection de Fetch (`firecrawl`, seção 5.3).

Nomes de secret, procedimento completo e corpo de requisição exatos:
seções 5.2/5.3 deste documento e
[`deploy/prod/omniroute-provisioning.md`](../../deploy/prod/omniroute-provisioning.md)
(neste mesmo repositório — não precisa do repositório `cesar-core`).

### Coupon Worker (`.env` gerado por `install.ps1`)

| Variável | Papel |
|---|---|
| `AUTH_TOKEN` | Autentica o endpoint de controle local do worker |
| `COUPONS_POSTGRES_DSN` | **Obrigatória para PROD** — aponta para o mesmo PostgreSQL do GG Oferta, credencial própria. Sem ela, o worker grava só em SQLite local e o GG Oferta nunca vê os cupons coletados (ver seção 9) |

### Portas/URLs aprovadas para PROD

Este documento não define uma topologia de rede nova — a decisão
estrutural (`DEC-118` item 8, GG Oferta) é: mesmo Windows Server físico,
lifecycle/secrets/rede independentes entre GG Oferta e César Core/
OmniRoute (nunca um único Compose cobrindo tudo), nenhuma exposição
pública do Core ou do OmniRoute. **Correção da rodada `DEC-119`:** a
definição do Compose do César Core (`deploy/prod/cesar-core.compose.yaml`)
passou a ser distribuída dentro do checkout do GG Oferta em vez de um
checkout próprio do repositório `cesar-core` — isso muda só onde o
arquivo YAML mora, não a independência operacional: é um projeto Compose
separado (`name: cesar-core`, subido/parado com seu próprio `docker
compose -f deploy/prod/cesar-core.compose.yaml ...`, secrets próprios em
`deploy/prod/cesar-core/.secrets/`), nunca misturado ao Compose do GG
Oferta. Só o GG Oferta é público (via Tailscale Funnel, já em uso — ver
[Instalação → Windows Server](../installation/windows-server.md)).
César Core publica só `127.0.0.1:8100` no host que o hospeda; Redis/
OmniRoute/SearXNG não têm porta publicada. Se a topologia real do
servidor divergir do que este parágrafo descreve, **pare e reporte** —
não é uma decisão que este handoff autoriza a improvisar.

**`DEC-121` (2026-09-07) — blocker real encontrado no deploy, corrigido:**
como GG Oferta e César Core são dois projetos Compose independentes no
mesmo host, `127.0.0.1:8100` **de dentro do container `api`** do GG
Oferta aponta para o próprio container `api`, nunca para o Windows
Server que publica o César Core em `127.0.0.1:8100` -- o `api` não
conseguia falar com o Core de jeito nenhum com esse valor. Corrigido
reaproveitando o mesmo mecanismo já usado por
`WINDOWS_OPS_AGENT_URL`/`ops_controller` (`DEC-103`): `host.docker.internal`
+ `extra_hosts: host-gateway`, agora também no serviço `api`. **URL
correta por tipo de processo** (a mesma distinção lógica, dois
transportes):

| Processo | `AISHOPPING_CESAR_CORE_BASE_URL` |
|---|---|
| Container `api` (`compose.yaml`, Docker) | `http://host.docker.internal:8100` -- já é o default no `compose.yaml`, não precisa de `.env` para isso |
| `collection_worker` nativo (Windows, fora do Docker) | `http://127.0.0.1:8100` -- inalterado, `backend/.env` |

Só o `api` recebe este wiring (`extra_hosts` + variáveis
`AISHOPPING_CESAR_CORE_*` + secret `cesar_core_api_key`) porque só ele
usa a capability AI do Core (`app/telegram/router.py`, interpretação de
linguagem natural do webhook). `telegram_notifier` e `ops_controller`
não chamam o César Core em nenhum código real -- confirmado por
auditoria de código, não wireados de propósito.

### Geração de secrets locais na primeira instalação

Estes três secrets **não existem em nenhum repositório** e precisam ser
gerados uma vez, diretamente no servidor, na primeira instalação. Em
nenhum dos três casos você deve inventar/digitar manualmente um valor,
nem exibir o valor gerado em log, relatório ou chat.

**Identidade GG↔Core (`ggoferta-core-client`/`cesar_core_api_key`,
`DEC-122`)** — Bearer que autentica as chamadas do GG Oferta (container
`api` **e** worker nativo, mesmo valor para os dois -- seção 4 acima)
contra o César Core. Um único valor, gravado em **dois arquivos** (um
por deployment independente, `DEC-118` item 8) -- gere uma vez e grave
nos dois, na mesma sessão PowerShell, sem nunca exibir o valor:

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToBase64String($bytes) -replace '\+','-' -replace '/','_' -replace '='
New-Item -ItemType Directory -Force -Path ".secrets" | Out-Null
New-Item -ItemType Directory -Force -Path "deploy\prod\cesar-core\.secrets" | Out-Null
Set-Content -Path ".secrets\cesar_core_api_key" -Value $secret -NoNewline -Encoding utf8
Set-Content -Path "deploy\prod\cesar-core\.secrets\ggoferta-core-client" -Value $secret -NoNewline -Encoding utf8
Remove-Variable secret, bytes
```

Confirme que os dois arquivos existem e não estão vazios
(`(Get-Item .secrets\cesar_core_api_key).Length -gt 0` e o equivalente
para o outro caminho) — nunca exiba o conteúdo. Se um dos dois arquivos
já existir de uma instalação anterior, **não sobrescreva** -- copie o
valor já existente para o arquivo que estiver faltando em vez de gerar
um novo (senão os dois lados passam a usar credenciais diferentes e
toda chamada GG→Core falha por `401`/`403`).

**`verification_code_pepper` (GG Oferta)** — usado pelo HMAC do código
de verificação de 6 dígitos. Não está coberto pelo fluxo automático de
`backend/scripts/manage_secrets.py` (gap real, não corrigido nesta
rodada — só documentado aqui). Gere e grave assim, em uma única
sessão PowerShell:

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToBase64String($bytes) -replace '\+','-' -replace '/','_' -replace '='
Set-Content -Path ".secrets\verification_code_pepper" -Value $secret -NoNewline -Encoding utf8
Remove-Variable secret, bytes
```

Confirme depois que o arquivo existe e não está vazio
(`(Get-Item .secrets\verification_code_pepper).Length -gt 0`) — nunca
exiba o conteúdo (`Get-Content`) no seu relatório.

**`INITIAL_PASSWORD` do OmniRoute** (`deploy/prod/cesar-core/.secrets/omniroute-admin.env`,
referenciado pelo bundle da seção 5.1) — só bootstrapa a senha
administrativa do painel do OmniRoute na primeira vez que ele sobe (não
sobrescreve uma senha já configurada em subidas seguintes). Gere de
forma equivalente:

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToBase64String($bytes) -replace '\+','-' -replace '/','_' -replace '='
New-Item -ItemType Directory -Force -Path "deploy\prod\cesar-core\.secrets" | Out-Null
Set-Content -Path "deploy\prod\cesar-core\.secrets\omniroute-admin.env" -Value "INITIAL_PASSWORD=$secret" -NoNewline -Encoding utf8
Remove-Variable secret, bytes
```

Guarde esse valor em um cofre de senhas real (fora do servidor) antes de
seguir — ele é a senha administrativa do OmniRoute e não fica em
nenhum outro lugar depois de gerado.

## 5. OmniRoute — provider connections e combos (não estão no Git)

### 5.1. Subir César Core + OmniRoute + Redis + SearXNG (bundle, sem clonar `cesar-core`)

**Duas etapas obrigatórias, nesta ordem.** O container `cesar-core`
monta `omniroute_ai`/`omniroute_search`/`omniroute_fetch` como secrets
de arquivo (Docker: fail-closed — o container não sobe se o arquivo não
existir ou estiver vazio), e essas 3 credenciais são **emitidas pelo
próprio OmniRoute**, que por sua vez precisa estar no ar para emiti-las.
Por isso o OmniRoute sobe sozinho primeiro, emite as credenciais, e só
depois o resto do stack sobe.

Pré-requisito de ambas as etapas: `verification_code_pepper` e
`INITIAL_PASSWORD` (subseção "Geração de secrets locais" acima) já
gerados; `ggoferta-core-client` e `searxng` já provisionados com
valores reais (não têm procedimento automático).

**Etapa A — subir só o OmniRoute e emitir as 3 credenciais
consumidoras:**

```powershell
docker compose -f deploy\prod\cesar-core.compose.yaml up -d omniroute
.\deploy\prod\cesar-core\bootstrap-omniroute-keys.ps1
```

`bootstrap-omniroute-keys.ps1` (determinístico, idempotente, nenhuma
decisão manual, nenhuma credencial em log/output):

- espera o container `cesar-core-omniroute-1` ficar `healthy`;
- lê a senha administrativa de
  `deploy/prod/cesar-core/.secrets/omniroute-admin.env` sem nunca
  exibi-la;
- para cada uma das 3 credenciais (`ggoferta-ai`, `ggoferta-search`,
  `ggoferta-fetch`): se o arquivo de secret correspondente já existe e
  não está vazio, **pula** (idempotente — já provisionado); senão,
  confere se já existe uma chave com esse nome no OmniRoute — se
  existir sem o arquivo correspondente, o script **para com erro
  explícito** (estado ambíguo real; não duplica, não apaga nada
  sozinho, a mensagem de erro diz como recuperar); senão, cria a chave
  via `POST /api/keys` e grava o valor bruto direto no arquivo de
  secret, sem nunca passar pelo stdout/log do host.
- termina com código de saída não-zero e mensagem clara se qualquer
  etapa falhar. **Não prossiga para a etapa B se este script falhar.**

Mecanismo interno (container descartável reaproveitando a própria
imagem do OmniRoute, rede Docker, bind mount para o arquivo de saída):
[`deploy/prod/cesar-core/bootstrap-omniroute-keys.js`](../../deploy/prod/cesar-core/bootstrap-omniroute-keys.js).
Validado em DEV nos três cenários relevantes (criação inicial,
reexecução idempotente, e chave existente com arquivo ausente
corretamente recusada) antes de entrar neste handoff.

**Etapa B — subir o restante do stack** (agora que os 3 secrets de
arquivo da etapa A existem):

```powershell
docker compose -f deploy\prod\cesar-core.compose.yaml up -d
docker compose -f deploy\prod\cesar-core.compose.yaml ps
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

Nenhum `docker build`, nenhum clone do repositório `cesar-core` — só
`docker compose pull`/`up` sobre a imagem já publicada e verificada.

**`DEC-125` (2026-09-08):** o `docker compose up -d` acima também sobe
automaticamente o serviço `cesar-core-secrets-fix` **antes** do
`cesar-core` (o Compose respeita a dependência sozinho, `depends_on:
condition: service_completed_successfully` — nenhum comando extra é
necessário). Esse serviço roda uma única vez, como root, e corrige um
problema real de permissão: os 3 arquivos que a etapa A gerou ficam com
o UID do container OmniRoute e `mode 0600`, ilegíveis pelo UID de
runtime do `cesar-core` (10001:10001) — sem essa correção, o `cesar-core`
sobe e responde `/health`, mas qualquer chamada de AI/Search/Fetch que
precise falar com o OmniRoute falha com `503 ai_upstream_unavailable`
(o Core nem chega a enviar a request — `PermissionError` interno,
convertido em `503` genérico pelo adapter). Se `cesar-core-secrets-fix`
falhar, o Compose recusa subir o `cesar-core` (`service
"cesar-core-secrets-fix" didn't complete successfully`) — confirme com
`docker compose -f deploy\prod\cesar-core.compose.yaml logs
cesar-core-secrets-fix` antes de investigar mais. Se os 3 secrets da
etapa A forem regenerados/rotacionados no futuro, rode de novo (comando
idempotente): `docker compose -f deploy\prod\cesar-core.compose.yaml up
cesar-core-secrets-fix`.

### 5.2. Provisionar as 4 connections e os 2 combos de AI

**Fato crítico:** connections e combos do OmniRoute vivem no
banco/volume dele, não em nenhum repositório. Isso significa que
**cada ambiente novo (PROD incluído) precisa ser provisionado do
zero**, e **nenhum UUID de connection/combo do DEV pode ser copiado
para PROD** — cada ambiente gera os seus próprios IDs ao criar os
recursos.

**Antes de provisionar, confira se já não existe** (idempotência —
não duplicar): liste as connections/combos existentes no OmniRoute de
PROD via API administrativa (`GET /api/providers`, `GET /api/combos`)
autenticado com a senha administrativa de PROD. Se as 4 connections e
os 2 combos abaixo já existirem com os nomes certos, pule para a
verificação (passo final desta seção).

**4 connections a garantir que existam** (nomes exatos, um provider
cada):

| Nome | Provider | Modelo aprovado |
|---|---|---|
| Gemini USER | `gemini` | `gemini-3.6-flash` |
| Gemini ADMIN_DEV | `gemini` | `gemini-3.6-flash` |
| Groq ADMIN_DEV | `groq` | `openai/gpt-oss-120b` |
| OpenRouter ADMIN_DEV | `openrouter` | `openrouter/free` |

Duas chaves Gemini **distintas** (USER e ADMIN/DEV nunca compartilham
credencial). Nenhum modelo `latest`/preview — os quatro acima são os
únicos aprovados.

**2 combos a garantir que existam** (`strategy: "priority"`):

- `user-cascade` = Gemini USER → `oc/mimo-v2.5-free` (Groq/OpenRouter
  **não existem** neste combo — é assim, estruturalmente, que USER
  nunca alcança Groq/OpenRouter, não uma checagem em runtime).
- `admin-dev-cascade` = Gemini ADMIN_DEV → Groq ADMIN_DEV →
  OpenRouter ADMIN_DEV → `oc/mimo-v2.5-free`.

`oc/mimo-v2.5-free` é o fallback gratuito/no-auth do catálogo nativo do
OmniRoute (prefixo `oc/`, não `opencode/`) — não precisa de connection
nem de chave.

**Procedimento completo, com corpo de requisição JSON exato, nomes de
secret e teste de cada connection/combo isoladamente:**
[`deploy/prod/omniroute-provisioning.md`](../../deploy/prod/omniroute-provisioning.md)
(neste repositório — cópia mantida em sincronia manual do runbook
original do `cesar-core`, exatamente para que PROD não precise daquele
repositório). Siga esse runbook à risca para o provisionamento em si —
esta seção só resume o que precisa existir ao final.

**Verificação final desta seção** (real, não simulada):

```
POST /api/combos/test  {"comboName": "user-cascade"}
POST /api/combos/test  {"comboName": "admin-dev-cascade"}
```

Cada passo distinto de cada combo deve responder `status: "ok"`. O
passo `oc/mimo-v2.5-free` pode ocasionalmente exceder 15–20s de
latência (característica operacional conhecida do fallback gratuito,
não falha de configuração) — repita o teste antes de investigar mais.

### 5.3. Provisionar as connections de Search e Fetch (`DEC-126`)

**Estas duas connections NÃO vêm prontas com o bundle** — são recursos
do banco/volume do OmniRoute, exatamente como as 4 connections de AI da
seção 5.2, e **também precisam ser provisionadas do zero em cada
ambiente novo**, PROD incluído. O blocker real que motivou esta seção:
sem elas, `/v1/search`/`/v1/fetch` respondem `503 search_upstream_unavailable`
(`"No credentials for searxng-search"`) e `502 fetch_upstream_error`
(`"No credentials for firecrawl"`) mesmo com `CESAR_CORE_SEARCH_ENABLED`/
`CESAR_CORE_FETCH_ENABLED` ligados e o resto do stack saudável.

**2 connections a garantir que existam** (nomes exatos, schema auditado
direto no OmniRoute DEV que já as tem funcionando):

| Nome/`provider` | Precisa de `apiKey`? | `providerSpecificData` |
|---|---|---|
| `searxng-search` | Não (provider sem autenticação) | `{"baseUrl": "http://searxng:8080/search"}` — **nunca** o default de UI `http://localhost:8888/search` |
| `firecrawl` | Sim — ver "Origem da credencial Firecrawl" abaixo | Nenhum |

Não existe campo "capability" separado em nenhuma das duas — é
implícito no valor de `provider` (resolvido internamente pelo
OmniRoute).

**Origem da credencial Firecrawl:** o GG Oferta **não tem mais** um
secret `firecrawl_api_key` canônico para reaproveitar — esse secret e
seu único consumidor (`firecrawl.py`, cliente direto) foram **removidos
do repositório** depois de confirmado que não havia mais nenhum
consumidor (FASE E.1,
[`docs/architecture/cesar-core-integration.md`](../architecture/cesar-core-integration.md)).
A API key desta connection é, portanto, um valor **novo, fornecido pelo
operador no momento do provisionamento** — exatamente como as 4 chaves
de provider AI da seção 5.2 (nunca armazenadas em nenhum repositório,
só digitadas/coladas na hora de criar a connection). Não gere nem
reaproveite nenhum valor antigo.

**Provisionamento determinístico e idempotente** — script versionado,
mesmo padrão do bootstrap de credenciais consumidoras da seção 5.1:

```powershell
.\deploy\prod\cesar-core\bootstrap-omniroute-search-fetch.ps1 `
    -FirecrawlApiKey (Read-Host -AsSecureString "Firecrawl API key")
```

Só é preciso fornecer `-FirecrawlApiKey` na primeira execução (quando a
connection `firecrawl` ainda não existe) — reexecuções com as duas
connections já provisionadas corretamente não pedem nem usam o valor.
Comportamento do script (validado em DEV nesta rodada, ambiente
descartável — connections de teste criadas e removidas ao final):

- connection já existe **e** configuração bate com o esperado (`baseUrl`
  correto para `searxng-search`; `isActive` e `apiKey` presentes para
  `firecrawl`) → pula, idempotente.
- connection já existe **mas** configuração diverge (`baseUrl` errado,
  `isActive=false`, ou `firecrawl` sem `apiKey`) → **para com erro
  explícito**, nunca corrige sozinho (pode ser customização deliberada
  do operador) — revise manualmente no painel do OmniRoute.
- connection não existe → cria com o valor exato documentado acima;
  `firecrawl` exige `-FirecrawlApiKey` (ou `.secrets\firecrawl-api-key`
  já gravado) para a criação — sem isso, falha explícito em vez de
  criar sem credencial.
- nunca imprime nenhum valor de secret (nem a senha administrativa, nem
  a API key do Firecrawl) — só nomes de connection e status.

Mecanismo interno (container descartável reaproveitando a própria
imagem do OmniRoute, mesma rede Docker do bundle):
[`deploy/prod/cesar-core/bootstrap-omniroute-search-fetch.js`](../../deploy/prod/cesar-core/bootstrap-omniroute-search-fetch.js).
Procedimento completo com corpo de requisição JSON exato (para
referência/auditoria manual, se necessário):
[`deploy/prod/omniroute-provisioning.md`](../../deploy/prod/omniroute-provisioning.md)
seção 4.

**Verificação final desta seção** (real, não simulada):

```
POST /v1/search   {"query": "teste", "max_results": 1, "requirements": {"service_class": "economy", "cost_policy": "free_only"}}
POST /v1/fetch     {"url": "https://example.com", "requirements": {"service_class": "economy", "cost_policy": "free_only"}}
```

Ambas via o César Core (`http://127.0.0.1:8100/v1/search` /
`/v1/fetch`, Bearer `application`), não direto no OmniRoute — devem
responder `HTTP 200` com `provider_gateway: "omniroute"` e
`provider: "searxng-search"` / `"firecrawl"` respectivamente.

## 6. Feature flags

| Flag | Onde | Default | OFF (comportamento) | ON (comportamento) |
|---|---|---|---|---|
| `AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` | GG Oferta | `false` | Idêntico ao fluxo anterior à FASE G — sem bootstrap histórico externo | `run_historical_bootstrap` (F1) roda: busca referência histórica externa uma vez por produto elegível, com retry/backoff exponencial e lease contra dupla execução, revalidando a cada 90 dias |
| `AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` | GG Oferta | `false` | Avaliação de mercado (F2) usa só histórico interno, como antes | Avaliação de mercado (F3) também considera a referência histórica externa buscada pelo F1 |
| `AISHOPPING_COUPONS_ENABLED` | GG Oferta | `false` | Preço exibido/alertado nunca considera cupom, mesmo que existam cupons coletados no banco | Ofertas elegíveis (Web e alerta) calculam o melhor cupom aplicável em tempo real (`best_applicable_coupon`) e mostram o preço com desconto |
| `CESAR_CORE_AI_ENABLED` | César Core | `false` | `/v1/ai/generate` responde "não configurado" | AI real via OmniRoute, resolvida por `ai_profile` |
| `CESAR_CORE_SEARCH_ENABLED` | César Core | `false` | `/v1/search` responde "não configurado" | Search real via SearXNG |

**Ordem segura de ativação** (cada uma é independente das outras —
ative uma de cada vez, prove antes de ativar a próxima):

1. `CESAR_CORE_AI_ENABLED` + `CESAR_CORE_SEARCH_ENABLED` (dependem do
   provisionamento da seção 5 já estar feito e verificado).
2. `AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` (F1) — não depende de
   nenhuma outra flag.
3. `AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` (F3) —
   funciona melhor com F1 já ativo há um tempo (referência histórica
   populada), mas não trava se F1 estiver OFF (só não tem o que
   referenciar ainda).
4. `AISHOPPING_COUPONS_ENABLED` — independente das anteriores; precisa
   do Coupon Worker (seção 9) já rodando e gravando cupons reais para
   ter efeito visível.

## 7. Ordem de deploy

1. **Preflight** — seção 2 deste documento, nos dois repositórios com
   checkout (GG Oferta, Coupon Worker). César Core não tem preflight de
   git (não tem checkout).
2. **Pull dos dois repositórios** (fast-forward seguro, já coberto na
   seção 2) + checkout das tags da seção 1.
3. **Backup do estado atual** — snapshot/backup do PostgreSQL de PROD
   antes de qualquer migration (ver
   [Backup e restauração](backup-restore.md) do GG Oferta).
4. **Migrations** (seção 3) — só GG Oferta, só depois do backup.
5. **Secrets de primeira instalação** (seção 4, subseção "Geração de
   secrets locais") — a identidade GG↔Core (`ggoferta-core-client`/
   `cesar_core_api_key`, `DEC-122` -- grava os **dois** arquivos de uma
   vez, mesmo valor), `verification_code_pepper` e `INITIAL_PASSWORD`
   do OmniRoute, se ainda não existirem. Sem os dois arquivos da
   identidade GG↔Core com o mesmo valor, tanto o `api` quanto o worker
   nativo sobem mas toda chamada de AI ao Core falha por credencial.
6. **César Core + OmniRoute + Redis + SearXNG** via o bundle
   `deploy/prod/cesar-core.compose.yaml` (seção 5.1), em duas etapas
   obrigatórias: **(a)** subir só o `omniroute` e rodar
   `bootstrap-omniroute-keys.ps1` para emitir/gravar as 3 credenciais
   consumidoras (`ggoferta-ai`/`ggoferta-search`/`ggoferta-fetch`) —
   script determinístico e idempotente, sem decisão manual; **(b)** só
   então subir o restante do stack (`docker compose ... up -d`).
   Subir/atualizar antes do GG Oferta, já que ele é dependência
   obrigatória (fail-closed) de AI/Search/enrichment. Confirmar `GET
   /health` e `GET /ready` antes de prosseguir. **Nenhum clone/build do
   repositório `cesar-core`.**
7. **Provisionar OmniRoute** — 4 connections + 2 combos de AI (seção
   5.2) **e** as connections de Search (`searxng-search`) e Fetch
   (`firecrawl`) (seção 5.3, `DEC-126`) — nenhuma das quatro categorias
   "já vem pronta" com o bundle, todas exigem provisionamento do zero
   neste ambiente. Rode a verificação real de cada subseção
   (`POST /api/combos/test` para os 2 combos; `POST /v1/search`/
   `POST /v1/fetch` reais para Search/Fetch) antes de prosseguir.
8. **GG Oferta — container `api` + `telegram_notifier`** (`docker
   compose up -d`) — com todas as flags da seção 6 **ainda OFF** neste
   ponto. Confirmar `GET /health` e `GET /ready`. Confirmar também
   (`DEC-121`, blocker real do preflight anterior): dentro do container
   `api`, `curl http://host.docker.internal:8100/health` responde
   (prova que o `extra_hosts` resolve o Windows Server) e uma mensagem
   real via Telegram que exija interpretação de linguagem natural
   retorna uma resposta coerente, não um erro de credencial/conexão --
   essa é a única chamada ao Core que o `api` faz (capability AI, só
   isso).

   **GG Oferta — worker nativo (`AIShoppingAgent-CollectionWorker`,
   Scheduled Task) — ordem obrigatória, `DEC-122`, blocker real
   encontrado num preflight posterior a este mesmo passo 8:**
   1. Identidade GG→Core já provisionada no passo 5 acima
      (`.secrets\cesar_core_api_key` existe com o valor real).
   2. Confirmar de novo que `C:\App\AIShoppingAgent\.secrets\cesar_core_api_key`
      existe e não está vazio (mesmo arquivo do passo 5 -- não crie uma
      segunda cópia).
   3. Executar `powershell -File scripts\manage_collection_worker_config.ps1 -Action Install`
      (idempotente, sem decisão manual -- ver seção 4, subseção "GG
      Oferta — worker nativo"). **Nunca `backend\.env` para este
      processo.**
   4. Validar com `-Action Status`: `AISHOPPING_CESAR_CORE_BASE_URL` e
      `AISHOPPING_CESAR_CORE_API_KEY_FILE` aparecem como "configurada"
      nas variáveis de Máquina (o comando nunca exibe o conteúdo do
      secret, só se a variável está definida e se o arquivo referenciado
      existe).
   5. **Só então** iniciar/confirmar a Scheduled Task
      `AIShoppingAgent-CollectionWorker` (`manage_collection_worker_task.ps1`)
      — ela lê as variáveis de Máquina frescas a cada disparo, sem
      precisar de reboot.
9. **Coupon Worker — primeira instalação em PROD** (seção 9) — diretório,
   `.env` (`AUTH_TOKEN` + `COUPONS_POSTGRES_DSN` apontando para o
   Postgres de PROD), instalar o agendamento (Windows Scheduled Task).
   Pode subir a qualquer momento depois do passo 4; não depende do
   César Core.
10. **Health/readiness dos três** — confirmar antes de tocar em
    qualquer flag (tabela na seção 8 abaixo).
11. **Flags OFF, prova básica** — com tudo no ar e flags ainda
    desligadas, confirmar que o comportamento é idêntico ao anterior a
    este deploy inteiro (nenhuma regressão visível com tudo desligado).
12. **Ativação gradual** — seguir a ordem da seção 6, uma flag por vez,
    com prova real entre cada uma.
13. **Gate Gemini** (seção 11) — antes especificamente da política de
    providers AI valer para tráfego real, não só para as flags de
    F1/F3/cupons.
14. **Prova funcional final** — um ciclo completo real: criar/observar
    uma missão, ver um cupom sendo considerado numa oferta elegível,
    confirmar quota USER=5/ADMIN_DEV=50 na prática, confirmar que o
    Telegram responde claramente quando a quota está cheia.

## 8. Health/readiness

| Serviço | Vivo | Pronto (dependências reais) |
|---|---|---|
| GG Oferta API | `GET /health` | `GET /ready` |
| César Core | `GET /health` | `GET /ready`, `GET /v1/capabilities` |
| Coupon Worker | — | endpoint de controle próprio (`config.json`), autenticado por `AUTH_TOKEN` |

## 9. Coupon Worker — primeira instalação em PROD

**Esta é a primeira instalação deste componente em PROD** (`v1.0.0`,
primeira release). Nenhuma escolha de caminho/forma de execução fica a
critério de quem está executando — siga exatamente estes passos.

1. **Diretório**: clone/atualize o repositório em
   `C:\App\AIShoppingAgentCupom` (paralelo ao `C:\App\AIShoppingAgent`
   do GG Oferta, mesma máquina — DEC-118). Se o servidor já usar outra
   convenção de diretório para os demais componentes, use a mesma, mas
   **confirme antes**, não assuma.
2. **Repo/tag**: `https://github.com/jhonnatancesar/AIShoppingAgent-cupom.git`,
   branch `master`, tag `v1.0.0`.
3. **Instalação**: `.\install.ps1` (cria `.venv`, instala requirements,
   gera `.env` com `AUTH_TOKEN` aleatório automaticamente — não precisa
   gerar esse secret manualmente, `install.ps1` já faz).
4. **Editar `.env`** gerado no passo anterior: adicionar
   `COUPONS_POSTGRES_DSN` apontando para o **mesmo PostgreSQL do GG
   Oferta** (mesmo host/porta/banco, **credencial própria e
   independente** da do GG Oferta — não reutilizar a senha do GG). Sem
   esta variável, o worker grava só em SQLite local e o GG Oferta nunca
   vê nenhum cupom coletado.
5. **Validar antes de agendar**: `python worker.py --once` — leia a
   linha de log emitida por `open_coupon_store()`: deve dizer
   explicitamente **"Coupon store: PostgresCouponStore"**. Se disser
   "Coupon store: SqliteCouponStore — TUDO local", **pare**:
   `COUPONS_POSTGRES_DSN` não foi lida corretamente, não prossiga para o
   agendamento com esse estado.
6. **Agendar**: `.\manage_coupon_worker_task.ps1 -Action Install -TaskUser "DOMINIO\Usuario"`
   (cria a tarefa agendada do Windows; precisa de uma sessão com
   autologon, mesma exigência do `collection_worker` nativo do GG
   Oferta).
7. **Start/stop/status**:
   `.\manage_coupon_worker_task.ps1 -Action Start|Stop|Status`.
8. **Health/logs**: endpoint de controle local (`config.json`,
   autenticado por `AUTH_TOKEN`) para status; logs em texto no diretório
   de instalação (`worker.py` loga cada rodada, incluindo qual
   `CouponStore` está ativo — reconfirme isso periodicamente, não só na
   instalação).

## 10. Coupon Worker — comportamento esperado (ciclo de vida dos cupons)

- Roda **na mesma máquina** do GG Oferta (Playwright + Edge real via
  CDP — não containeriza, não roda em Linux).
- Usa o **mesmo PostgreSQL** do GG Oferta, via `PostgresCouponStore`
  (`COUPONS_POSTGRES_DSN`), credencial própria e independente.
- **SQLite continua em uso mesmo com Postgres configurado** — mas só
  para bookkeeping interno que o GG nunca lê (`source_candidates`,
  janela promocional/`control`). Só a tabela `coupons` vai para o
  Postgres do GG.
- **Não existe fallback silencioso Postgres → SQLite.** Se
  `COUPONS_POSTGRES_DSN` estiver definida e a conexão falhar, o worker
  levanta erro explícito (`CouponStoreIntegrationError`) e não roda —
  nunca degrada para SQLite-only sem avisar. SQLite-only só acontece
  quando `COUPONS_POSTGRES_DSN` está **ausente desde o início**
  (modo local/teste intencional, não esperado em PROD).
- **Ciclo `active`/`expired`:** só esses dois status existem.
  - *Staleness* (`expire_stale`): um cupom `active` que não é
    reconfirmado (não aparece de novo numa varredura) desde um corte de
    tempo vira `expired` — comparação de ausência no banco, sem IA.
  - *Esgotamento explícito*: quando o texto da própria página diz
    literalmente "esgotado"/"esgotada" (nunca "está esgotando", que é
    tratado como ainda válido), E o achado está num escopo confiável
    (card/produto/busca — nunca páginas de nível de loja como home/
    banners, onde o texto pode se referir a outro produto), o cupom já
    nasce `expired` nessa mesma varredura. Reaproveita o status
    `expired` existente — não é um terceiro estado.
  - *Reativação*: se um cupom marcado `expired` (por qualquer um dos
    dois motivos acima) reaparecer numa varredura futura sem o sinal de
    esgotamento, o `upsert` (mesma chave `store_id`+`code`+`evidence`)
    volta o status para `active` automaticamente — sem passo manual.
- **O worker só preserva evidência real** (`raw_rule_text`, `evidence`)
  — nunca fabrica código, valor de desconto ou decide se o cupom se
  aplica a uma oferta específica.
- **O GG Oferta resolve aplicabilidade**, não o worker:
  `best_applicable_coupon` (`backend/app/coupons/pricing.py`) decide,
  em tempo real a cada exibição de oferta (site) e no momento da
  decisão de alerta (snapshot imutável gravado no evento, nunca
  recalculado depois pelo Telegram), qual cupom entre os candidatos
  realmente reduz o preço da oferta específica.

## 11. Gate Gemini — obrigatório antes de ativar a política de providers AI

A política de providers AI (USER → Gemini USER → `oc/mimo-v2.5-free`;
ADMIN/DEV → Gemini ADMIN_DEV → Groq → OpenRouter → `oc/mimo-v2.5-free`)
foi validada em DEV com o fallback completo funcionando (cada
connection desativada/testada/reativada com sucesso), **mas o happy
path com Gemini respondendo como prioridade 1 não foi reproduzido em
DEV** — ficou bloqueado por indisponibilidade/quota real da API Gemini
gratuita usada na própria bateria de testes (confirmado: as duas
connections Gemini seguem `valid: true` isoladamente, não é problema de
credencial/config).

**Antes de considerar esta política ativa para tráfego real em PROD**,
com todas as 4 connections ativas e nenhum fallback forçado:

1. `POST /v1/ai/generate` com `ai_profile=user` → esperado
   `AIResponse.provider == "gemini"`.
2. `POST /v1/ai/generate` com `ai_profile=admin_dev` → esperado
   `AIResponse.provider == "gemini"`.

**Se qualquer uma falhar:**
- Se a causa for indisponibilidade/quota externa **da própria API
  Gemini** (não do OmniRoute, não de configuração) — é a condição já
  aceita e documentada; não é motivo para reverter a política, mas
  registre o resultado e repita mais tarde antes de confiar
  operacionalmente no happy path.
- Se a causa for qualquer outra coisa (erro de configuração, connection
  inválida, combo errado, etc.) — **isso é um blocker operacional
  real**. Não altere fila, prioridade, combo, fallback ou arquitetura
  para tentar forçar sucesso. Pare e reporte.

## 12. O que NÃO fazer

- Não copiar UUID de connection/combo do OmniRoute de DEV para PROD.
- Não usar `git reset --hard` para resolver divergência sem entender a
  causa primeiro.
- Não aplicar migration sem backup do banco antes.
- Não ativar mais de uma flag por vez sem prova real entre elas.
- Não forçar o gate Gemini a "passar" alterando arquitetura/fallback.
- Não clonar nem buildar o repositório `cesar-core` em PROD -- use
  sempre o bundle `deploy/prod/cesar-core.compose.yaml` e a imagem já
  publicada.
- Não criar uma NOVA tag/release do GG Oferta como parte da execução
  deste deploy -- as tags a usar (seção 1) já existem; se durante o
  deploy você achar necessário registrar uma correção documental/de
  código, isso é uma decisão separada, pare e reporte antes de taguear.
- Não exibir/logar o valor de nenhuma credencial (senha administrativa
  do OmniRoute, `ggoferta-ai`/`ggoferta-search`/`ggoferta-fetch`,
  `ggoferta-core-client`, etc.) em nenhum momento -- nem em log, nem em
  relatório, nem em saída de comando copiada para qualquer lugar.
- Não habilitar `ALLOW_API_KEY_REVEAL` no OmniRoute para tentar
  recuperar uma credencial consumidora perdida. Se
  `bootstrap-omniroute-keys.ps1` parar porque a chave já existe no
  OmniRoute mas o arquivo de secret sumiu, siga exatamente a mensagem
  de erro do script (restaurar o arquivo de um backup, ou remover a
  chave órfã pelo painel administrativo e reexecutar) -- não invente
  outro caminho.
- Não montar a credencial `cesar_core_api_key` (ou qualquer variável
  `AISHOPPING_CESAR_CORE_*`) em `telegram_notifier`/`ops_controller` --
  auditoria de código (`DEC-121`) confirmou que nenhum dos dois chama o
  César Core; só o `api` usa a capability AI. Não espalhar credencial
  onde o código não exige.
- Não usar `127.0.0.1`/`localhost` em `AISHOPPING_CESAR_CORE_BASE_URL`
  do container `api` -- dentro dele aponta para o próprio container,
  não para o Windows Server que hospeda o César Core (causa raiz do
  blocker do `DEC-121`). O valor correto para o `api` é
  `http://host.docker.internal:8100` (já é o default em
  `compose.yaml`).
- Não criar/editar `backend\.env` para configurar o worker nativo
  (`AIShoppingAgent-CollectionWorker`) em PROD -- `DEC-104`/`DEC-122`:
  esse processo usa exclusivamente variáveis de ambiente de Máquina do
  Windows, via `scripts\manage_collection_worker_config.ps1`. Um
  `backend\.env` criado manualmente não quebraria nada sozinho (variável
  de Máquina tem precedência), mas é um mecanismo de configuração
  concorrente que não deveria existir -- se você achar um
  `backend\.env` em PROD, não assuma que ele é a fonte de verdade real
  do worker nativo.
- Não criar um segundo arquivo de secret para o worker nativo --
  `AISHOPPING_CESAR_CORE_API_KEY_FILE` do worker aponta para o MESMO
  arquivo (`.secrets\cesar_core_api_key`) que o secret do container
  `api`, nunca uma cópia própria.
- Não corrigir o `503`/`PermissionError` do Core lendo
  `omniroute_ai`/`_search`/`_fetch` com `chmod 777`, `chmod 644`
  indiscriminado, ou qualquer "world-readable" -- `DEC-125`: use o
  serviço `cesar-core-secrets-fix` do bundle (já sobe sozinho via
  `depends_on`), nunca relaxe a permissão do arquivo original do
  bootstrap.
- Não setar `uid`/`gid`/`mode` na sintaxe longa de `secrets:` do Compose
  esperando corrigir ownership de secret -- `DEC-125`: esses campos só
  têm efeito sob Docker Swarm, nunca sob `docker compose up` puro (o
  único jeito que este bundle usa). Não têm efeito nenhum aqui.
- Não altere a imagem/`USER` do `cesar-core` para "resolver" permissão
  de secret -- a correção fica inteira no bundle de deploy (novo
  serviço + volume), nunca no repositório/imagem do César Core.
- Não envie `cost_policy=free_only` para `ai_profile=admin_dev` (nem
  vice-versa) -- `DEC-124`: `user` sempre `free_only`, `admin_dev`
  sempre `paid_allowed`, é o próprio `ai_profile` que já determina isso
  no código (`backend/app/ai_provider/cesar_core.py`), nunca decida isso
  manualmente numa chamada de teste.
- Não assuma que as connections `searxng-search`/`firecrawl` "já vêm
  configuradas" com o bundle -- `DEC-126`: são recursos do banco do
  OmniRoute, exatamente como as 4 connections de AI, e precisam de
  provisionamento explícito (seção 5.3) em cada ambiente novo.
- Não use `http://localhost:8888/search` na connection `searxng-search`
  -- é só um placeholder de UI do OmniRoute, nunca persistido; o valor
  correto e obrigatório é `http://searxng:8080/search` (`DEC-126`).
- Não reaproveite nenhum valor antigo de `firecrawl_api_key` do GG
  Oferta para a connection `firecrawl` do OmniRoute -- esse secret e seu
  único consumidor foram removidos do repositório na FASE E.1
  (confirmado, sem consumidor restante); a API key da connection é um
  valor novo, fornecido pelo operador (`DEC-126`).
- Não corrija uma connection `searxng-search`/`firecrawl` existente mas
  com configuração incompatível rodando o bootstrap de novo esperando
  que ele conserte sozinho -- `DEC-126`: o script para com erro
  explícito de propósito; revise manualmente no painel do OmniRoute.

## 13. Referências (mesma arquitetura, sem redesenho)

- Bundle de deploy do César Core (Compose, sem clonar o repositório):
  [`deploy/prod/cesar-core.compose.yaml`](../../deploy/prod/cesar-core.compose.yaml).
- Runbook de provisionamento OmniRoute (cópia local, sem clonar o
  repositório): [`deploy/prod/omniroute-provisioning.md`](../../deploy/prod/omniroute-provisioning.md).
- Bootstrap determinístico das 3 credenciais consumidoras
  (`ggoferta-ai`/`ggoferta-search`/`ggoferta-fetch`), seção 5.1 etapa A:
  [`deploy/prod/cesar-core/bootstrap-omniroute-keys.ps1`](../../deploy/prod/cesar-core/bootstrap-omniroute-keys.ps1)
  (wrapper) e
  [`deploy/prod/cesar-core/bootstrap-omniroute-keys.js`](../../deploy/prod/cesar-core/bootstrap-omniroute-keys.js)
  (mecanismo interno).
- Correção de permissão dos mesmos 3 secrets para o UID de runtime do
  `cesar-core` (`DEC-125`, sobe automaticamente via `depends_on`, seção
  5.1 etapa B):
  [`deploy/prod/cesar-core/fix-omniroute-secret-permissions.py`](../../deploy/prod/cesar-core/fix-omniroute-secret-permissions.py).
- Bootstrap determinístico das connections `searxng-search`/`firecrawl`
  (`DEC-126`, seção 5.3):
  [`deploy/prod/cesar-core/bootstrap-omniroute-search-fetch.ps1`](../../deploy/prod/cesar-core/bootstrap-omniroute-search-fetch.ps1)
  (wrapper) e
  [`deploy/prod/cesar-core/bootstrap-omniroute-search-fetch.js`](../../deploy/prod/cesar-core/bootstrap-omniroute-search-fetch.js)
  (mecanismo interno).
- Arquitetura canônica GG ↔ Core (background/racional -- não é
  operacionalmente necessário para executar este deploy, os dois itens
  acima já bastam): `docs/architecture/gg-oferta-core.md` no
  repositório `cesar-core`, se você tiver acesso a ele; se não tiver,
  não é bloqueante.
- Configuração do worker nativo (`DEC-104`/`DEC-122`, Machine
  Environment, nunca `backend\.env`):
  [`scripts/manage_collection_worker_config.ps1`](../../scripts/manage_collection_worker_config.ps1)
  e
  [`docs/architecture/windows-collection-worker.md`](../architecture/windows-collection-worker.md).
- Setup integrado dos três componentes (visão de instalação, não de
  deploy): [`docs/installation/integrated-setup.md`](../installation/integrated-setup.md).
- Runbook operacional geral (rotina, diagnóstico, rollback pós-deploy):
  [`docs/operations/runbook.md`](runbook.md).
- Backup e restauração: [`docs/operations/backup-restore.md`](backup-restore.md).
- Estado vivo detalhado (histórico completo de decisões): `docs/internal/project-context.md` e `docs/internal/decision-log.md` (`DEC-109` a `DEC-119`).
