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
| GG Oferta | **`v1.3.2`** | `7710646` (`main`) | Substitui `v1.3.1` (que ainda dependia do repositório `cesar-core` no servidor — corrigido). Inclui o bundle `deploy/prod/`, este handoff corrigido, cota ADMIN/DEV=50, correção do Telegram |
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
repositórios que existem em PROD (`git checkout v1.3.2` — confirme que é
essa a mais recente com `git tag --sort=-creatordate` antes — no GG
Oferta, `v1.0.0` no Coupon Worker). O César Core **não tem repositório
em PROD**: use `deploy/prod/cesar-core.compose.yaml` (deste próprio
checkout do GG Oferta, já em `v1.3.2`), que já referencia `ghcr.io/
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

### GG Oferta (`backend/.env` nativo ou `.env` do Compose + `.secrets/`)

| Variável | Papel |
|---|---|
| `AISHOPPING_DEFAULT_MAX_ACTIVE_MISSIONS` | Cota de missões ativas USER — default de código `5`, não precisa de variável explícita a menos que se queira outro valor |
| `AISHOPPING_DEFAULT_MAX_ACTIVE_MISSIONS_ADMIN_DEV` | Cota de missões ativas ADMIN/DEV — **valor decidido: `50`**. Sem esta variável, ADMIN/DEV herda o default do USER (`5`) |
| `AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` | Flag F1 — default `false` |
| `AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` | Flag F3 — default `false` |
| `AISHOPPING_COUPONS_ENABLED` | Flag de consumo de cupons — default `false` |
| `AISHOPPING_CESAR_CORE_BASE_URL` | URL do César Core (ver "Portas/URLs aprovadas para PROD" abaixo) |
| `AISHOPPING_CESAR_CORE_API_KEY_FILE` | Arquivo com o Bearer do GG Oferta → César Core |
| `AISHOPPING_TELEGRAM_BOT_TOKEN_FILE` / `AISHOPPING_TELEGRAM_WEBHOOK_SECRET_FILE` | Secrets do bot (já existentes, sem mudança nesta rodada) |
| `AISHOPPING_DATABASE_*` | PostgreSQL do GG Oferta (já existente) |

Detalhe completo de todas as variáveis (não só as novas):
[`docs/installation/configuration.md`](../installation/configuration.md).

### César Core (`deploy/prod/.env` — lido pelo Compose por estar na mesma
pasta de `cesar-core.compose.yaml`, não é o `.env` de nenhum repositório
`cesar-core`, que não existe em PROD)

| Variável | Papel |
|---|---|
| `CESAR_CORE_AI_ENABLED` / `CESAR_CORE_SEARCH_ENABLED` | Opt-in — default `false`. Sem eles, `/v1/ai/generate`/`/v1/search` respondem "não configurado" mesmo com tudo mais no ar |
| `CESAR_CORE_AI_USER_MODEL` | Nome do combo OmniRoute para `ai_profile=user` — **`user-cascade`** |
| `CESAR_CORE_AI_ADMIN_DEV_MODEL` | Nome do combo OmniRoute para `ai_profile=admin_dev` — **`admin-dev-cascade`** |
| `CESAR_CORE_SECURITY_GG_OFERTA_API_KEY_FILE` | Caminho do arquivo de secret `application` (par do `AISHOPPING_CESAR_CORE_API_KEY_FILE` do GG) — default já aponta para `./cesar-core/.secrets/ggoferta-core-client`, só sobrescreva se usar outro caminho |
| `CESAR_CORE_OMNIROUTE_AI_API_KEY_FILE` / `_SEARCH_API_KEY_FILE` / `_FETCH_API_KEY_FILE` | Idem, para os secrets `omniroute_ai`/`omniroute_search`/`omniroute_fetch` (consumidor Core → OmniRoute, diferentes das 4 connections AI da seção 5.2) |
| `CESAR_CORE_SEARXNG_SECRET_FILE` | Idem, para o secret `searxng` |
| `CESAR_CORE_PUBLISHED_PORT` | Porta publicada em `127.0.0.1` — default `8100` |

Todas essas variáveis (e seus defaults) já estão explícitas em
`deploy/prod/cesar-core.compose.yaml` (`${VAR:-default}`) — só crie
`deploy/prod/.env` se precisar sobrescrever algum default; sem esse
arquivo, os defaults do próprio compose já bastam para AI/Search
desligados (estado inicial esperado, seção 7 passo 6).

### OmniRoute (dentro do volume do César Core, não é `.env`)

- Senha administrativa do painel (`/api/auth/login`).
- 4 chaves de provider AI: `gemini_user_api_key`,
  `gemini_admin_dev_api_key` (chave **diferente** da de USER),
  `groq_admin_dev_api_key`, `openrouter_admin_dev_api_key`.

**Gap conhecido, não coberto em detalhe por este handoff:** os 5
arquivos de secret que `deploy/prod/cesar-core.compose.yaml` monta
(`ggoferta-core-client`, `ggoferta-ai`, `ggoferta-search`,
`ggoferta-fetch`, `searxng`) precisam existir com valores reais de PROD
antes do passo 5.1 — o Bearer `ggoferta-core-client` é o mesmo valor
usado em `AISHOPPING_CESAR_CORE_API_KEY_FILE` do lado do GG Oferta (uma
decisão sua, gere um valor forte para os dois lados); já `ggoferta-ai`/
`ggoferta-search`/`ggoferta-fetch` são credenciais **emitidas pelo
próprio OmniRoute** (`api_keys`, escopo de consumidor) depois que ele já
estiver no ar — ou seja, existe uma dependência circular parcial (o
Core precisa dessas chaves para subir "completo", mas o OmniRoute
precisa estar no ar para emiti-las). Se você não tiver um procedimento
documentado para essa emissão específica, **pare e reporte** em vez de
inventar um — não é coberto por este handoff nem pelo runbook de
provisionamento de AI (que cobre só as 4 connections de provider, uma
coisa diferente).

Nomes de secret, procedimento completo e corpo de requisição exatos:
seção 5 deste documento e
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

### Geração de secrets locais na primeira instalação

Estes dois secrets **não existem em nenhum repositório** e precisam ser
gerados uma vez, diretamente no servidor, na primeira instalação. Em
nenhum dos dois casos você deve inventar/digitar manualmente um valor,
nem exibir o valor gerado em log, relatório ou chat.

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

Com os dois secrets acima já gerados e o restante de
`deploy/prod/cesar-core/.secrets/` provisionado (as 4 chaves de AI
seguem sendo criadas **dentro do OmniRoute** via API administrativa,
não como arquivo — ver seção 5.2; os secrets de arquivo aqui são só os
que o Compose monta: `ggoferta-core-client`, `ggoferta-ai`,
`ggoferta-search`, `ggoferta-fetch`, `searxng`, mesmos nomes/convenção
do `README.md` do `cesar-core`):

```powershell
docker compose -f deploy\prod\cesar-core.compose.yaml up -d
docker compose -f deploy\prod\cesar-core.compose.yaml ps
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

