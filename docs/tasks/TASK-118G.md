# TASK-118G — Search pelo Core, enriquecimento separado

Estado: implementação e validação DEV concluídas, aguardando revisão; sem commit/push/PROD/118H.

## Arquitetura

Market Research -> WebSearchManager -> CesarCoreSearchProvider -> Core ->
OmniRoute 3.8.50 -> SearXNG certificado. Interface WebSearchProvider somente
busca URLs; resposta neutra com título/URL/snippet/posição, provider/source,
cache, queries, IDs e créditos. Bearer próprio via arquivo; purpose
`market_research`, service class configurada e política `FREE_ONLY`.

## Fallback e enriquecimento

Firecrawl `/v2/search` é fallback explícito apenas por rede/timeout e 500/503/504
retriáveis. Não em vazio, snippets fracos, 400/401/403/404/422/429, policy, quota
ou contrato inválido. 502 é conservadoramente não retriável porque o Core o usa
para auth/input/contrato upstream; indisponibilidade é normalizada em 503.

`/v2/scrape` permanece fora da interface Search: somente evidência insuficiente,
até 3 URLs de domínios distintos. Sem URLs, nada é scraped. Identidade, quórum,
TTL e finalização de negócio são preservados. O parâmetro legado `firecrawl`
na coleta transporta enriquecimento e alimenta o adapter; o fluxo principal
nunca chama seu search diretamente. Sem Firecrawl, Core pode pesquisar sem
enriquecimento. Grounding de IA não migra nesta TASK.

## Configuração

Flags `AISHOPPING_CESAR_CORE_SEARCH_ENABLED` e
`AISHOPPING_CESAR_CORE_SEARCH_FALLBACK_ENABLED` padrão false; habilitar ambas
explicitamente em DEV. Flag Search falsa é rollback explícito para Firecrawl
atrás do manager. Credencial em `AISHOPPING_CESAR_CORE_API_KEY_FILE`, arquivo
`.secrets/cesar-core-client-dev`. URL/service/classe da 118F; timeout Search
próprio. Examples não mudam Compose/PROD.

Core: JSON habilitado no SearXNG, `providerSpecificData.baseUrl` no OmniRoute,
target `searxng-search` e `CESAR_CORE_SEARCH_PROVIDER_HEALTH_URL` para `/healthz`.
Sem configuração completa não há target disponível; readiness verifica serviço
ao vivo. Capabilities descrevem configuração. Nenhum serviço permanente.

## max_results e observabilidade

Cap obrigatório na saída; SearXNG pode adquirir mais resultados externamente.
OmniRoute trunca e Core/consumidor garantem o cap final. Não é bug nem enforcement
externo. SearXNG gratuito sem conta/API key, mas depende de serviço local e
sofre CAPTCHA/rate limit de motores. Context7 nunca atende market_research.

Search registra provider/source, fallback/motivo, cache, queries e IDs sem
conteúdo/URL/credenciais. Métricas distinguem Search, scrape_enrichment, URLs
tentadas e créditos quando reportados, sem chamar scrape de fallback.

## Evidência

67 testes focados do GG Oferta passaram, incluindo regressão da integração de IA;
os 17 testes de Search foram repetidos após o ajuste de nomes. Os 11 testes de
Market Research em PostgreSQL 18.4 descartável passaram (regressão de negócio,
não prova do fluxo completo de banco com SearXNG). Core: 207 testes não-contract
e 22/22 contracts reais passaram.

E2E real novo passou: 3 resultados, cache false/true, zero-result,
auth/capability/quota sem fallback e conexão recusada com Firecrawl real
(2 créditos reportados nessa chamada). SearXNG parado também produziu readiness
degraded e fallback Firecrawl real, 3 resultados e 2 créditos nessa chamada.
Métricas e cinco eventos de tracing foram verificados na rodada final.
Bons/fracos snippets e teto de scrape possuem testes determinísticos
complementares. Contract opt-in: `tests/test_web_search_contract.py` com harness
Core DEV isolado, nunca PROD.

Ruff passou no Core e nos demais arquivos afetados do GG; orchestration.py
mantém seis UP037 preexistentes, confirmados também no HEAD. Diff-check passou.
Rollout permanece desligado por padrão; os serviços da prova são temporários.

## Incidente do harness

O primeiro harness mudou o cwd e o Core leu o `.env` do GG Oferta; uma exceção
exibiu senha local de banco na saída do teste. Valor não reproduzido aqui.
Usuário avisado e rotação recomendada, não executada. Harness corrigido para
Settings isolados sem dotenv. Rodada corrigida não encontrou credenciais de
Core/Firecrawl nos logs; não se afirma ausência de exposição na rodada inicial.
