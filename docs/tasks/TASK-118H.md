# TASK-118H — Rollout, resiliência e documentação

Status: **validação DEV concluída, aguardando revisão**, após primeira rodada
aprovada. Sem deploy PROD, commit ou push. Runbook: `docs/operations/cesar-core-runbook.md`.

## Base aprovada e escopo

118A–118G implementadas. Últimos commits publicados: GG Oferta `80dc135` e
César Core `3578f2b`. Implementar agora: etapa operacional já prevista na
TASK-118, sem novas features de negócio, banco/migrations ou frontend.
O trabalho paralelo do Claude, `CLAUDE.md` e arquivos locais são preservados.

Validar AI/Search ponta a ponta, indisponibilidade de Core/OmniRoute/provider,
timeouts, 429, resposta malformada, restart, observabilidade e rollback. Preparar
runbook DEV→PROD, mas não executar nem declarar rollout PROD sem autorização.
Demonstrar extensibilidade para Claudião sem ativá-lo ou criar sua credencial.

## Preflight desta rodada

- Python oficial 3.14.6 disponível.
- Arquivos das credenciais DEV de aplicação nos dois repositórios, OmniRoute e
  Firecrawl presentes; valores não exibidos. Presença não prova validade atual.
- Docker inicialmente indisponível; usuário autorizou iniciá-lo. Ao executar,
  Desktop já estava rodando. Engine, imagens certificadas e recursos foram
  conferidos; containers originais permaneceram parados.
- Stack real descartável `*-118h-validation`, rede exclusiva e cópia do banco
  OmniRoute aberta a partir da origem somente leitura; nenhuma alteração na origem.
- Baseline a reutilizar: OmniRoute 3.8.50, digest já certificado na 118B–118G;
  SearXNG com digest do ADR 0017 do Core, ambos confirmados. Sem troca de versão.
- O harness anterior corrigido é local/ignorado. A 118H deve tornar a execução
  reproduzível sem versionar cópias de bancos, credenciais ou provas brutas.

## Matriz de aceitação

Baseline anterior é distinguido das execuções desta rodada abaixo.

| Cenário | Baseline existente | Validação exigida na 118H |
| --- | --- | --- |
| AI tipada e normalizada | contract real 118F e 22 contracts Core na 118G | PASS: 22/22 Core e positivo GG |
| Search SearXNG | contract real GG na 118G, 3 resultados | PASS: cap, cache e IDs no contract GG |
| Core indisponível | conexão recusada real na 118F/118G | PASS: processo terminado, novo PID e mesmos managers GG recuperados |
| OmniRoute indisponível | transporte possui testes de erro | PASS: parada real, AI 503 normalizado, Search fallback, recuperação |
| Provider indisponível | SearXNG parado com readiness degraded na 118G | PASS: parada real, degraded, Search fallback e recuperação |
| Timeout, 429, malformed response | testes focados de adapters e transporte | PASS: focados timeout; quota real; malformed HTTP controlado sem fallback |
| Auth, capability, quota | contracts reais 118E/118G | Preservar rejeição pré-upstream e ausência de fallback |
| Restart | sem prova consolidada 118H | PASS: Core com novo PID, quota local reiniciada e recuperação sem restart GG |
| Rollback | flags/factories existentes com testes | PASS: Core→Gemini/cascata anterior e Firecrawl→Core, flags e reload de factories |
| Observabilidade | métricas/traces nos contracts anteriores | IDs, resultado, usage, fallback e scrape separados |
| Claudião | registry RESERVED, sem scopes | Demonstrar extensão dos contratos sem ativação real |

Falhas HTTP controladas não devem ser apresentadas como falhas naturais do
provider real. Os positivos usam serviços reais; não aceitar apenas HTTP 200.

## Semânticas que não mudam

- AI: disaster opt-in somente em erro de conexão; timeout incerto, HTTP de erro,
  resposta inválida e cap violado falham fechado, sem duplicar geração local.
- Search: Firecrawl `/v2/search` somente em indisponibilidade elegível;
  vazio/401/403/quota/policy não acionam fallback.
- Firecrawl `/v2/scrape` é enriquecimento independente, até 3 URLs selecionadas;
  snippets fracos não geram uma segunda busca.
- `max_tokens`: enforcement por capability certificada e pós-condição fail-closed.
- `max_results`: cap obrigatório da saída; não promete limitar aquisição SearXNG.
- `FREE_ONLY`, credenciais separadas, finalidade/classe e grounding preservados.
- Flags permanecem false nos defaults; Claudião RESERVED e sem credencial.

## Limites operacionais a resolver antes do rollout

Os adapters GG usam `normalize_loopback_http_endpoint`: o caminho validado é
processo local → Core HTTP loopback. O Compose Core é conceitual, sem Dockerfile;
não constitui deployment executável. O acesso container→Core precisa de uma
definição explícita de topologia/transportes confiáveis; não liberar URLs
arbitrárias nem trocar loopback por `0.0.0.0` como atalho.

