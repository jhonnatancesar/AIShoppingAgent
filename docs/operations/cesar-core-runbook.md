# Integração César Core — operação, falhas e rollback

Escopo: TASK-118H, operação **DEV local** validada. Este documento não autoriza
deploy nem mudança em PROD. Código publicado, rollout habilitado e serviço em
execução são estados diferentes. O Core é dependência obrigatória no GG.
Funcionalidade da integração:
[Arquitetura → Integração com César Core](../architecture/cesar-core-integration.md).
Primeira instalação: [Instalação → César Core](../installation/cesar-core.md).
Este documento cobre só falhas, rollback e reprodução da validação.

## Arquitetura e limites

- AI: GG Oferta → AIProviderManager → CesarCoreAIProvider → Core → OmniRoute.
- Search: GG Oferta → WebSearchManager → CesarCoreSearchProvider → Core →
  OmniRoute → SearXNG.
- Evidência insuficiente: Firecrawl `/v2/scrape` nas URLs selecionadas, até três
  domínios distintos. É enriquecimento; não é outra busca nem fallback Search.

AI e Search não compartilham interface de provider. Grounding passa pelo Core.
`FREE_ONLY` é preservado no Core; Firecrawl Scrape usa a conta/créditos já
existentes e sua utilização precisa ser observada separadamente.

`max_tokens` exige capability certificada e defesa fail-closed após resposta;
`max_results` limita a saída, não promete limitar aquisição externa do SearXNG.
Vazio legítimo é sucesso. Context7 não substitui Web Search de Market Research.

Os adapters GG atuais aceitam **HTTP loopback**. A publicação Core em rede Docker
e HTTPS não estão validadas por esta task. Não usar `0.0.0.0` como atalho.
Circuitos (GG) continuam em memória, por processo. Quota **não é mais** em
memória: a política (limite) mora no Control Plane administrativo do Core
(SQLite, `quota_policies`, um limite por `application_id`+`capability`) e o
consumo (contador da janela de 60s) mora em Redis, chaveado por namespace.
Reiniciar o processo Core **não** reseta a janela nem devolve admissões já
consumidas — ver ADR 0018 (`docs/adr/0018-persistent-quota-recovery.md` no
repositório `cesar-core`). Múltiplas réplicas compartilhando o mesmo Redis
compartilham o mesmo contador; múltiplas réplicas compartilhando o mesmo
SQLite compartilham a mesma política. Antes de PROD, aprovar topologia,
transporte, credenciais próprias e essa semântica; não reutilizar credenciais DEV.

## Configuração (somente nomes e caminhos, sem valores secretos)

GG Oferta, processo DEV:

```powershell
$env:AISHOPPING_ENVIRONMENT='development'
$env:AISHOPPING_CESAR_CORE_BASE_URL='http://127.0.0.1:8100'
$env:AISHOPPING_CESAR_CORE_API_KEY_FILE='C:\AIShoppingAgent\AIShoppingAgent\.secrets\cesar-core-client-dev'
$env:AISHOPPING_FIRECRAWL_API_KEY_FILE='C:\AIShoppingAgent\AIShoppingAgent\.secrets\firecrawl_api_key'
```

`AISHOPPING_CESAR_CORE_SERVICE` identifica `backend` ou `collection_worker`;
`AISHOPPING_CESAR_CORE_SERVICE_CLASS` seleciona `economy`/`standard`/`quality`.
`AISHOPPING_CESAR_CORE_MAX_TOKENS` tem default 1024; prova textual usa 512.
Timeout AI: `AISHOPPING_CESAR_CORE_TIMEOUT_SECONDS` (90s); Search:
`AISHOPPING_CESAR_CORE_SEARCH_TIMEOUT_SECONDS` (30s).

Core requer:

| Configuração | Uso |
| --- | --- |
| `CESAR_CORE_SECURITY_GG_OFERTA_API_KEY_FILE` | Cópia própria da credencial aplicação → Core |
| `CESAR_CORE_OMNIROUTE_AI_API_KEY_FILE` | Arquivo upstream AI |
| `CESAR_CORE_OMNIROUTE_SEARCH_API_KEY_FILE` | Arquivo upstream Search, caminho independente |
| `CESAR_CORE_OMNIROUTE_BASE_URL` | OmniRoute local, na prova `http://127.0.0.1:20128` |
| `CESAR_CORE_AI_ENABLED` | Ativar AI |
| `CESAR_CORE_AI_DEFAULT_MODEL` | Target certificado `oc/mimo-v2.5-free` |
| `CESAR_CORE_AI_MODEL_ENFORCES_MAX_TOKENS` | `true` somente para target certificado |
| `CESAR_CORE_SEARCH_ENABLED` | Ativar Search |
| `CESAR_CORE_SEARCH_DEFAULT_PROVIDER` | `searxng-search` |
| `CESAR_CORE_SEARCH_PROVIDER_HEALTH_URL` | Health SearXNG; prova `http://127.0.0.1:18889/healthz` |
| `CESAR_CORE_SEARCH_TECHNICAL_DOCUMENTATION_PROVIDER` | `context7` só para documentação técnica |

