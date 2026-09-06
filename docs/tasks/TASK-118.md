# TASK-118 — Integrar o OmniRoute como camada central de roteamento de IA e Web Search

**Estado atual:** 118A–118G concluídas; 118F/118G aprovadas, commitadas e
publicadas. SearXNG certificado em DEV; Search e enriquecimento separados.
118H com validação DEV concluída, aguardando revisão (`TASK-118H.md`).
Flags continuam desligadas por padrão; nenhum rollout PROD autorizado.

O texto abaixo preserva o histórico do pré-flight original, não o status
atual das etapas já entregues.

Status: **Pré-flight fechado — zero decisões arquiteturais bloqueadoras
(2026-08-30).** Fonte oficial confirmada, contratos reais extraídos do
código, arquitetura completa decidida: `GG Oferta →
AIProviderManager/WebSearchManager → César Core → OmniRoute`
(repositório `cesar-core` novo e separado), protocolo, escopo,
contrato de Web Search, credenciais, secrets, fallback de migração,
contrato de qualidade/capacidade (Policy Layer), política de custo,
topologia PROD V1 (três deployments independentes no mesmo host),
exposição pública (nenhuma nova) e supervisão/lifecycle — todos
fechados (`DEC-107`, §4 a §10). Restam só detalhes de implementação
(esquema exato de payload, mecanismo de deploy do César Core no
Windows), nunca decisão arquitetural. Nenhum código, configuração,
secret ou dependência deste repositório alterados — implementação
segue não iniciada, aguardando autorização explícita pra começar.

## Pré-flight §1 — Fonte oficial e referência de commit

**Regra de fonte, fixada explicitamente pelo usuário**: só o repositório
oficial conta como referência arquitetural — nunca forks, mirrors ou
repositórios de terceiros com o mesmo nome. Em caso de conflito entre
site, wiki e código: (1) código do repositório oficial vence; (2)
documentação do próprio repositório oficial; (3) wiki oficial; (4) site
oficial.

- **Repositório oficial**: `https://github.com/diegosouzapw/OmniRoute`
  (confirmado como o projeto real por cross-referência direta com o site
  oficial `https://omniroute.online`, que linka e promove explicitamente
  esse repositório — 58.396 estrelas no momento da consulta). Wiki
  oficial: `https://github.com/diegosouzapw/OmniRoute/wiki`.
- **Outro repositório com o mesmo nome** (`ChrisCompton/omniroute`)
  apareceu na pesquisa inicial — tratado como **não-oficial**, descartado
  como referência, por instrução explícita do usuário e por não ter
  nenhuma associação com o site oficial.
- **Commit exato analisado**: `1f4dc830f3290a5507b5350417ae1547f825aefc`,
  branch `release/v3.8.51` (branch padrão do repositório), pushed em
  `2026-08-30T13:39:01Z`. Clonado localmente (fora deste repositório,
  numa pasta de investigação separada) pra leitura direta do código-fonte
  e do `docs/openapi.yaml` oficial (spec OpenAPI real, não resumo de
  terceiro). **Este commit é a referência arquitetural fixada para todo
  o resto deste pré-flight** — qualquer achado abaixo cita o caminho
  exato do arquivo dentro desse commit.

## Pré-flight §2 — Contratos reais extraídos do código/spec oficial

Tudo abaixo vem direto de `docs/openapi.yaml` (spec OpenAPI oficial do
repositório, ~9.670 linhas) e dos guias em `docs/` do próprio
repositório — nunca de resumo de blog/terceiro, nunca de suposição.

### Autenticação (`docs/guides/MANAGEMENT-AUTH.md`, `docs/openapi.yaml` `components.securitySchemes`)

Quatro famílias de credencial, **não intercambiáveis**:

| Credencial | Forma | Uso pretendido pro GG Oferta |
|---|---|---|
| Sessão JWT do dashboard | cookie `auth_token` | Não se aplica (é humano logando no dashboard) |
| Token de máquina do CLI | interno | Não se aplica |
| Access Token (`oma_live_…`) | Settings → Access Tokens | Não se aplica (é pra CLI/gestão remota) |
| **Chave de API de inferência** | `sk-…`, sem escopo `manage`/`admin` | **Esta é a credencial certa** — só autoriza `/v1/*` (chat, search, embeddings etc.), nunca rotas de gestão. Criada em Dashboard → API Manager / API Keys. |

Header: `Authorization: Bearer sk-<secret>`. Mesmo padrão de secret que o
projeto já usa (`*_FILE`, nunca em `.env`/Git) se encaixa aqui — a chave
de inferência do OmniRoute vira só mais um `AISHOPPING_OMNIROUTE_API_KEY_FILE`.

### IA — `POST /api/v1/chat/completions` (`docs/openapi.yaml`, contrato em torno da linha 1360)

