# Firecrawl

`FirecrawlSearchProvider` (`app/search/firecrawl.py`) é o único cliente
Firecrawl do projeto. Firecrawl é **pesquisa/coleta de evidência web**,
nunca um provedor de IA — o `AIProviderManager` nunca a chama para gerar
texto, e nenhum módulo de domínio fala com a API da Firecrawl
diretamente fora deste cliente. Dois consumidores hoje:

- `AdminDevAIProviderManager` (grounding do perfil DEV, TASK-083) — uma
  busca (`/v2/search`) injetada como contexto não confiável antes da
  mesma cascata gratuita de LLM (`docs/architecture/ai-provider-manager.md`).
- `app.market_research` (TASK-113) — avaliação de mercado externa para
  qualidade de alertas de preço. Desenho completo em `docs/tasks/
  TASK-113.md` §33/§39-40; este documento cobre só o cliente/contrato
  HTTP, não duplica a lógica de negócio.

## Endpoints usados

Só dois, ambos confirmados contra a documentação oficial
(`docs.firecrawl.dev`, auditoria 2026-08-27):

- **`POST /v2/search`** — busca web. Payload: `query`, `sources`
  (sempre `["web"]` neste projeto — imagens/notícias nunca usados),
  `limit`. Resposta: `success`, `data.web[]` (`title`/`url`/
  `description`/`markdown`/`html`), `warning`, `id`, `creditsUsed`.
- **`POST /v2/scrape`** — leitura de UMA página específica, básica (sem
  `proxy`, sem `actions`, sem `location`/geo-spoofing). Payload:
  `url`, `formats: ["markdown"]` (forma de string aceita pela API,
  confirmado contra a documentação oficial — não precisa da forma de
  objeto `{type: "markdown"}`). Resposta: `success`, `data.markdown`,
  `data.metadata.{title,url,sourceURL,statusCode,error}` — **nunca**
  um `data.url` de nível superior (achado da auditoria: uma versão
  anterior do parser tentava lê-lo e sempre caía no fallback
  `sourceURL`; corrigido para `metadata.url`, a URL final pós-
  redirecionamento).

Regra absoluta, sem exceção, para os dois: **sem stealth, sem `proxy`
evasivo (`enhanced`/`auto` nunca usados — só o comportamento básico
default), sem geo-spoofing, sem CAPTCHA solving, só páginas públicas**
— mesma política de nunca contornar proteção anti-bot já aplicada ao
resto do projeto (Terabyte desativada em vez de evadida, DEC-070).

## Duas buscas por avaliação (TASK-113 §33.18)

Quando o gatilho local de `app.market_research` dispara, SEMPRE rodam
duas buscas `/v2/search` na mesma passada, nunca uma isolada: mercado
atual (para `market_low`/`market_high`/`classification`) e histórico
externo direcionado (para `historical_low_external`). Cada uma tem seu
próprio critério de suficiência (§33.16); se a busca sozinha não
atingir o mínimo, o fallback `/v2/scrape` roda em até **3 URLs** de
domínios distintos retornadas pela própria busca (nunca uma URL de
fora do resultado) — nunca mais que isso por busca.

## Validação determinística de identidade

Evidência (título/descrição de `/v2/search`, ou `markdown`/`title` de
`/v2/scrape`) só entra no assessment depois de reparseada pelo MESMO
motor determinístico que gera `Product.identity_key`
(`app.products.identity.resolve_product_variant`, TASK-097) e bater
EXATAMENTE com a identidade esperada — a IA que interpreta os números/
datas da evidência (`app.market_research.service._interpret_evidence`)
nunca decide equivalência de produto nem autoriza merge de identidade;
o Product Identity Engine continua a única autoridade para isso.

## Retry / circuit breaker / rate limit vs. bloqueio de origem

Antes da TASK-113, nenhuma chamada Firecrawl tinha retry nem circuit
breaker. Agora as duas chamadas (`search`/`scrape_basic`) passam por
`retry_operation`/`CircuitBreaker` (`app.core.resilience`, infra já
existente, reaproveitada sem framework novo) — retry limitado com
backoff/jitter só para `429`/`5xx`/timeout/indisponibilidade (nunca
`400`/`401`/`403`, determinísticos). `search` e `scrape_basic` usam
CHAVES DE CIRCUITO SEPARADAS (`firecrawl_search`/`firecrawl_scrape`),
mas o princípio que importa é mais fino que a chave: um circuito só
abre por falha do **SERVIÇO** Firecrawl (a chamada HTTP em si falhou
ou expirou) — nunca por uma origem específica que o `/v2/scrape`
tentou ler e não conseguiu. Essa segunda situação é representada pela
própria API com `success: true` no envelope mas `data.metadata.
statusCode` fora de 2xx/3xx ou `data.metadata.error` preenchido — o
parser (`parse_firecrawl_scrape_response`) trata isso como "sem
evidência desta fonte" (devolve `None`, nunca lança), e quem chama
(`app.market_research.service._search_with_scrape_fallback`) só
desiste daquela URL específica e segue para a próxima, sem tocar o
circuito nem contar como falha do serviço.

## Falha nunca invalida a coleta

Firecrawl/IA indisponíveis nunca derrubam um `CollectionRun`: o
histórico comercial (`Offer`/`PriceObservation`) já foi persistido
antes, sem depender de rede externa nenhuma. Uma falha do serviço na
pesquisa de mercado vira `MarketPriceAssessment.status = FAILED` com
`retry_after` (backoff, nunca reclaim imediato) — nunca uma exceção
propagada para fora de `app.market_research.service.run_market_research`.

## Single-flight / cache

Uma pesquisa nunca é repetida por Mission, usuário ou variação pequena
de preço — compartilhada por `product_id` via claim atômico
(`INSERT ... ON CONFLICT ... WHERE`, `app.market_research.service.
claim_assessment`) com TTL configurável. Desenho completo (estados,
lease, TTL por modo de cadência) em `docs/tasks/TASK-113.md` §33.3/
§33.14/§33.15 — não duplicado aqui.
