# Integração com César Core (IA, Web Search e Enrichment)

César Core é um gateway privado de IA, Web Search e enriquecimento de URL,
publicado num **repositório próprio** (`cesar-core`, fora deste monólito),
com sua própria versão, release e Control Plane administrativo. Este
documento cobre a funcionalidade da integração vista do lado do
AIShoppingAgent (GG Oferta) — o que cada capacidade faz, como o fail-closed
funciona e onde a quota é decidida. Para instalar as duas partes juntas veja
[Instalação → César Core](../installation/cesar-core.md); para operação,
falhas, rollback e reprodução da validação veja o
[Runbook César Core](../operations/cesar-core-runbook.md), que não é
duplicado aqui.

Desde a Fase E.1, o Core não é opt-in: configuração e credencial ausentes
fecham AI, grounding, Search e enrichment. Não existe retorno a provider
direto no GG Oferta para nenhuma das quatro. GG Oferta e o worker nativo não
guardam credencial, endpoint ou nome de provider de Gemini, Groq, OpenRouter
ou Firecrawl — a dependência é sempre o César Core; os providers concretos
ficam abaixo do OmniRoute.

## IA (ADR-016, TASK-118F)

`AIProviderManager` continua a única porta de IA do domínio. As factories
(`build_user_ai_provider_manager`, `build_admin_dev_ai_provider_manager`)
sempre devolvem `CesarCoreAIProviderManager`, que envolve
`CesarCoreAIProvider`. AI normal e `require_search_grounding=True` passam pelo
Core. Gemini/Groq/OpenRouter diretos, suas factories e disaster fallback foram
removidos do GG Oferta.

- Toda chamada `generate()` vai ao Core.
- Erro de configuração, rede, timeout ou HTTP fecha o fluxo sem provider local.
- Perfil, mensagens tipadas, correlação e contrato de erro do
  `AIProviderManager` não mudam para quem chama — a troca de gateway é
  interna à factory.

## Web Search (ADR-017, TASK-118G)

`WebSearchManager` sempre monta `CesarCoreSearchProvider`. Firecrawl
`/v2/search`, adapter e fallback foram removidos. Entrada inválida, falha de
configuração, rede, timeout, qualquer erro HTTP/policy/quota e resposta
malformada fecham o fluxo. Resultado vazio do Core continua sendo sucesso.

## Enrichment (Fase E.1)

Market Research (Search encontra URLs; enrichment extrai conteúdo de até 3
delas quando o snippet não basta) permanece uma capacidade separada de
Search — mas, desde a Fase E.1, também passa pelo Core:
`CesarCoreFetchProvider` (`app/search/cesar_core_fetch.py`) chama
`POST /v1/fetch` no César Core, que traduz para `POST /v1/web/fetch` no
OmniRoute (Firecrawl é hoje o provider real por trás disso, mas o GG nunca
sabe disso). Reusa a mesma credencial de aplicação de AI/Search
(`cesar_core_api_key_file`) — não existe credencial separada por capability
no lado do GG. `firecrawl.py` (cliente direto), `firecrawl_api_key(_file)` e
seus secrets foram removidos do GG Oferta depois de confirmado que não havia
mais consumidor.

Origem específica bloqueada/sem conteúdo continua sendo sucesso de negócio
(`None`/`fetched=false`), nunca exceção — mesma semântica de antes da
migração. Configuração ou Core indisponível fecha o fluxo (`enrichment=None`
no chamador, tratado pelo Market Research como "recurso desligado", nunca
como erro fatal — regra de negócio preexistente, não alterada por esta
fase). Ver `C:\cesar-core\docs\architecture\gg-oferta-core.md` ("Estado da
FASE E.1") para o registro completo, incluindo o achado do `DEC-107` que
tornou essa migração possível sem inventar integração nova.

## Quota: onde a política vive (ADR 0018, `cesar-core`)

A partir da TASK-118H, quota AI/Search/enrichment do César Core tem duas
metades separadas, ambas fora deste repositório:

- **Política (limite)**: mora no Control Plane administrativo do próprio
  Core — SQLite `quota_policies`, um limite por `application_id`+`capability`
  (`gg_oferta`/`ai`, `gg_oferta`/`search`, `gg_oferta`/`fetch`). Mudar o
  limite do GG Oferta é
  feito **na interface administrativa do César Core**
  (`http://<host-do-core>/admin`) ou na API Admin real dele
  (`POST /admin/api/login` → `PUT /admin/api/applications/gg_oferta`) — nunca
  editando nada neste repositório.
- **Consumo (contador da janela de 60s)**: mora em Redis, chaveado por
  namespace. Reiniciar o processo do Core **não** reseta a janela nem devolve
  admissões já consumidas.

Do lado do GG Oferta, uma quota esgotada chega como erro sanitizado e
não-retryable (`cesar_core_request_failed` na IA, `core_search_rejected` na
Search) — nunca com detalhe do limite configurado nem contagem interna do
Core. Detalhes de topologia Redis/SQLite, backup e rollback pertencem ao
repositório `cesar-core` (`README.md`, `docs/adr/0018-persistent-quota-recovery.md`,
`docs/deployment/docker.md`), não a este.

## Onde isto foi decidido

`docs/adr/ADR-016-cesar-core-ai-rollout.md` (IA), `docs/adr/ADR-017-web-search-vs-enrichment.md`
(Search), `docs/tasks/TASK-118.md` a `TASK-118H.md` (histórico completo,
inclusive achados e correções ao longo das fases). Secrets desta integração:
`docs/installation/secrets.md`.