AI/Search não reutilizam silenciosamente arquivos de outra capability. A cópia
temporária da chave de teste não representa duas chaves emitidas independentemente.
OmniRoute 3.8.50 usa `REQUIRE_API_KEY=true` no ambiente certificado. SearXNG:
`search.formats: [html, json]`; conexão `searxng-search` com
`providerSpecificData.baseUrl=http://searxng-118h-validation:8080/search` na rede
isolada. Permitir endereço privado apenas nessa rede de confiança.

Não carregar `.env` do GG no Core. O harness desativa dotenv nos Settings e
remove flags herdadas. Não despejar `docker inspect`, env ou exceções contendo
configuração em console/log compartilhado.

## Startup e recuperação

OmniRoute, SearXNG e Core podem iniciar separadamente. Recomenda-se subir
dependências primeiro para reduzir recusas durante warm-up, mas isso não é uma
dependência rígida de ordem. Core continua vivo e fica `degraded` até os probes
de suas capabilities passarem. `capabilities=available` descreve configuração,
não substitui readiness nem garante provider externo saudável.

Após recuperação de Core/OmniRoute/SearXNG, o próximo Search tenta novamente o
Core. AI recupera sem restart
do GG; se o circuit breaker abriu após falhas repetidas, aguardar seu período
(`AISHOPPING_CIRCUIT_OPEN_SECONDS`, default 30s) para a tentativa half-open.
Não limpar circuitos em produção como estratégia habitual de recuperação.

Mudanças de caminhos ou URLs exigem reload de Settings/factories. Como
processos podem manter managers/configuração em cache, **reiniciar somente os
processos consumidores afetados** é o procedimento operacional conservador.
Alterar arquivo de ambiente não modifica automaticamente processos em execução.
Reiniciar dependência indisponível não exige reiniciar todo o stack.

## Health e diagnóstico

- GG: `/health` e `/ready` da API local em execução; depois smoke test pelo manager.
  A saúde geral do GG não substitui prova da integração opt-in.
- Core: `GET http://127.0.0.1:8100/health`, `/ready`, `/v1/capabilities`, `/metrics`.
  `/ready` pode retornar HTTP 200 com corpo `status=degraded`: ler o corpo.
- OmniRoute: `GET http://127.0.0.1:20128/api/health`; o Core faz probes autenticados
  AI/Search. Health 200 sozinho não prova autenticação nem geração.
- SearXNG: `/healthz` da instância; positivo `/search?format=json&q=...` somente
  quando necessário. CAPTCHA/rate limit de motores continua possível.
- Firecrawl: não inventar health endpoint. Quando necessário, executar busca
  limitada pela API/adapter e verificar resultado/créditos. Isso pode consumir
  créditos; não usar como liveness periódico.

| Sintoma | Causa provável | Verificação | Ação |
| --- | --- | --- | --- |
| AI 401 | Credencial aplicação → Core inválida/ausente | Arquivo legível e cópias correspondentes, sem imprimir | Corrigir configuração; não fallback |
| AI 502 | Auth/contrato/limite upstream violado | Código normalizado e request/correlation IDs | Corrigir target/credencial/contrato; não truncar para ocultar consumo |
| AI 503 | OmniRoute/rede indisponível | Ready, health e IDs | Restaurar dependência; não repetir inferência incerta |
| Search vazio | Sem resultados ou limitação do motor | Provider/source, status e evidência | Aceitar vazio; sem segunda busca ou URLs inventadas |
| SearXNG fora | Processo, rede ou configuração JSON | Health, conexão OmniRoute e rede isolada | Restaurar instância/configuração |
| Core degraded | Credencial/configuração/dependência obrigatória | Corpo de ready e capacidades habilitadas | Corrigir causa; não afrouxar readiness |
| Quota 429 | Janela Redis da aplicação esgotada (limite lido do Control Plane) | HTTP counter por aplicação/rota/status, Retry-After | Aguardar janela; sem retry/fallback para burlar quota; mudar o limite exige a API Admin (ADR 0018), não o restart do Core |
| Search 400/401/403 | Input/auth/capability/policy | Código/IDs e config | Corrigir requisição/autorização, não cascata |
| Firecrawl Scrape fora | Rede, 429/5xx ou autenticação | Erro normalizado e tentativas de enriquecimento | Seguir sem enriquecimento; investigar, sem loop |

