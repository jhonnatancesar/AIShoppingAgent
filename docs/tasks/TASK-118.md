# TASK-118 — Integrar o OmniRoute como camada central de roteamento de IA e Web Search

Status: **Pré-flight em andamento (2026-08-30) — fonte oficial
confirmada e contratos reais extraídos do código.** Nenhum código,
configuração, secret ou dependência deste repositório alterados.

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
- **Achado direto do texto da descrição do endpoint**: "AnySearch
  (`anysearch-search`) provides free fallback-only web search" — existe
  uma opção gratuita nativa de fallback pra busca, relevante pra política
  de custo do projeto (gratuito → free tier → pago).
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
remotamente, é uma peça de infraestrutura que o próprio GG Oferta
precisaria rodar. Três formas oficiais de instalar:

- `npm install -g omniroute` (recomendado pelo próprio guia).
- **Docker**: `docker run -d --name omniroute -p 20128:20128
  diegosouzapw/omniroute:latest` (imagem oficial publicada,
  `diegosouzapw/omniroute:X.Y.Z` pra pin de versão). **Se encaixa
  diretamente no `compose.yaml` já existente** — diferente do
  `collection_worker` (que precisou sair do Docker por causa do Edge/
  CDP), o OmniRoute é um serviço HTTP comum, sem necessidade de sessão
  interativa Windows nem navegador real.
- Build a partir do código-fonte (`git clone` + `npm install` + `npm run dev`).

Sobe em `http://localhost:20128`, dashboard incluído.

## Pré-flight §3 — Estado das 7 perguntas do documento original

1. **O que é o OmniRoute de verdade** — ✅ respondida acima (§1/§2).
2. **Fallback quando o OmniRoute está indisponível** — parcialmente
   informada: o OmniRoute já resolve fallback *entre providers que ele
   gerencia* (3 camadas, §2). Falta decisão do usuário só pro caso
   "OmniRoute inteiro fora do ar" (cascata local de emergência vs. falha
   explícita) — **decisão ainda em aberto, não assumida aqui**.
3. **Como declarar "requisito mínimo de qualidade/capacidade" por
   chamada** — ainda em aberto; o contrato do OmniRoute não define isso
   por si (é decisão de como o GG Oferta chama o `chat/completions`,
   ex.: por `model` explícito vs. deixar o roteamento decidir).
4. **Granularidade da abstração de Web Search** — ainda em aberto
   (módulo novo dedicado vs. extensão do `AI Provider Manager`); agora
   com o contrato real do `/v1/search` em mãos pra informar essa decisão.
5. **Reconciliação com a cascata gratuita atual** (Gemini → Groq →
   OpenRouter) — ainda em aberto; achado relevante: o OmniRoute tem
   providers gratuitos nativos (Kiro, OpenCode Free, AnySearch) que
   podem se sobrepor ou substituir parte dessa cascata — decisão de
   política de custo cabe ao usuário, não a este pré-flight.
6. **Onde ficam as chaves centralizadas no OmniRoute** — parcialmente
   respondida: só uma chave de inferência (`sk-…`) precisa ficar no GG
   Oferta (`*_FILE`, mesmo padrão já usado); as chaves dos providers
   individuais (Gemini, Groq etc.) migrariam pra dentro do próprio
   OmniRoute (configuradas no dashboard dele) — implica um novo local de
   configuração de secrets fora do `.secrets\` atual, ponto que precisa
   de decisão explícita do usuário antes de codificar.
7. **Critério de fuzzy/IA vs. regra determinística** — inalterado, seguem
   valendo os guardrails já vigentes do projeto (ex.: Product Identity
   Engine); o OmniRoute não influencia essa decisão.

## Pré-flight — ainda faltando antes de fechar

Decisões que só o usuário pode tomar (não inventadas aqui):
- Fallback pro caso "OmniRoute inteiro indisponível" (pergunta 2).
- Onde/como rodar o OmniRoute em produção (mesmo Windows Server via
  Docker Compose existente é o caminho mais natural pelo que foi
  encontrado, mas não é uma decisão já tomada).
- Se as chaves dos providers individuais (Gemini/Groq/OpenRouter) migram
  pra dentro do OmniRoute ou continuam só no GG Oferta com o OmniRoute
  só roteando (pergunta 6).
- Granularidade da abstração de Web Search (pergunta 4).
- Política de custo final reconciliando a cascata gratuita atual com os
  providers gratuitos nativos do OmniRoute (pergunta 5).

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
  provider podem ser substituídas por configuração de acesso ao
  OmniRoute, mantendo o padrão `*_FILE` já vigente.
- Observabilidade/telemetria de chamadas de IA e busca já existente
  (`app/observability/`) — precisa continuar distinguindo provider,
  modelo, motivo de fallback, rota gratuita vs. paga, e chamadas que
  ainda usaram Firecrawl.

## Critérios de aceite

- `AI Provider Manager` continua sendo o único ponto de acesso interno
  à IA; nenhum módulo de domínio passa a chamar o OmniRoute ou um
  provider diretamente.
- Existe uma abstração interna própria para Web Search, com a mesma
  disciplina de não vazar a API do OmniRoute para o domínio.
- Política de custo (gratuito → free tier → pago) é respeitada e
  observável — dá para saber, por chamada, qual rota foi usada e por
  quê.
- Firecrawl deixa de ser usado para Web Search genérica; uso
  remanescente é só scraping avançado, auditável e reduzido em volume
  real (não só em teoria).
- Fallback seguro quando o OmniRoute está indisponível (mecanismo exato
  a decidir no pré-flight) — nenhuma funcionalidade essencial do GG
  Oferta trava por indisponibilidade externa sem alternativa.
- PROD e DEV continuam com credenciais e dados completamente separados;
  nenhuma chave em código/repositório/log/Telegram.
- Migração incremental: OmniRoute integrado atrás das abstrações
  existentes primeiro, validado com uso real, só depois remoção de
  infraestrutura antiga que se tornar redundante.

## Riscos e pontos a decidir no pré-flight

*(Lista original, mantida por histórico — ver "Pré-flight §3" acima para
o estado real de cada uma após a investigação de 2026-08-30.)*

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

Integrar o OmniRoute atrás das abstrações já existentes primeiro
(`AI Provider Manager` e a nova abstração de Web Search), sem remover
nenhum provider/mecanismo atual. Validar comportamento real (provider
usado, fallback, custo, disponibilidade) antes de qualquer remoção.
Reduzir uso de Firecrawl para Web Search só depois da nova rota provada
em produção. Remoção de infraestrutura antiga (providers individuais,
uso de Firecrawl para busca) é etapa final, condicionada a validação —
nunca simultânea à integração inicial.

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