- Compatível com OpenAI (`ChatCompletionRequest`/`ChatCompletionResponse`).
- Resposta traz headers reais de observabilidade prontos pra uso:
  `X-OmniRoute-Response-Cost` (custo em USD, 10 casas decimais),
  `X-OmniRoute-Tokens-In`/`-Tokens-Out`, `X-OmniRoute-Model`,
  `X-OmniRoute-Provider` (provider resolvido de verdade), `X-OmniRoute-
  Latency-Ms`, `X-OmniRoute-Cache-Hit`, `X-OmniRoute-Fallback-Attempts`,
  `X-OmniRoute-Decision` (trace da decisão de roteamento: estratégia +
  provider + latência), `X-OmniRoute-Cost-Saved` (em cache hit). Isso
  resolve praticamente sozinho o requisito de observabilidade do
  `AI Provider Manager` (distinguir provider/modelo/motivo de
  fallback/rota gratuita vs. paga) sem precisar reconstruir nada.
- Erros: `401` (`Unauthorized`), `502` quando **todos** os providers
  upstream falharem.

### Web Search — `POST /api/v1/search` (`docs/openapi.yaml`, contrato em torno da linha 1165)

- `GET /api/v1/search` lista providers de busca configurados e seus
  `search_types` (`web`/`news`/`x`).
- `POST /api/v1/search`: `query` (obrigatório), `provider` (opcional, id
  ou alias), `max_results` (1-100, default 5), `search_type`
  (`web`/`news`/`x`, default `web`), `offset`, `country`, `language`,
  `time_range` (`any`/`hour`/`day`/`week`/`month`/`year`), `content`
  (snippet/full_page/format md-text/max_characters), `filters`
  (`include_domains` etc.).
- **Fallback real confirmado no código + testes (correção 2026-08-30 —
  a primeira leitura, baseada só na prosa do `openapi.yaml`, estava
  errada e foi corrigida por pedido explícito do usuário)**: ver seção
  dedicada "Web Search — fallback real, confirmado no código" abaixo.
- Confirmado também em `docs/frameworks/SEARCH_TOOLS_STUDIO.md`
  (`POST /v1/search`, "existing endpoint, no changes") e em
  `src/app/api/search/providers/route.ts` (catálogo de providers com
  `kind: "search" | "fetch"` e `status: "configured" | "missing" |
  "rate_limited"` derivado ao vivo das credenciais).
- Scraping avançado tem endpoint próprio, separado da busca:
  `POST /v1/web/fetch` (`src/app/api/v1/web/fetch/route.ts`), com
  providers incluindo `firecrawl` — o próprio OmniRoute já reconhece
  Firecrawl como um dos seus providers de fetch, o que é coerente com o
  plano da TASK-118 de manter o Firecrawl só pra scraping avançado.

### Web Search — fallback real, confirmado no código (correção 2026-08-30)

Fonte: `src/app/api/v1/search/route.ts` (428 linhas, handler completo
de `POST /v1/search`), `open-sse/config/searchRegistry.ts` (definição
de `SEARCH_PROVIDERS`), `tests/unit/search-handler-duckduckgo.test.ts`
e `tests/unit/search-route.test.ts`, todos no commit
`1f4dc830f3290a5507b5350417ae1547f825aefc`. **Sem inferência** — cada
afirmação abaixo é literal do código ou de um teste que passa.

**Todos os providers de `SEARCH_PROVIDERS` e como se comportam:**

| Provider | `authType` | `fallbackOnly` | Observação real |
|---|---|---|---|
| `serper-search`, `brave-search`, `perplexity-search`, `exa-search`, `tavily-search`, `nimble-search`, `firecrawl`, `google-pse-search`, `linkup-search`, `searchapi-search`, `youcom-search`, `ollama-search`, `zai-search`, `jina-search` | `apikey` | não | **Auto-selecionáveis** — elegíveis pra seleção automática por custo quando `provider` não é informado no request |
| `x-search` | `apikey` | não | Só `search_type: "x"`, nunca busca web genérica |
| `searxng-search` | `none` | **sim** | Aponta pra `http://localhost:8888/search` — precisa de uma instância SearXNG própria rodando; `authType: none` só quer dizer "sem chave de API", não "sem dependência" |
| `context7` | `none` | **sim** | Comentário explícito no código: "doc-focused corpus, **never** auto-selected for generic web search" — busca de documentação de biblioteca, não é fallback de busca web genérica |
| **`duckduckgo-free`** | **`none`** | **sim** | **Este é o fallback real, zero-configuração.** Comentário do código: "Free, no-API-key DuckDuckGo lite scraping... Last-resort only". `route.ts` linha ~265: quando nenhum provider credenciado está disponível, o código promove `duckduckgo-free` e define `credentials = {}` diretamente (nunca passa por resolução de credencial) |
| `xquik-search` | `apikey` | sim | Só `search_type: "x"` (Twitter/X), exige API key própria |
| `anysearch-search` (AnySearch) | **`apikey`** | sim | `costPerQuery: 0` (gratuita) e free tier de 1000 req/dia — **mas ainda exige uma API key configurada** (`authHeader: "bearer"`). **Não é um fallback zero-configuração** — a primeira leitura deste pré-flight (baseada só na prosa do `openapi.yaml`, "AnySearch provides free fallback-only web search") estava **incorreta** nesse ponto específico; corrigido agora contra o código real |