Nenhum `docker build`, nenhum clone do repositório `cesar-core` — só
`docker compose pull`/`up` sobre a imagem já publicada e verificada.

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
   secrets locais") — `verification_code_pepper` e `INITIAL_PASSWORD`
   do OmniRoute, se ainda não existirem.
6. **César Core + OmniRoute + Redis + SearXNG** via o bundle
   `deploy/prod/cesar-core.compose.yaml` (seção 5.1) — subir/atualizar
   antes do GG Oferta, já que ele é dependência obrigatória
   (fail-closed) de AI/Search/enrichment. Confirmar `GET /health` e
   `GET /ready` antes de prosseguir. **Nenhum clone/build do repositório
   `cesar-core`.**
7. **GG Oferta** (api + `collection_worker` + `telegram_notifier`) —
   com todas as flags da seção 6 **ainda OFF** neste ponto. Confirmar
   `GET /health` e `GET /ready`.
8. **Coupon Worker — primeira instalação em PROD** (seção 9) — diretório,
   `.env` (`AUTH_TOKEN` + `COUPONS_POSTGRES_DSN` apontando para o
   Postgres de PROD), instalar o agendamento (Windows Scheduled Task).
   Pode subir a qualquer momento depois do passo 4; não depende do
   César Core.
9. **Health/readiness dos três** — confirmar antes de tocar em
   qualquer flag (tabela na seção 8 abaixo).
10. **Flags OFF, prova básica** — com tudo no ar e flags ainda
    desligadas, confirmar que o comportamento é idêntico ao anterior a
    este deploy inteiro (nenhuma regressão visível com tudo desligado).
11. **Ativação gradual** — seguir a ordem da seção 6, uma flag por vez,
    com prova real entre cada uma.
12. **Gate Gemini** (seção 11) — antes especificamente da política de
    providers AI valer para tráfego real, não só para as flags de
    F1/F3/cupons.
13. **Prova funcional final** — um ciclo completo real: criar/observar
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

## 13. Referências (mesma arquitetura, sem redesenho)

- Bundle de deploy do César Core (Compose, sem clonar o repositório):
  [`deploy/prod/cesar-core.compose.yaml`](../../deploy/prod/cesar-core.compose.yaml).
- Runbook de provisionamento OmniRoute (cópia local, sem clonar o
  repositório): [`deploy/prod/omniroute-provisioning.md`](../../deploy/prod/omniroute-provisioning.md).
- Arquitetura canônica GG ↔ Core (background/racional -- não é
  operacionalmente necessário para executar este deploy, os dois itens
  acima já bastam): `docs/architecture/gg-oferta-core.md` no
  repositório `cesar-core`, se você tiver acesso a ele; se não tiver,
  não é bloqueante.
- Setup integrado dos três componentes (visão de instalação, não de
  deploy): [`docs/installation/integrated-setup.md`](../installation/integrated-setup.md).
- Runbook operacional geral (rotina, diagnóstico, rollback pós-deploy):
  [`docs/operations/runbook.md`](runbook.md).
- Backup e restauração: [`docs/operations/backup-restore.md`](backup-restore.md).
- Estado vivo detalhado (histórico completo de decisões): `docs/internal/project-context.md` e `docs/internal/decision-log.md` (`DEC-109` a `DEC-119`).
