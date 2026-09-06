# Handoff para Claude — César Core 1.0.0 em DEV

Registrado em 2026-09-04 por solicitação do usuário. **Executar somente em DEV**.
Este arquivo não declara o rollout GG concluído. Não alterar PROD, frontend,
dados de negócio ou trabalho paralelo; não incluir alterações alheias em commits.

## Release disponível

- Core local: `C:\cesar-core`.
- Tag imutável: `v1.0.0`.
- Commit da release: `b22a5e88b8767657ce919b03886a52f5b2645cf3`.
- Imagem privada: `ghcr.io/jhonnatancesar/cesar-core:1.0.0`.
- Referência para DEV reproduzível:
  `ghcr.io/jhonnatancesar/cesar-core@sha256:e96feababeec5626ecf0fd696e0c46188c8e0009cbd0092166d91088125bf3ad`.
- Actions aprovado: https://github.com/jhonnatancesar/cesar-core/actions/runs/33902224388.
- Código/documentação enviados para main do Core. Repositório e package privados,
  todos os direitos reservados. Acesso ao GHCR exige autorização de leitura.

Fontes: README, compose.yaml, docs/deployment/docker.md,
docs/deployment/release-1.0.0-validation.md e ADRs 0018/0019 no Core.
Não confundir a tag 1.0.0 do **Core** com tags históricas do GG Oferta.

## O que já foi provado no Core

232 testes de regressão/configuração, 22 contracts baseline e nove contracts
quota/recovery passaram. Imagem publicada foi baixada e executada por digest:
health/ready ok, AI real com messages e texto CESAR_RELEASE_OK (24 completion
tokens, cap 512), Search SearXNG com três resultados reais (max_results=3),
cache false→true, quota 429 e contador preservado após restart Core e stack.
Verificação das credenciais usadas em logs/respostas passou.

Isso não substitui o E2E **do consumidor GG** após seus ajustes. Containers,
volumes e arquivos `*-validation` foram apenas provas descartáveis; porta 18100
não é um serviço permanente para configurar no GG.

## Mudança obrigatória a absorver

A quota Core **não é mais em memória**. Redis compartilhado com AOF/always,
noeviction, no-appendfsync-on-rewrite=no; sem fallback em memória. Namespace
estável por ambiente e janela de 60s desde a primeira admissão. Restart não
renova quota nem TTL. Readiness verifica CONFIG GET/INFO, não apenas PING.

O runbook `cesar-core-runbook.md` e TASK-118H locais ainda contêm trechos
históricos sobre quota volátil: atualizar esses trechos ao retomar, preservando
a proveniência dos resultados antigos. A ADR 0018 do Core é a autoridade atual.

1. Corrigir o contract
   `tests/test_118h_recovery_contract.py::test_real_quota_pre_upstream_and_restart_resets_local_window`:
   agora precisa provar **persistência**, não sucesso por reset após restart.
   Manter Redis/namespace e TTL; não enfraquecer o teste nem apagar contadores
   para simular recuperação. Isolar namespaces de teste desde o início.
2. Tratar distintamente `quota_exceeded` (429), `quota_store_unavailable` (503)
   e `quota_store_misconfigured` (503). O adapter Search atual em
   `backend/app/search/cesar_core.py` classifica 503 genericamente como retriável;
   esses códigos novos ainda não estão na lista de rejeições não elegíveis.
   Não permitir que uma falha da proteção de quota seja contornada silenciosamente
   por Firecrawl. Preservar fail-closed; se surgir conflito de policy, parar e
   apresentar a decisão necessária, não afrouxar automaticamente.
3. Não alterar o Core para acomodar um contract GG obsoleto. Código GG e harnesses
   devem refletir a garantia durável aprovada.

## Preflight e ativação DEV

Verificar branch/worktree e alterações do Claude antes de qualquer edição.
Ler instruções locais, task e runbook. Identificar se o consumidor roda nativo
ou em container. Ambos adapters GG hoje aceitam **somente HTTP loopback**;
127.0.0.1 dentro de container não é o host. Para a primeira prova, usar GG nativo
DEV → porta loopback publicada do Core. Não liberar URLs arbitrárias nem expor
0.0.0.0 para contornar isso. Uma topologia diferente exige validação específica.

Na raiz Core, seguir o runbook oficial de primeiro boot: quatro imagens separadas,
secrets locais, target AI certificado e conexão searxng-search com URL interna
http://searxng:8080/search. Não substituir a imagem oficial OmniRoute 3.8.50.
Usar CESAR_CORE_IMAGE com o digest acima; não buildar no servidor por necessidade.
Não iniciar/remover containers compartilhados sem resolver conflitos de portas.

Credencial GG→Core já tem arquivo DEV próprio no GG:
`.secrets/cesar-core-client-dev`. No Core existe a cópia DEV em
`.secrets/ggoferta-core-client-dev`. Verificar correspondência apenas em memória,
sem imprimir o valor. Não reutilizar chave OmniRoute, não rotacionar/recriar
sem motivo, não criar credencial Claudião. Chaves upstream AI/Search ficam no
Core/OmniRoute, em arquivos separados, nunca no cliente GG.

Configuração do processo consumidor DEV (nomes reais):

```powershell
$env:AISHOPPING_ENVIRONMENT='development'
$env:AISHOPPING_CESAR_CORE_BASE_URL='http://127.0.0.1:8100'
$env:AISHOPPING_CESAR_CORE_API_KEY_FILE='C:\AIShoppingAgent\AIShoppingAgent\.secrets\cesar-core-client-dev'
$env:AISHOPPING_CESAR_CORE_MAX_TOKENS='512'
```

A porta 8100 é padrão, confirmar no deployment DEV escolhido. Não existe Search
fallback local. Reiniciar apenas consumidor afetado
após mudar Settings/factories; não presumir hot reload. Swagger Core: /docs.

## Critérios de aceite GG DEV

- AIProviderManager → CesarCoreAIProvider → Core → OmniRoute: texto real,
  messages system/user/assistant sem concatenação, usage e max_tokens.
- Market Research → WebSearchManager → CesarCoreSearchProvider → Core →
  OmniRoute → SearXNG: resultado não vazio, título/URL/snippet/source, limite=3,
  cache consistente e request/correlation/upstream IDs.
- Sem Bearer/inválido: 401; capability negada: 403; quota: 429 pré-upstream.
- Restart Core mantém quota; Redis inadequado/fora bloqueia gateway, sem bypass
  de quota por fallback. Recuperação não apaga contadores.
- Firecrawl /v2/scrape continua enriquecimento separado por evidência insuficiente,
  até o limite atual de três URLs/domínios conforme implementação; não é fallback.
- Grounding passa pelo Core. FREE_ONLY preservado. Créditos
  Firecrawl observáveis, sem ampliar volume/custo só para testar.
- Executar testes focados e contracts reais do GG afetados; verificar ausência de
  secrets nos outputs; atualizar documentação histórica da quota e registrar
  resultados honestos. Não apresentar mocks como E2E real.
- Falha de Core/configuração é fail-closed; não há rollback para provider direto.

## Limites deste handoff

Somente registro documental por Codex: nenhuma implementação, configuração local
ou processo do GG foi alterado nesta rodada. Arquivos já modificados pelo Claude
e documentos 118H preexistentes ficaram intactos. A execução do plano e aprovação
do E2E GG são o próximo trabalho do Claude, não resultado presumido deste arquivo.