**Comportamento exato sem nenhuma credencial de busca configurada**
(`route.ts`, ramo `else` de auto-seleção, linhas ~209-276): tenta o
provider auto-selecionado por custo → itera os demais não-`fallbackOnly`
por custo crescente → se nenhum tiver credencial, itera os
`fallbackOnly` por custo crescente e, ao chegar em `duckduckgo-free`,
usa direto (`credentials = {}`, sem checar credencial nenhuma) — **é o
único provider que garante funcionar sem qualquer configuração prévia**.
`context7` fica de fora dessa iteração de propósito (corpus de
documentação, não busca web).

**Testes reais que prova isso** (`tests/unit/search-handler-duckduckgo.test.ts`):
- `"handleSearch fulfills duckduckgo-free via the HTML scraping path (no API key)"`
- `"handleSearch fails over to duckduckgo-free when the primary provider errors"`

`tests/unit/search-route.test.ts` linha ~429: comentário confirmando que,
sem provider configurado, a resposta não é erro 400 — "it promotes the
fallback-only duckduckgo-free provider so out-of-the-box [search
works]", com asserção de que a chamada de fallback bate no endpoint real
do DuckDuckGo lite.

**Alternate provider (failover pós-seleção)**: além do fallback de
"nenhuma credencial", o handler também escolhe um `alternateProviderId`
(linhas ~291-327) pra failover se o provider principal falhar em tempo
de execução — segue a mesma prioridade (não-`fallbackOnly` por custo
primeiro, só cai pra `duckduckgo-free`/outros `fallbackOnly` como
último recurso).

### Health — `GET /api/health` (não-autenticado) e `GET /api/monitoring/health` (autenticado)

`GET /api/health`: liveness probe simples, `{status: "ok", timestamp}`,
sem autenticação, `Cache-Control: no-store`. Versão/uptime/memória ficam
só no `/api/monitoring/health` autenticado.

### Fallback e resiliência — `docs/architecture/RESILIENCE_GUIDE.md` + `GET/POST/DELETE /api/fallback/chains`

O OmniRoute já implementa **3 camadas de resiliência prontas no
servidor**, documentadas com caminho de código real:

1. **Circuit breaker por provider inteiro** (`src/shared/utils/
   circuitBreaker.ts`) — estados `CLOSED`/`DEGRADED`/`OPEN`/`HALF_OPEN`,
   dispara só em `[408, 500, 502, 503, 504]` (nunca em 401/403/429, que
   são erro de conta, não do provider).
2. **Cooldown por conexão/chave individual** (`src/sse/services/
   auth.ts`) — backoff exponencial, respeita `Retry-After` real do
   upstream quando presente.
3. **Model lockout** (camada 3, `RESILIENCE_GUIDE.md` §3).

Isso significa que boa parte do "mecanismo de fallback quando o
OmniRoute está indisponível" (risco #2 original) já existe *dentro* do
OmniRoute pros providers que ele gerencia — o que falta decidir é só o
fallback pra quando o **OmniRoute inteiro** está fora do ar (self-hosted,
pode cair como qualquer serviço próprio).

`GET/POST/DELETE /api/fallback/chains` permite registrar/consultar/
remover cadeias de fallback por modelo (`{model, chain: [{provider,
priority, enabled}]}`) — configuração explícita, não caixa-preta.

### Erros — formato uniforme (`ApiErrorResponse`, `docs/openapi.yaml` ~linha 9196)

```json
{"error": {"message": "...", "type": "...", "details": "opcional"}, "requestId": "uuid"}
```

### Deploy/self-hosted (`docs/getting-started/QUICK-START.md`)

O OmniRoute é **self-hosted** — não é uma API SaaS de terceiro chamada
remotamente, é uma peça de infraestrutura própria. Três formas oficiais
de instalar: `npm install -g omniroute`; **Docker** (`docker run -d
--name omniroute -p 20128:20128 diegosouzapw/omniroute:latest`, imagem
oficial publicada, `diegosouzapw/omniroute:X.Y.Z` pra pin de versão);
build a partir do código-fonte. Sobe em `http://localhost:20128`,
dashboard incluído. É um serviço HTTP comum — sem necessidade de sessão
interativa Windows nem navegador real (diferente do `collection_worker`).