AI, grounding e Search falham fechados em configuração, rede, timeout, HTTP,
quota ou policy. Retries internos do Firecrawl Scrape são finitos (default 3).

## Recuperação operacional

Não existe rollback para providers diretos. Restaure Core/OmniRoute/provider ou
corrija URL/credencial/capability; até lá AI, grounding e Search permanecem
indisponíveis de forma fechada. Banco e dados comerciais não são revertidos.

## Reprodução da validação isolada

Usar Python oficial e os dois repositórios locais. Não executar contra PROD.
Na raiz `C:\cesar-core`, com Docker disponível e porta 20128 livre:

```powershell
python scripts/stack_118h_dev.py up
try {
    python scripts/run_118h_contracts.py recovery --gg-repo C:\AIShoppingAgent\AIShoppingAgent
    python scripts/run_118h_contracts.py core --gg-repo C:\AIShoppingAgent\AIShoppingAgent
    python scripts/run_118h_contracts.py gg --gg-repo C:\AIShoppingAgent\AIShoppingAgent
    python scripts/run_118h_contracts.py resilience --gg-repo C:\AIShoppingAgent\AIShoppingAgent
} finally {
    python scripts/stack_118h_dev.py down
}
```

Verificar código de saída de **cada** fase; qualquer falha impede aprovação.
Os scripts de setup usam exclusivamente o original DEV já adotado, leitura de
origem SQLite e cópia descartável, nomes/labels exclusivos e portas loopback.
Não rodar junto de outra validação que use 20128. A fase `gg` testa cache frio;
se a consulta do contract já foi executada recentemente, reiniciar **somente**
o OmniRoute descartável antes dessa fase ou aguardar o TTL (180s).
`down` remove banco copiado, chaves temporárias, logs e provas de teste; não
apaga sessão Codex, banco original ou arquivos `.secrets` dos projetos.

Fixtures de restart usam processo Core real com PID novo e os mesmos managers
GG. Provas de quota observam sockets reais sem substituir respostas. Falhas
HTTP 400/malformed e Firecrawl 503 são injetadas em servidor HTTP loopback;
isso valida decisão/retries, não alega indisponibilidade natural da Firecrawl.
Positivos de AI/Search e rollback usam providers externos reais.

O caso de quota (`test_real_quota_persists_across_core_restart`) sobe um
Control Plane administrativo descartável e exclusivo desse processo Core:
SQLite, hash de senha Argon2id e pepper gerados em arquivo temporário durante
o próprio teste, origem loopback isolada (`CESAR_CORE_ADMIN_ALLOWED_ORIGIN`)
e `CESAR_CORE_ADMIN_COOKIE_SECURE=false` restrito a essa origem HTTP loopback.
A política de quota é configurada pela API administrativa real do Core
(`POST /admin/api/login` → `GET/PUT /admin/api/applications/{id}`, com sessão
e `X-CSRF-Token`), nunca escrita direto em `quota_policies`; a mudança é
confirmada por uma segunda leitura antes de prosseguir. O antigo argumento
`--quota` do harness continua existindo só para semear o bootstrap de um
SQLite ainda vazio — não reflete mais a política vigente depois que ela já
existe ou foi alterada pela API Admin (ver ADR 0018). Ao final, o SQLite, o
hash de senha e o pepper temporários desse caso são apagados explicitamente,
sem depender só da limpeza automática de diretório temporário do pytest.

## Observabilidade e segurança

Correlacionar request/correlation IDs, application/service/purpose quando
disponíveis, provider/source, resultado, usage/cached e fallback_reason.
Separar `search_fallback=firecrawl` de `scrape_enrichment=firecrawl`, quantidade
de URLs e créditos reportados. Quota aparece no HTTP counter 429 por rota;
não existe promessa de uma métrica dedicada chamada `quota`.

Nunca imprimir Bearer, valores dos arquivos, mensagens system, prompts, queries
ou resultados integrais em traces. Testes novos comparam credenciais e conteúdo
com logs/traces capturados e removem logs temporários. Arquivos locais continuam
ignorados no Git. O incidente antigo da 118G permanece aceito/documentado, sem
reescrever histórico Codex e sem nova auditoria global como requisito.
