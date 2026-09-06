# Firecrawl (histórico) e enrichment atual

**Desde a Fase E.1, o GG Oferta não tem mais cliente Firecrawl direto.**
`app/search/firecrawl.py` foi removido. O enriquecimento de Market Research
(extrair conteúdo de até 3 URLs já encontradas pelo Search, quando o snippet
não é suficiente) passa por `CesarCoreFetchProvider`
(`app/search/cesar_core_fetch.py`) → César Core (`POST /v1/fetch`) →
OmniRoute (`POST /v1/web/fetch`) → provider configurado no Core (Firecrawl é
o provider real hoje, mas o GG não sabe disso nem guarda credencial/endpoint
dele). Detalhe completo da capability:
[Arquitetura → Integração com César Core](cesar-core-integration.md#enrichment-fase-e1).

Este documento preserva só o que continua sendo regra de negócio do GG
Oferta, inalterada pela troca de infraestrutura.

## Validação determinística de identidade

Evidência (título/snippet do Core Search, ou título/conteúdo do
enriquecimento) só entra no assessment depois de reparseada pelo MESMO motor
determinístico que gera `Product.identity_key`
(`app.products.identity.resolve_product_variant`, TASK-097) e bater
EXATAMENTE com a identidade esperada — a IA que interpreta os números/datas
da evidência (`app.market_research.service._interpret_evidence`) nunca
decide equivalência de produto nem autoriza merge de identidade; o Product
Identity Engine continua a única autoridade para isso.

## Sem evidência da origem nunca é erro

Uma origem específica bloqueada, sem conteúdo ou com paywall é sucesso de
negócio (`CesarCoreFetchProvider.scrape_basic` devolve `None`), nunca uma
exceção — mesma semântica que o cliente Firecrawl direto já tinha antes da
migração. `app.market_research.service._search_with_enrichment` desiste
daquela URL específica e segue para a próxima; erro de transporte/gateway
(`CesarCoreFetchError`) é o único caso tratado como falha do enriquecimento.

## Falha nunca invalida a coleta

Enrichment/IA indisponíveis nunca derrubam um `CollectionRun`: o histórico
comercial (`Offer`/`PriceObservation`) já foi persistido antes, sem depender
de rede externa nenhuma. Uma falha do serviço na pesquisa de mercado vira
`MarketPriceAssessment.status = FAILED` com `retry_after` (backoff, nunca
reclaim imediato) — nunca uma exceção propagada para fora de
`app.market_research.service.run_market_research`.

## Single-flight / cache

Uma pesquisa nunca é repetida por Mission, usuário ou variação pequena de
preço — compartilhada por `product_id` via claim atômico
(`INSERT ... ON CONFLICT ... WHERE`, `app.market_research.service.
claim_assessment`) com TTL configurável. Desenho completo (estados, lease,
TTL por modo de cadência) em `docs/tasks/TASK-113.md` §33.3/§33.14/§33.15 —
não duplicado aqui.