Quota do Core é fixed-window em memória, por processo. Reiniciar zera a janela;
múltiplos workers/réplicas não compartilham contador. Não afirmar persistência
ou quota global. Preparação de produção deve declarar número de processos e
aceitação dessa semântica ou obter decisão para backend persistente/distribuído.

## Sequência de execução

1. Restabelecer Docker local, sem tocar stacks/dados compartilhados.
2. Preparar stack isolado e harness com Settings sem dotenv compartilhado,
   secrets por arquivo, saída sanitizada e cleanup em finally.
3. Reexecutar baselines reais, completar matriz de falhas/restart/rollback.
4. Registrar resultados, dependências e limites reais; corrigir somente escopo 118H.
5. Revisar docs, testes focados, Ruff e diff-check; parar para revisão do rollout.
6. Produção somente após autorização separada e preflight do destino.

## Segurança do histórico anterior

A exposição inicial de senha DEV na 118G foi aceita pelo usuário como não
bloqueante. Não editar histórico local do Codex, não repetir a senha, não
rotacionar automaticamente nem reabrir auditoria ampla como requisito desta task.

## Resultado atual

Documentação 118F/118G sincronizada. Nenhuma implementação de domínio alterada.
Harness `C:\cesar-core\scripts\run_118h_contracts.py`: fases `core`, `gg`,
`resilience`, `recovery` e subprocesso `serve`; argumento `--gg-repo`.
`scripts/stack_118h_dev.py up/down` prepara/limpa exclusivamente o stack DEV
descartável por nome, label e marcador de diretório. Não é deployment PROD.
Settings isolados sem dotenv, flags herdadas removidas e erros sem inputs.
AI/Search usam caminhos de credenciais distintos; a cópia temporária da chave
upstream não é uma alegação de duas credenciais emitidas independentemente.

Primeira execução Core: 20 passaram/2 falharam porque o novo harness herdou
AI+Search nas fixtures históricas. Corrigido apenas o harness: cada fixture
volta a definir suas capabilities/credenciais; assertions não foram enfraquecidas.
Repetição final: **22/22 Core em 35,55s**. **2/2 GG em 26,45s** (AI e Search).
Novo `tests/test_118h_resilience_contract.py`: **2/2 em 48,09s**, paradas reais de
OmniRoute/SearXNG, readiness degraded→ok, fallback Search Firecrawl, recuperação
para SearXNG com resultados e sem nova chamada Firecrawl. Falha de OmniRoute
também gerou AI 503 `ai_upstream_unavailable` com correlation ID preservado.
Firecrawl real consome créditos; não confundir gratuidade SearXNG com fallback.
Teste focado de isolamento do harness: **1/1**. Ruff dos arquivos novos passou.
Diff-check passou nos dois repositórios. Containers/rede de validação removidos;
diretório temporário com banco copiado e chaves de teste limpo. Hash do banco
original e do prompt preservado permaneceu idêntico. Docker Desktop continua
disponível; containers originais permaneceram parados.

## Continuação solicitada — matriz final

| Casos | Componentes e prova | Resultado |
| --- | --- | --- |
| A/F | GG→Core→OmniRoute AI e SearXNG Search reais | Sucesso, conteúdo normalizado |
| B/G | Core terminado, clientes GG mantidos | AI erro normalizado; Search Firecrawl; após novo PID, Core/SearXNG |
| C/H | OmniRoute parado | AI 503 no Core e erro tipado no GG; Search fallback; dependência recupera |
| I | SearXNG parado | Ready degraded, Search fallback; após retorno usa principal sem estado residual |
| D/L | Credencial aplicação inválida | 401, erro GG não elegível, contador passivo upstream não muda |
| E/N | Quota real 1/min em processo isolado | AI/Search 429, uma rejeição por rota, nenhuma nova chamada upstream |
| J | Query real sem resultados | Sucesso vazio, sem fallback (contract GG) |
| K | HTTP 400 injetado por socket local | Cliente Search não usa fallback configurado |
| M | Registry do Core isolado nega Search | 403 real, sem upstream/fallback |
| O | Core 503 e Firecrawl 503 injetados por HTTP local | Erro `firecrawl_search_failed`, uma tentativa Core e três Firecrawl, sem loop |
| Malformed | HTTP 200 não JSON local | Erro normalizado não elegível, sem fallback |

Não se apresenta falha HTTP injetada como indisponibilidade natural do serviço
Firecrawl. Positivos e rollback Firecrawl/Gemini usam APIs reais. O novo teste
de quota inicialmente procurava uma métrica inexistente chamada `quota`; corrigido
para o contador HTTP 429 real por rota, preservando prova de sockets invariantes.
O teste de retries inicialmente configurava delay zero inválido; corrigido para
0,01s, três tentativas. Nenhuma implementação ou assertion histórica enfraquecida.