**Correção de arquitetura (2026-08-30, por instrução explícita do
usuário — a leitura anterior deste documento, que dizia "se encaixa
diretamente no `compose.yaml` já existente" do GG Oferta, estava
errada):**

```
GG Oferta
  → AIProviderManager / WebSearchManager
  → César Core
  → OmniRoute
```

- **OmniRoute é infraestrutura central compartilhável** — não fica
  arquiteturalmente acoplado ao `compose.yaml`/repositório do GG Oferta.
- **`cesar-core` é um repositório novo, separado**
  (`https://github.com/jhonnatancesar/cesar-core.git`, confirmado
  existente e ainda vazio via `git ls-remote` em 2026-08-30 — mesmo
  padrão já aplicado ao Coupon Collector, `DEC-105`: repositório
  próprio, nunca misturado ao GG Oferta) — é o **dono da integração com
  o OmniRoute**.
  Todo o código que fala HTTP direto com `/api/v1/chat/completions`,
  `/api/v1/search` etc. vive em `cesar-core`, nunca em
  `backend/app/ai_provider/` deste repositório.
- **GG Oferta só terá adapters/client do César Core** — o
  `AIProviderManager`/`WebSearchManager` (este repositório) chama o
  César Core, nunca o OmniRoute diretamente. O guardrail já vigente
  ("Não conectar módulos diretamente a Gemini, OpenAI ou Claude") passa
  a valer igualmente pra "não conectar módulos diretamente ao
  OmniRoute" — só o César Core fala com o OmniRoute.
- **Implantação pode coexistir no mesmo host físico** desde o início
  (ex.: mesmo Windows Server), mas **serviços, repositórios e arquivos
  compose permanecem claramente separados** — três repositórios/
  processos distintos (GG Oferta, César Core, OmniRoute), nunca um
  `compose.yaml` único misturando os três.
## Pré-flight §4 — Decisões fechadas pela arquitetura do César Core (aprovadas pelo usuário, 2026-08-30)

Estas cinco decisões **encerram** os pontos 2 (parcialmente), 4, 5
(parcialmente) e 6 das "7 perguntas do documento original" (seção
histórica abaixo) e o protocolo GG Oferta ↔ César Core que ficara em
aberto na correção de arquitetura anterior. Registradas aqui como
decisão do usuário, não inferidas.

### 1. Protocolo GG Oferta ↔ César Core

**HTTP interno com API versionada** — nunca biblioteca Python
compartilhada como contrato entre repositórios. Motivo explícito: César
Core é processo/repositório independente e precisa poder servir
futuramente aplicações implementadas em outras stacks (não só Python).

Exemplo de superfície:
```
POST /v1/ai/generate
POST /v1/search
GET  /health
GET  /ready
GET  /v1/capabilities
```

Fila **não é** o transporte padrão pra IA/Search síncrono. Fila pode
ser adicionada futuramente só para operações assíncronas específicas —
não substitui o HTTP síncrono.

### 2. Escopo do César Core

**Multi-aplicação desde o nascimento** — não é um serviço interno
exclusivo do GG Oferta que por acaso vive num repositório separado.
Registro de aplicações desde o início: `gg_oferta = ACTIVE`,
`claudiao = RESERVED / NOT_CONFIGURED`. GG Oferta é só o **primeiro
consumidor**, não o único previsto. Esta pergunta está fechada — não
deve ser reaberta em rodadas futuras.

### 3. Contrato de Web Search

**Contrato central genérico e neutro** — o César Core **não conhece**
conceitos de domínio do GG Oferta (`MarketPriceAssessment`, `Offer`,
`Product`, `Mission` etc.). É o **GG Oferta** quem traduz sua
necessidade de negócio pro contrato genérico do César Core (query,
filtros, tipo de busca), nunca o inverso. Isso também fecha a
granularidade da abstração de Web Search: existe uma camada própria no
GG Oferta (`WebSearchManager`, paralela ao `AI Provider Manager`) que
faz essa tradução antes de chamar o César Core.

### 4. Credenciais de provider (Gemini/Groq/OpenRouter/etc.)

**Estado arquitetural final**: essas credenciais ficam configuradas no
**OmniRoute**, não no GG Oferta. Depois da migração completa, o GG
Oferta **não deve continuar armazenando permanentemente** credenciais
individuais desses providers.

**Durante o rollout**: as credenciais/implementações antigas (a cascata
gratuita atual, `CLAUDE.md`) podem permanecer **temporariamente** como
rollback/disaster fallback — documentado explicitamente aqui como
**estado de transição, nunca arquitetura final**. Isso também fecha a
pergunta 5 original (reconciliação com a cascata gratuita atual): a
cascata direta vira mecanismo transitório de rollback, removível por
feature flag, não uma peça permanente da arquitetura.