Rodada final complementar: **7/7 em 61,76s**, incluindo privacidade de todos os
registros novos e logs dos processos Core. Core **22/22 em 34,17s**; GG AI/Search
**2/2 em 10,31s**. Focados GG **34/34**; harness/guardas do provisionador **4/4**.
Dependências finais: **2/2 em 41,26s**, incluindo erro tipado no manager AI do GG
quando OmniRoute fica fora. Cleanup executado: rede, containers, banco copiado,
chaves e logs temporários removidos. Banco original e prompt com hashes idênticos.
Ruff completo do Core e testes novos GG passou; diff-check passou nos dois repos.

Rollback foi provado recarregando Settings/factories: não é promessa de hot reload.
Runbook prescreve restart apenas do consumidor afetado para mudança de configuração.
Nenhum rollback de banco, migration, exclusão de dados ou restart GG por simples
recuperação de dependência. Limites residuais: HTTP loopback DEV; quota em memória;
API/modelo gratuito e motores externos sujeitos a disponibilidade/cota; Firecrawl
pode consumir créditos. Não há aprovação de deploy/rollout PROD nesta rodada.

## Continuação — Control Plane administrativo do Core e quota via API Admin

O César Core ganhou um Control Plane administrativo próprio (SQLite, sessão
Argon2id, CSRF), já commitado em `cesar-core` (`8b489a2`). Isso mudou onde a
quota vive: a **política** (limite AI/Search por aplicação) passou a morar em
`quota_policies` (SQLite do Control Plane), lida por `get_store().get_quota(...)`
em toda admissão; o **consumo** (contador da janela de 60s) continua em Redis,
por namespace — ver ADR 0018 (`docs/adr/0018-persistent-quota-recovery.md` no
repositório `cesar-core`), que não é duplicado aqui.

**Isso supera as afirmações anteriores** de "quota em memória, por processo,
reinicia com o Core" (linha 75–78 e a linha "Restart" da matriz de aceitação
acima): reiniciar o processo Core não recria a janela nem devolve admissões
já consumidas. O teste histórico que esperava esse reset foi reescrito.

O argumento `--quota` do harness (`run_118h_contracts.py`) ficou com função
**apenas** de semente do bootstrap: só tem efeito na primeira vez que o SQLite
do Control Plane daquele processo ainda não existe. Depois que a linha
`gg_oferta/<capability>` já existe (bootstrap anterior ou mudança pela API
Admin), `--quota` não altera mais o limite efetivo. O harness ganhou também
`--admin-database`, `--admin-password-hash-file`, `--admin-pepper-file` e
`--admin-allowed-origin` para permitir um Control Plane administrativo
descartável e isolado por processo — usado exclusivamente pelo caso de teste
de quota, nunca pelos demais (que continuam usando a configuração default).

`tests/test_118h_recovery_contract.py::test_real_quota_persists_across_core_restart`
foi reescrito para configurar a política pela API administrativa real do Core
(`POST /admin/api/login`, `GET/PUT /admin/api/applications/gg_oferta`, sessão +
`X-CSRF-Token`), nunca por escrita direta em `quota_policies`, confirmando a
política efetiva por uma segunda leitura antes de prosseguir. Prova, nessa
ordem: quota AI=1 configurada pela API; primeira chamada real 200 (conteúdo
não vazio); contador e TTL existem no Redis do namespace isolado desse caso;
segunda chamada rejeitada (429, código `cesar_core_request_failed`, não
retryable, zero novas chamadas upstream via `/metrics`); restart real do
processo Core (mesmo Redis/SQLite/GG Oferta/OmniRoute intactos); contador e
TTL preservados após o restart; terceira chamada (pós-restart) também
rejeitada, com zero chamadas upstream nesse novo processo. Repetição da
suíte completa: **7/7 PASS**. Foco em `tests/test_web_search_manager.py`
(`quota_store_unavailable`/`quota_store_misconfigured` não retryable, sem
fallback Firecrawl): **19/19 PASS**, sem alteração de escopo.

Isolamento: SQLite, hash de senha Argon2id e pepper do Control Plane de teste
são gerados em arquivo temporário só para esse caso e apagados explicitamente
ao final (não dependem só da limpeza automática de diretório temporário do
pytest); a quota real do `gg_oferta` no ambiente persistente
(`.data/control-plane.sqlite3` do GG Oferta, usado pelos outros seis testes
desta suíte) não foi tocada. Nenhum `FLUSHALL`/`FLUSHDB`/`DELETE` global foi
usado; isolamento vem só de namespace Redis exclusivo por caso (já existente)
e do SQLite/segredos descartáveis novos.