### 5. Secrets do César Core

César Core tem secrets **próprios**, separados dos do GG Oferta.
Convenção V1: arquivos locais, suporte `*_FILE` (mesmo padrão já usado
neste repositório), ACL apropriada, nunca em Git, nunca em log, **nunca
compartilhar o diretório de secrets do GG Oferta** (`.secrets\` deste
repositório fica exclusivo dele).

## Pré-flight §5 — Arquitetura de fallback (migração e estado final)

### Durante a migração (rollout)

```
GG Oferta
  → AIProviderManager
  → CesarCoreAIProvider
  → César Core
  → OmniRoute
  → providers
```

Se o César Core/OmniRoute estiver **estruturalmente indisponível**, o
`AIProviderManager` pode usar **temporariamente** a cascata direta
antiga (Gemini → Groq → OpenRouter) — serve só como rollback/disaster
fallback durante o rollout, removível por feature flag.

**Regra explícita contra fallback duplicado**: se o OmniRoute respondeu
e já executou seu próprio fallback interno (3 camadas, §2 acima), o GG
Oferta **não** deve repetir Gemini/Groq/OpenRouter de novo por cima —
isso duplicaria tentativas e mascararia qual camada realmente falhou.

Mesma filosofia pra Search durante o rollout:
```
CesarCoreSearchProvider
  → falha estrutural do César Core/OmniRoute
  → FirecrawlSearchProvider direto (transitório/rollback, não arquitetura final)
```

### Estado final desejado (pós-migração)

```
GG Oferta
  → AIProviderManager
  → César Core
  → OmniRoute
  → providers
```

Sem `CesarCoreAIProvider` como camada de fallback-para-cascata-antiga
nomeada — a cascata direta antiga deixa de existir como caminho de
produção normal, só resta enquanto o feature flag de rollback estiver
ativo. O mesmo vale para Search (sem `FirecrawlSearchProvider` direto
como caminho padrão).

## Pré-flight §3 — Estado das 7 perguntas do documento original

*(Histórico — ver §4/§5 acima para o estado real e definitivo de cada
uma após a arquitetura do César Core ser aprovada, 2026-08-30. Não
reabrir as que já constam fechadas ali.)*

1. **O que é o OmniRoute de verdade** — ✅ fechada (§1/§2).
2. **Fallback quando o OmniRoute está indisponível** — ✅ fechada (§5).
3. **Como declarar "requisito mínimo de qualidade/capacidade" por
   chamada** — **segue em aberto**, ver "O que realmente continua em
   aberto" abaixo.
4. **Granularidade da abstração de Web Search** — ✅ fechada (§4.3):
   `WebSearchManager`, módulo próprio paralelo ao `AI Provider Manager`.
5. **Reconciliação com a cascata gratuita atual** — ✅ fechada (§4.4):
   vira mecanismo transitório de rollback, não permanente.
6. **Onde ficam as chaves centralizadas no OmniRoute** — ✅ fechada
   (§4.4/§4.5): providers individuais ficam no OmniRoute; César Core
   guarda só sua própria chave de inferência, secrets próprios (§4.5).
7. **Critério de fuzzy/IA vs. regra determinística** — inalterado,
   guardrails já vigentes do projeto continuam valendo; o OmniRoute/
   César Core não influencia essa decisão (nunca foi uma pergunta real
   sobre a integração, é só uma reafirmação).

## Pré-flight §6 — Contrato de qualidade/capacidade (Policy Layer)

Fecha o ponto 1 de "O que realmente continua em aberto" (revisão
anterior). Decisão do usuário, 2026-08-30.

**O consumidor (GG Oferta) nunca escolhe provider/model.** O contrato
com o César Core recebe só requisitos **neutros**, separados em quatro
eixos:

- `application` — quem está chamando (`gg_oferta`, futuramente
  `claudiao`).
- `service` — qual serviço interno do consumidor está chamando (ex.:
  `market_research`, `product_identity`).
- `purpose` — pra que serve a chamada (ex.: `search_grounding`,
  `intent_interpretation`, `canonicalization`).
- `requirements` — capacidades necessárias, nunca provider/model:
  `structured_output`, `reasoning`, `vision`, `tool_calling`.

Mais um eixo ortogonal, **`service_class`** neutro: `economy` |
`standard` | `quality`.

A combinação `application + purpose + requirements + service_class` é
resolvida pela **Policy Layer** (dentro do César Core) — nunca pelo GG
Oferta. GG Oferta nunca solicita diretamente Gemini/Groq/Claude ou um
model id específico. **O contrato precisa continuar válido pro futuro
consumidor "Claudião"** — por isso é genérico por `application`, nunca
hardcoded pro GG Oferta.

## Pré-flight §7 — Política de custo

Fecha o ponto 2 de "O que realmente continua em aberto" (revisão
anterior). Decisão do usuário, 2026-08-30.

**César Core é a autoridade que sabe QUANTO cada `application` pode
gastar** — não o GG Oferta, não o OmniRoute.

Classes de política: `FREE_ONLY` | `FREE_PREFERRED` | `PAID_ALLOWED`.
A política de uma `application`/`purpose` pode carregar
`max_cost_per_request`, `daily_budget`, `monthly_budget`.

**Não é necessário implementar toda a gestão financeira nesta rodada
("118A")** — só o contrato/modelo arquitetural precisa estar preparado
antes de fechar o pré-flight.

**Divisão de responsabilidade, explícita e não-negociável:**

| Responsabilidade | Dono |
|---|---|
| `application` authorization, `purpose` policy, cost permission, limits, `requirements` | **César Core** |
| Provider availability, quota, cooldown, circuit breaker, provider fallback, routing | **OmniRoute** |

César Core **não reimplementa fallback provider por provider** — isso
já existe dentro do OmniRoute (3 camadas de resiliência, §2 acima).
César Core só decide a política **permitida** (`service_class` +
orçamento) e traduz isso pro route/profile/combo apropriado do
OmniRoute (o conceito de `combo` — `/api/combos`, estratégias
`priority`/`weighted`/`fusion` — já existe no OmniRoute real, confirmado
no pré-flight §2; César Core não inventa um mecanismo de roteamento
paralelo, só aciona o que já existe com os parâmetros certos).

## Pré-flight §8 — Topologia PROD V1

Fecha o ponto 3 de "O que realmente continua em aberto" (revisão
anterior). Decisão do usuário, 2026-08-30.

**Estado inicial: mesmo Windows Server físico, três deployments
independentes:**

```
C:\App\AIShoppingAgent   (GG Oferta -- repo, config, secrets, lifecycle próprios)
C:\App\cesar-core        (César Core -- repo, config, secrets, lifecycle próprios)
C:\App\omniroute         (OmniRoute -- repo/imagem, config, secrets, lifecycle próprios)
```

GG Oferta e César Core **não compartilham `compose.yaml`**. OmniRoute
permanece serviço/deployment separado dos outros dois. Rede Docker
externa compartilhada, conceitualmente `cesar-platform`, pra comunicação
entre containers dos três.

**O `collection_worker` do GG Oferta roda nativo no Windows (TASK-109,
fora do Docker)** — por isso o César Core também precisa expor um
endpoint acessível pelo **host local** (não só pela rede Docker
interna), nunca público na Internet.

Fluxo de comunicação:

```
Windows collection_worker (nativo)
  → localhost / endpoint interno do César Core

GG Oferta (containers)
  → rede interna (cesar-platform)
  → César Core

César Core
  → rede interna (cesar-platform)
  → OmniRoute
```

## Pré-flight §9 — Exposição pública

Decisão do usuário, 2026-08-30.

**Não criar** `core.ggoferta.com` nem `omniroute.ggoferta.com`. Nenhuma
API interna (César Core, OmniRoute) exposta via Cloudflare/Tunnel.
Internet continua só: `Internet → ggoferta.com → GG Oferta`. César Core
e OmniRoute são **infraestrutura interna**, sem rota pública própria.

## Pré-flight §10 — Supervisão / lifecycle

Decisão do usuário, 2026-08-30.

César Core e OmniRoute têm lifecycle **próprio**, cada um: container
com `restart: unless-stopped` + healthcheck.

Superfície de saúde do César Core: `GET /health`, `GET /ready`,
`GET /v1/capabilities` (já fixados no protocolo, §4.1). **`/ready`
precisa distinguir estados**, nunca um booleano único:

- Core (processo do César Core) vivo.
- OmniRoute alcançável (rede/health do OmniRoute responde).
- Capacidade de IA disponível.
- Capacidade de Search disponível.

Falha de **um** provider upstream individual (ex.: Gemini fora do ar)
**não significa** que o César Core inteiro está indisponível — o
OmniRoute já isola isso por provider (circuit breaker, §2); o `/ready`
do César Core só fica negativo quando a capacidade inteira (IA ou
Search) não tem nenhum caminho viável, não por um provider isolado
falhar.

## Decisões arquiteturais bloqueadoras da TASK-118 — estado final

**Vazia.** Todas as decisões arquiteturais que bloqueavam o início da
implementação (protocolo, escopo, contrato de Web Search, credenciais,
secrets, fallback, qualidade/capacidade, política de custo, topologia,
exposição, supervisão) estão fechadas (§4 a §10). Nenhuma incompatibilidade
real foi encontrada entre essas decisões e os contratos reais do
OmniRoute confirmados no pré-flight (§2) — em particular, o conceito de
`combo`/estratégia de roteamento que a Policy Layer do César Core
precisa acionar já existe de verdade no OmniRoute (`/api/combos`),
não é uma suposição.

Pontos que seguem como **detalhe de implementação** (não bloqueiam
começar, são resolvidos durante a implementação em si, não neste
pré-flight): esquema exato de request/response do contrato César Core
(`/v1/ai/generate`, `/v1/search` — campos concretos pra
`application`/`purpose`/`requirements`/`service_class`); como o César
Core traduz `service_class`/orçamento pro `combo` exato do OmniRoute;
mecanismo de deploy/supervisão do César Core no Windows (Task
Scheduler? Windows Service? — mesma família de decisão operacional já
resolvida pro `collection_worker`, TASK-109, mas ainda não replicada
aqui).

Nenhuma implementação de código, migration, secret ou config foi feita
nesta rodada — só leitura/registro do pré-flight, conforme pedido.

## Objetivo

Reduzir o acoplamento direto do GG Oferta com múltiplos providers de IA
e mecanismos de Web Search — chaves, quotas, fallback e escolha de
modelo hoje resolvidos internamente — centralizando essa responsabilidade
no OmniRoute. O `AI Provider Manager` (`backend/app/ai_provider/`)
continua sendo a única porta de acesso interna à IA (guardrail já
vigente em `CLAUDE.md`: "Não conectar módulos diretamente a Gemini,
OpenAI ou Claude"), mas passa a poder ser implementado sobre o OmniRoute
em vez de sobre cada provider individualmente — sem o GG Oferta ficar
arquiteturalmente dependente do OmniRoute.

## Arquitetura pretendida

*(Desenho original abaixo, mantido por histórico — **superado pela
correção de arquitetura em "Pré-flight §2 — Deploy/self-hosted" acima**:
falta a camada `César Core` entre o GG Oferta e o OmniRoute. Ver aquela
seção para o desenho corrigido e aprovado.)*

```
IA:
  GG Oferta → AI Provider Manager → OmniRoute → providers/modelos

Web Search:
  GG Oferta → abstração interna de Web Search → OmniRoute → mecanismo de busca

Scraping avançado (quando Web Search não basta):
  GG Oferta → Firecrawl (mantido, uso reduzido e específico)
```

- `AI Provider Manager` continua existindo como abstração interna
  (pode ser simplificado, nunca removido) — o domínio nunca chama um
  provider ou o OmniRoute diretamente.
- Nova abstração interna de Web Search (equivalente ao papel do `AI
  Provider Manager` para IA) — o domínio nunca chama a API do OmniRoute
  diretamente para busca.
- OmniRoute concentra: múltiplas API keys já usadas pelo projeto,
  providers gratuitos/no-auth, providers com free tier, APIs pagas,
  fallback, quota/rate limit, disponibilidade/health, escolha de
  provider/modelo.
- Política de custo: (1) gratuito/no-auth adequado; (2) free tier
  configurado; (3) pago só como último recurso — nunca `auto`
  irrestrito quando a chamada tiver requisito mínimo de
  qualidade/capacidade que o GG Oferta precise declarar.
- Firecrawl permanece só para scraping/extração avançada de página
  específica — nunca mais para Web Search genérica, reduzindo consumo
  de créditos. Remoção do que for exclusivo de Web Search só depois de
  migração e validação real.

## Componentes afetados (previstos, a confirmar no pré-flight)

- `backend/app/ai_provider/` (`manager.py`, providers individuais,
  contratos) — possível simplificação, nunca reescrita completa.
- `backend/app/search/firecrawl.py` e qualquer chamador que hoje use
  Firecrawl só para busca (ex.: `market_research`, TASK-113).
- Nova abstração de Web Search (módulo a definir no pré-flight).
- `backend/app/core/config.py`/secrets — chaves hoje individuais por
  provider deixam de existir permanentemente aqui (`DEC-107`, §4.4);
  substituídas por uma única credencial de acesso ao **César Core**
  (nunca ao OmniRoute diretamente), mantendo o padrão `*_FILE` já
  vigente. As credenciais individuais atuais podem continuar existindo
  temporariamente como rollback (§5), atrás de feature flag.
- Observabilidade/telemetria de chamadas de IA e busca já existente
  (`app/observability/`) — precisa continuar distinguindo provider,
  modelo, motivo de fallback, rota gratuita vs. paga, e chamadas que
  ainda usaram Firecrawl.

## Critérios de aceite

- `AI Provider Manager` continua sendo o único ponto de acesso interno
  à IA; nenhum módulo de domínio passa a chamar o César Core, o
  OmniRoute ou um provider diretamente (`DEC-107`, §4.1).
- Existe uma abstração interna própria para Web Search
  (`WebSearchManager`), com a mesma disciplina de não vazar o contrato
  do César Core/OmniRoute para o domínio.
- Política de custo (gratuito → free tier → pago) é respeitada e
  observável — dá para saber, por chamada, qual rota foi usada e por
  quê. Mecanismo definido no contrato César Core (§7: `FREE_ONLY`/
  `FREE_PREFERRED`/`PAID_ALLOWED` + orçamentos, César Core traduz pro
  `combo` do OmniRoute).
- Firecrawl deixa de ser usado para Web Search genérica; uso
  remanescente é só scraping avançado, auditável e reduzido em volume
  real (não só em teoria). `FirecrawlSearchProvider` direto vira só
  rollback transitório (§5), não caminho padrão.
- Fallback seguro quando o César Core/OmniRoute está indisponível —
  arquitetura decidida em §5 (rollback temporário pra cascata antiga,
  removível por feature flag, sem fallback duplicado quando o OmniRoute
  já executou o próprio fallback interno).
- PROD e DEV continuam com credenciais e dados completamente separados;
  nenhuma chave em código/repositório/log/Telegram. Secrets do César
  Core nunca compartilham diretório com os do GG Oferta (`DEC-107`, §4.5).
- Migração incremental: César Core/OmniRoute integrado atrás das
  abstrações existentes primeiro, validado com uso real, só depois
  remoção do fallback transitório antigo (por feature flag).

## Riscos e pontos a decidir no pré-flight

*(Lista original, mantida por histórico — ver "Pré-flight §3/§4/§5"
acima para o estado real e definitivo de cada uma, 2026-08-30. A maioria
já está fechada; não reabrir.)*

1. O que exatamente é o OmniRoute (API real, autenticação, contrato de
   requisição/resposta, SDK oficial ou HTTP direto) — nada disso pode
   ser assumido de memória; precisa de documentação oficial atual,
   mesma disciplina já usada para o Cloudflare Access na TASK-117.
2. Mecanismo de fallback quando o OmniRoute está indisponível: cascata
   local de emergência (ex.: a cascata gratuita já vigente hoje,
   `CLAUDE.md`) vs. falha explícita — decisão arquitetural, não
   implementação prematura.
3. Como declarar "requisito mínimo de qualidade/capacidade" por chamada
   sem reintroduzir a complexidade que o OmniRoute deveria absorver.
4. Granularidade da nova abstração de Web Search: módulo novo dedicado
   vs. extensão do `AI Provider Manager` — nenhuma decisão tomada aqui.
5. Como a política gratuita atual do projeto (`CLAUDE.md`: cascata
   Gemini → Groq → OpenRouter para USER/DEV, ADMIN compartilhando a
   mesma política) se reconcilia com o OmniRoute decidindo a rota —
   quem manda em quê precisa ficar explícito antes de codificar.
6. Onde e como as chaves centralizadas no OmniRoute ficam configuradas
   no GG Oferta (só credencial de acesso ao OmniRoute) vs. o que
   eventualmente eventualmente precisa continuar local.
7. Critério exato para permitir/proibir uso de fuzzy/IA em decisões que
   já têm regra determinística no domínio — reafirmar, nunca
   flexibilizar, guardrails já vigentes (ex.: Product Identity Engine).

## Estratégia de migração (alto nível, sem interrupção)

*(Arquitetura concreta de fallback/migração já decidida em "Pré-flight
§5" acima — esta seção é o resumo de alto nível, consistente com ela.)*

Integrar o César Core/OmniRoute atrás das abstrações já existentes
primeiro (`AI Provider Manager` e `WebSearchManager`), sem remover
nenhum provider/mecanismo atual — a cascata direta antiga vira rollback
transitório atrás de feature flag (§5), não é removida na integração
inicial. Validar comportamento real (provider usado, fallback, custo,
disponibilidade) antes de qualquer remoção. Reduzir uso de Firecrawl
para Web Search só depois da nova rota provada em produção. Remoção do
fallback transitório antigo (providers individuais, uso de Firecrawl
para busca) é etapa final, condicionada a validação — nunca simultânea
à integração inicial.

## Testes (quando a TASK for implementada)

Focados, não suíte completa/Docker indiscriminado: integração GG Oferta
→ OmniRoute; seleção/fallback; indisponibilidade do OmniRoute/provider;
Web Search via OmniRoute; separação Web Search × Firecrawl; preservação
do contrato das abstrações existentes (`AI Provider Manager` e a nova
abstração de Web Search) para os chamadores atuais.

## Fora de escopo (nesta e na implementação futura, salvo decisão em contrário)

Implementação de código, configuração ou secret (o pré-flight em si já
foi feito, 2026-08-30, sob pedido explícito — ver seções no topo deste
documento). Remoção do Firecrawl
como um todo — só o uso dele para Web Search genérica é candidato a
redução. Reescrita completa do `AI Provider Manager`. Qualquer chamada
paga não estritamente necessária. Deploy ou mudança em PROD.
