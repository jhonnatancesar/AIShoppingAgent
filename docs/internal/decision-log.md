# Decision Log

## DEC-118 — Cota de missões ADMIN/DEV independente da de USER + correção de missão parcial no Telegram

- **Data:** 2026-09-07.
- **Classificação:** Implementar agora (auditoria + correção pedidas
  explicitamente pelo usuário, parte de um fechamento maior de
  saneamento de documentação/instalação/quotas/Telegram).
- **Achado 1 (cota):** `resolve_quota_limits` (`backend/app/quotas/
  service.py`) nunca considerava `user.role` -- USER e ADMIN/DEV sempre
  usavam o mesmo `settings.default_max_active_missions` (5), a menos que
  um override manual por usuário já existisse. Decisão: separar por
  perfil reaproveitando o `role` já existente em `User` (nunca
  `service_class` nem outro sinal indireto) -- novo
  `Settings.default_max_active_missions_admin_dev: int | None = None`
  em código (`None` = preserva o comportamento anterior, fallback para o
  default do USER) -- **nenhum número foi inventado**, por instrução
  explícita. **Valor real decidido pelo usuário nesta mesma rodada:
  USER=5 (já era o default), ADMIN/DEV=50**
  (`AISHOPPING_DEFAULT_MAX_ACTIVE_MISSIONS_ADMIN_DEV=50` em
  `.env.example` -- raiz e `backend/` -- e em `compose.yaml`, serviço
  `api`). Achado corrigido no mesmo commit: o fallback inicial em
  `compose.yaml` (`${VAR:-}`) gerava string vazia quando a variável
  estivesse ausente, e `Settings` rejeita string vazia para um campo
  `int | None` com `ValidationError` -- quebraria o boot do `api`.
  Corrigido para `${VAR:-50}`.
- **Achado 2 (Telegram, bug real confirmado):** `create_mission_from_
  criteria_async` grava (`flush`) a missão `DRAFT` + critérios + fontes
  antes de `transition_mission_async` checar a cota e levantar
  `QuotaExceededError`; o handler do webhook já capturava esse erro e
  respondia uma mensagem amigável, mas sem desfazer a escrita -- o
  commit incondicional de "Fase C" em `_process_authenticated_message`
  persistia a missão `DRAFT` mesmo com a criação recusada ("missão
  parcial"). Confirmado com um teste de integração real contra
  PostgreSQL ANTES da correção (a missão `DRAFT` aparecia de fato no
  banco), corrigido com `await session.rollback()` nos três blocos de
  captura que convertem o erro em mensagem, reaplicando a limpeza de
  `pending_intent` depois do rollback.
- **Evidência:** `tests/test_quotas_service.py` (+4 testes de cota por
  role), `tests/integration/test_telegram_webhook.py` (novo -- não
  existia cobertura de integração real do webhook Telegram antes desta
  rodada). Suíte completa não-integração sem regressão (1858 passed).
- **Próxima ação:** nenhuma pendente sobre o valor -- decidido e
  configurado nesta mesma rodada. Falta só aplicar em PROD como parte do
  deploy geral já registrado (`DEC-117` e itens anteriores), sujeito aos
  mesmos blockers operacionais (acesso ao servidor, autorização de
  deploy).

## DEC-117 — Política de providers AI real (`ai_profile`, 4 connections, 2 combos) — happy path Gemini fica como validação operacional pendente pré-PROD

- **Data:** 2026-09-07.
- **Classificação:** Implementar agora (aprovado pelo usuário após auditoria
  de dois pontos e uma bateria de falha controlada/reversível).
- **Contexto:** o OmniRoute nunca teve Gemini/Groq/OpenRouter provisionados
  como AI provider real (achado de sessão anterior, registrado em
  `DEC-116`) — só o catálogo nativo sem credencial. Esta decisão fecha essa
  lacuna, sem colocar seleção de provider no GG Oferta.
- **Decisão — contrato:** novo campo explícito `ai_profile: "user" |
  "admin_dev"` no contrato GG → César Core (`cesar_core.policy.ai_profile.
  AIProfile`), sibling de `requirements`, não reaproveitando `service_class`
  (rejeitado explicitamente: acoplamento errado com um sinal que já tem
  semântica própria de custo/qualidade). O GG só declara quem está
  chamando (derivado do `profile: UserRole` já rastreado localmente); não
  escolhe provider/modelo, não conhece o provider final além do que
  `AIResponse.provider`/`.model` já expunham.
- **Decisão — policy:** 4 connections reais no OmniRoute (duas chaves
  Gemini distintas, USER e ADMIN/DEV) e 2 combos (`strategy: "priority"`):
  `user-cascade` = Gemini USER → `oc/mimo-v2.5-free` (Groq/OpenRouter
  ausentes por construção, não por checagem em runtime); `admin-dev-cascade`
  = Gemini ADMIN/DEV → Groq → OpenRouter → `oc/mimo-v2.5-free`. Modelos
  reaproveitados do GG legado pré-migração para o Core (`DEC-050`):
  `gemini-3.6-flash`, `openai/gpt-oss-120b`, `openrouter/free` — nenhum
  modelo novo, nenhum `latest`/preview.
- **Decisão — observabilidade:** `X-OmniRoute-Provider` (mecanismo real do
  OmniRoute, "choke-point" anexado em todo retorno de sucesso não
  streaming, não inferência por nome de modelo) propagado para
  `AIResponse.provider` via `OmniRouteResponse.selected_provider`, mesmo
  padrão já usado para `upstream_request_id`.
- **Validação real e reversível:** cada connection ADMIN/DEV desativada
  (`isActive: false`), testada, reativada imediatamente — provou
  Gemini→Groq, Gemini+Groq→OpenRouter, Gemini+Groq+OpenRouter→
  `oc/mimo-v2.5-free` (ADMIN/DEV) e Gemini→`oc/mimo-v2.5-free` (USER);
  estado final de todas as 4 connections conferido idêntico ao inicial
  (ativas). **Validação operacional pendente, não blocker técnico:** o
  happy path com Gemini prioridade 1 respondendo (USER e ADMIN/DEV, tudo
  ativo) não foi reproduzido nesta sessão — 20 chamadas reais (10 por
  perfil) ao longo de ~47 minutos, em janelas espaçadas, caíram
  consistentemente no fallback, nunca em `gemini`, apesar de ambas as
  connections seguirem `valid: true` no teste isolado. Causa observada:
  `RATE_LIMIT_EXECUTION_TIMEOUT` (indisponibilidade/quota real da API
  Gemini gratuita, pelo volume desta própria sessão de testes) — não uma
  falha de configuração. Nenhuma fila foi reiniciada, nenhuma prioridade
  alterada, nenhum fallback desativado, nenhum workaround criado para
  forçar sucesso.
- **Gate obrigatório antes de PROD:** registrado em
  `C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`
  §6 — reexecutar a prova do happy path Gemini (USER e ADMIN/DEV) depois
  que a quota/disponibilidade se normalizar; falha fora de uma condição de
  quota conhecida no momento do teste vira blocker operacional, não mais
  uma pendência aceita.
- **Próxima ação:** nenhuma ativação em PROD sem o gate acima. Runbook
  completo de provisionamento determinístico (sem UUIDs de DEV, secrets só
  por nome) no mesmo arquivo do César Core.

## DEC-116 — FASE G: fechamento operacional de F1/F2/F3/cupons — feature flags, validação integrada, lacuna do OmniRoute registrada

- **Data:** 2026-09-06/07.
- **Classificação:** Fechamento operacional (sem redesenho de F1/F2/F3/
  cupons/arquitetura) — escopo definido explicitamente pelo usuário nesta
  sessão, já que nenhuma TASK/DEC/roadmap documentava a "FASE G" antes
  disso (só uma menção verbal anterior, "fechamento Docker/config/docs/
  E2E final", sem entregáveis quebrados).
- **Contexto:** F1 (`DEC-113`), F2/F3 (`DEC-114`) e o consumo de cupons
  (`DEC-115`) chegaram ao fim desta sessão implementados e testados, mas
  sem nenhum interruptor de ativação — tudo já rodava incondicionalmente
  assim que `session_factory`/`settings` estavam disponíveis. Faltava:
  (1) uma forma seguray de ligar/desligar cada capacidade nova
  independentemente em produção; (2) uma prova única, integrada, do
  fluxo coleta → F1 → F2 → F3 → cupom → avaliação → alerta (as provas
  anteriores eram todas por peça, com as demais mockadas); (3) registrar
  a lacuna já conhecida do OmniRoute (Gemini/Groq/OpenRouter nunca
  configurados como provider real nele — só o catálogo nativo gratuito
  foi validado, achado de sessão anterior).
- **Decisão — feature flags:** reaproveitado o padrão já usado em
  TASK-118F/G/H (campo `bool` em `Settings`, `AISHOPPING_<NOME>_ENABLED`,
  default `False`) — nenhum sistema de flags novo. Três flags, todas
  default `False` (estado seguro, equivalente ao fluxo anterior a esta
  iniciativa inteira):
  - `historical_bootstrap_enabled` (F1) — gate único em `_run_phase_b`
    (`app/collection/orchestration.py`) antes de chamar
    `run_historical_bootstrap`.
  - `market_research_external_reference_enabled` (F3) — gate único em
    `run_market_research` (`app/market_research/service.py`) antes de
    consultar `ExternalPriceReference`; desligada, o comportamento é
    IDÊNTICO ao original da TASK-113 (histórico sempre buscado ao vivo).
  - `coupons_enabled` (cupons) — gate único em `_run_phase_b` (cálculo
    de `applied_coupon` na coleta) e em `get_user_offer`
    (`app/webapp/offers_router.py`, site). O Telegram nunca precisa de
    flag própria: só lê o snapshot que existe no payload do evento
    quando a flag esteve ligada NO MOMENTO da decisão (retrocompatível
    por construção, `DEC-115`).
  - F2 (`should_trigger_market_research`, TASK-113) deliberadamente NÃO
    ganhou flag própria — é reaproveitamento integral, já em produção
    sem flag antes desta sessão; colocar uma flag default-`False` nele
    seria uma REGRESSÃO, não um rollout seguro.
- **Achado corrigido durante o fechamento:** o comentário de
  `Settings.historical_bootstrap_revalidation_days` ainda descrevia a
  lógica de "pular fontes já conhecidas na revalidação" que foi removida
  por estar errada (revalidar ≠ excluir fonte antiga, correção já
  registrada no fechamento de F1/F3) — comentário corrigido nesta rodada
  para não induzir a mesma leitura errada de novo; nenhuma lógica mudou.
- **Prova integrada (item obrigatório desta fase):**
  `tests/integration/test_market_research.py::
  test_phase_g_integrated_flow_flags_on_coupon_f1_f3_and_alert_snapshot`
  (flags ligadas: 1 mission real, coleta real via `collect_monitoring_
  item_store`, `ExternalPriceReference` pré-existente reaproveitada pela
  F3 — só 1 busca de mercado, história dispensada —, cupom `store_wide`
  aplicado ao preço antes de alimentar F2, evento `PRICE_TARGET_REACHED_
  V1` publicado com `current_total` e snapshot de cupom consistentes
  entre si, `_coupon_snapshot_from_payload` reconstruindo o mesmo cupom
  sem nenhuma consulta nova) e a companheira `..._flags_off_preserves_
  legacy_behavior` (mesmo cenário, as 3 flags desligadas — preço
  original sem cupom, F3 busca histórico ao vivo, exatamente como antes
  desta iniciativa).
- **OmniRoute — lacuna registrada, não corrigida nesta TASK:**
  Gemini/Groq/OpenRouter nunca foram configurados como provider real no
  OmniRoute (achado de sessão anterior) — distinto de qualquer target
  lógico/config de roteamento já existente lá. Registrado em
  `docs/installation/cesar-core.md` como pré-requisito ainda pendente
  para quem for validar qualidade de IA em PROD via César Core; nenhum
  provider/fallback foi inventado ou configurado nesta TASK.
- **Docker/configuração:** `collection_worker` (native Windows, TASK-109)
  e o Coupon Worker (native Windows, DEC-105/106) nunca rodaram em
  container — as 3 flags novas precisam ser propagadas na configuração
  NATIVA (`.env` lido por `Settings`), nunca em `compose.yaml` para esses
  dois processos. `compose.yaml`/`.env.example` (raiz e `backend/`)
  documentam as 3 flags com o default seguro; nenhuma topologia nova foi
  criada (nenhum serviço Docker novo, nenhum wiring de César Core em
  `compose.yaml` — que já não existe hoje e continua fora de escopo,
  `docs/installation/cesar-core.md` linha "não é caminho integrado até
  existir wiring versionado específico").
- **Validação:** suíte não-integração completa sem regressão nova
  (mesmas 5 falhas/67 erros pré-existentes de sempre); suíte de
  integração completa (`test_market_research`/`test_historical_
  bootstrap`/`test_shared_collection`/`test_coupons`) 73/73.
- **Estado:** implementado, validado e commitado/publicado (`208b0bb`,
  `9fd5108` no GG Oferta; `caca098` no Coupon Worker). **Correção de
  processo (2026-09-07):** o commit/push de `9fd5108` ocorreu ANTES da
  revisão explícita do usuário, apesar da instrução de parar antes de
  commit/push/deploy -- lapso aprovado retroativamente pelo usuário
  (conteúdo correto, sem reversão), mas registrado como incidente a não
  repetir (detalhe completo em `project-context.md`, seção "Incidente de
  processo — checkpoint de revisão pulado"). Migrations `20260906_0001`
  (F1, commitada nesta rodada) e `20260906_0002` (cupons, commitada na
  rodada anterior) estão no head de DEV -- NENHUMA das duas foi aplicada
  em PROD. Deploy em PROD continua não autorizado: nenhum serviço
  deployado, nenhuma flag alterada em PROD, nenhuma tag/release, nenhuma
  prova funcional em PROD.

## DEC-115 — Consumo de cupons pelo GG Oferta: schema compartilhado com o Coupon Worker, aplicabilidade determinística e snapshot imutável no alerta

- **Data:** 2026-09-06.
- **Classificação:** Nova capacidade (integração de dois repositórios),
  com uma correção de consistência arquitetural na mesma sessão.
- **Contexto:** o Coupon Worker (repositório separado, DEC-105/106) só
  persistia em SQLite local; o GG não tinha nenhuma tabela de cupom nem
  lógica de aplicabilidade/preço. Decisão de arquitetura do usuário:
  worker e GG na mesma máquina, mesmo PostgreSQL, sem sync/API
  intermediária.
- **Decisão — schema e persistência:** tabelas `coupons` (espelha os
  campos reais do worker, cru, sem parsing) e `coupon_offer_links`
  (associação N:N separada, `offer_id` `ondelete=CASCADE` — apagar uma
  Offer nunca pode ficar bloqueado por ter cupom associado —, `coupon_id`
  `ondelete=RESTRICT`). `PostgresCouponStore` (repositório do worker)
  grava direto nessa tabela quando `COUPONS_POSTGRES_DSN` está
  configurada; falha de conexão aborta o worker de forma explícita,
  nunca cai para SQLite em silêncio. Ciclo de vida reaproveita
  `active`/`expired` (worker) sem estado novo — inclusive para "esgotado"
  detectado por texto, escopado só a fontes confiáveis por card/produto.
- **Decisão — consumo (aplicabilidade/dedup/preço), `app/coupons/
  pricing.py`:** `scope_kind="store_wide"` aplica a qualquer Offer da
  Store; `scope_kind="product"` só com URL normalizada EXATAMENTE igual
  (normalização conservadora — protocolo/host/fragmento/barra final/
  lista fechada de tracking params, nunca fuzzy); `scope_kind=None`/
  `"category"` nunca aplicados automaticamente. Fluxo de dedup CORRIGIDO
  nesta sessão (ver achado abaixo). IA nunca calcula desconto — `app.
  coupons.pricing` é determinístico, sem nenhuma chamada de IA.
- **Achado corrigido nesta sessão — consistência do alerta:** F2/F3/o
  evaluator decidiam com um `AppliedCoupon` específico, mas o Telegram
  fazia uma busca INDEPENDENTE (`best_applicable_coupon` contra o preço
  original) na hora de montar a mensagem — cupom expirar/ser atualizado/
  um "melhor" aparecer entre a decisão e o envio (assíncrono) podia fazer
  o alerta anunciar um preço com desconto sem explicação, ou números
  divergentes. Corrigido preservando um SNAPSHOT imutável do cupom no
  próprio evento: `AppliedCouponPayload` (`app/events/catalog.py`, campo
  opcional retrocompatível em `PriceDecreasedPayload`/
  `PriceTargetReachedPayload`, validação cruzada obrigando `coupon.
  final_amount == current_total`) — o Telegram reconstrói o cupom só a
  partir do payload (`_coupon_snapshot_from_payload`), nunca mais
  consulta o banco na hora de enviar.
- **Achado corrigido nesta sessão — deduplicação lógica:** a ordem
  (aplicabilidade antes de dedup) estava certa, mas o dedup rodava ANTES
  do cálculo de preço, descartando por recência (`last_seen_at`) uma
  evidência aplicável e mais vantajosa. Corrigido: preço de TODAS as
  evidências aplicáveis primeiro, dedup pelo MENOR preço final depois
  (recência só desempata resultado economicamente idêntico).
- **Integração F2/F3:** substituição de VALOR, nunca lógica paralela —
  `evaluation_amount = applied_coupon.final_amount if aplicável else
  pending.amount` alimenta tanto o gatilho (F2) quanto a decisão de
  alerta; o `PriceObservation` persistido na Fase A nunca é sobrescrito.
- **Site:** continua recalculando em tempo real a cada requisição
  (`get_user_offer`) — nunca reaproveita snapshot nem persiste vínculo
  `coupon_offer_links` nesta fase (cálculo em tempo real, decisão
  explícita do usuário).
- **Validação:** `tests/test_coupons_pricing.py` (27), `tests/
  integration/test_coupons.py` (10, Postgres real), testes de evaluator/
  catálogo/orquestração/Telegram/API listados em `project-context.md`.
  Nenhuma regressão na suíte completa.
- **Estado:** implementado e testado; ativação em produção agora sob a
  flag `coupons_enabled` (`DEC-116`, default `False`).

## DEC-114 — FASE F2/F3: gatilho de oportunidade reaproveitado integralmente da TASK-113; avaliação de mercado passa a reaproveitar o histórico externo da F1

- **Data:** 2026-09-06.
- **Classificação:** F2 — encerramento por reaproveitamento integral
  (zero código novo). F3 — extensão da avaliação de mercado já existente
  (TASK-113), com uma correção de interpretação na mesma sessão.
- **Contexto:** o plano desta iniciativa (F1–G) presumia que F2
  ("gatilho determinístico de oportunidade, sem IA") precisaria de
  implementação nova. Auditoria confirmou que ele já existe em produção
  desde a TASK-113: `should_trigger_market_research`
  (`app/market_research/service.py`), `get_internal_historical_best`
  (`app/alerts/internal_history.py`), `is_material_improvement`
  (`app/alerts/material_improvement.py`) — nenhuma lógica de
  "oportunidade" nova foi criada; usar isso como base era literalmente
  usar o próprio código já aprovado.
- **Decisão — F2:** encerrada por reaproveitamento. Nenhuma mudança de
  código; documentado o mapeamento completo em `project-context.md`.
- **Decisão — F3:** `run_market_research` passa a consultar
  `get_external_price_reference_evidence` (dado coletado pela F1) ANTES
  de decidir se faz busca de histórico ao vivo — quando há referência
  utilizável, a busca de histórico é DISPENSADA (só a de mercado atual
  roda); quando não há, comportamento idêntico ao anterior à F3.
- **Achado corrigido nesta sessão:** a primeira implementação criou um
  conceito de "expiração" de 90 dias para a referência externa (config
  `historical_reference_max_age_days`) — interpretação ERRADA do pedido
  do usuário. Revalidar (F1, `DEC-113`) ≠ excluir/expirar um preço
  histórico já coletado (é um fato que não deixa de ser verdadeiro com o
  tempo). Removido por completo — `ExternalPriceReference` é reaproveitada
  pela F3 independente da idade (`collected_at`); só o valor de
  `historical_bootstrap_revalidation_days` (F1) decide quando COLETAR
  evidência adicional, nunca quando descartar a existente.
- **Validação:** `tests/integration/test_market_research.py`
  (`test_external_reference_skips_history_search_and_is_reused`,
  `test_external_reference_is_reused_regardless_of_age`).
- **Estado:** implementado e testado; ativação em produção agora sob a
  flag `market_research_external_reference_enabled` (`DEC-116`, default
  `False`) — F2 continua sempre ativo, sem flag.

## DEC-113 — FASE F1: bootstrap histórico externo one-shot por produto, com retry/backoff/lease reaproveitados de `MarketPriceAssessment`

- **Data:** 2026-09-05/06.
- **Classificação:** Nova capacidade, com uma correção estrutural
  (crash de dependência eager) e uma correção de resiliência (retry sem
  backoff) na mesma sessão.
- **Contexto:** próximo passo do plano F1–G ("F1 — bootstrap + histórico
  de preço, sem IA") depois do encerramento da FASE E (César Core
  obrigatório para AI/Search/grounding). Objetivo: coletar, uma vez por
  produto/condição/moeda, referências de preço histórico externas
  (`ExternalPriceReference`) via Search/Firecrawl+IA, sem repetir o
  trabalho a cada ciclo de coleta.
- **Decisão:** `HistoricalBootstrap`/`ExternalPriceReference`
  (`app/historical_bootstrap/`), `run_historical_bootstrap` chamado pela
  Fase B da coleta (`app/collection/orchestration.py`) quando a oferta é
  `MATCH`. `historical_bootstrap_revalidation_days` (default 90, mesmo
  padrão de config já usado por `MarketPriceAssessment`) controla quando
  o bootstrap pode rodar de novo para buscar evidência ADICIONAL —
  nunca para descartar a já coletada (ver `DEC-114` para o erro
  relacionado, encontrado e corrigido na F3).
- **Achado corrigido — crash estrutural:** a chamada `search=
  build_web_search_manager(settings)` era avaliada ANTECIPADAMENTE
  (eager), derrubando o fan-out inteiro da coleta quando as credenciais
  de Search estavam ausentes — mesmo para ofertas que nunca chegariam a
  precisar de busca. Corrigido tornando `search` uma FÁBRICA (`search=
  lambda: build_web_search_manager(settings)`), invocada só dentro do
  try/except que `run_historical_bootstrap` já tinha — nunca uma
  dependência antecipada de toda a coleta, só da funcionalidade que
  realmente a usa. Rejeitado explicitamente pelo usuário um band-aid de
  try/except no ponto de chamada ("esconderia a ausência de credencial
  sem corrigir a causa arquitetural").
- **Achado corrigido — resiliência de falha:** falha sem referências
  existentes apagava a linha (retry a cada ciclo, sem backoff); falha
  COM referências existentes marcava `completed_at=now` (fingia
  sucesso, atrasando a próxima tentativa real por 90 dias). Corrigido
  reaproveitando EXATAMENTE o padrão já aprovado de `MarketPriceAssessment`
  (TASK-113): `status=FAILED`, `retry_after`+`failure_count` (backoff
  exponencial, 15min→360min, mesmos valores de config já existentes,
  nenhum novo criado), `lease_until` (recuperação de crash). Também
  corrigido: exceção na persistência final não deixava mais o registro
  preso em `PROCESSING` para sempre (try/except adicional, mesma
  disciplina).
- **Validação:** `tests/integration/test_historical_bootstrap.py` (15
  casos, incluindo os 7 novos de retry/backoff/lease/recuperação de
  crash), `tests/integration/test_shared_collection.py::
  test_shared_fan_out_reuses_single_assessment_across_ten_missions`
  (prova de que o crash estrutural foi mesmo corrigido).
- **Estado:** implementado e testado; ativação em produção agora sob a
  flag `historical_bootstrap_enabled` (`DEC-116`, default `False`).

## DEC-112 — FASE E.3: hardening contra vazamento de dados na URL de Fetch/Enrichment

- **Data:** 2026-09-05.
- **Classificação:** Achado de auditoria de segurança read-only (categoria
  "vazamento de dados", distinta de SSRF/`DEC-111`), corrigido nesta rodada
  em GG Oferta e César Core (repositório separado `C:\cesar-core`).
- **Contexto:** uma auditoria anterior a esta fase confirmou empiricamente
  (teste sintético ao vivo) que o OmniRoute loga a URL completa
  (query + fragment) de todo `POST /v1/web/fetch` em nível INFO, sempre.
  Como a URL de enriquecimento vem de resultado de busca (input não
  confiável) e é usada em três lugares — decisão de buscar, persistência
  em `MarketPriceAssessment.evidence`, e prompt de `_interpret_evidence`
  — um parâmetro como `?session_id=...`/`?access_token=...` ou uma URL
  assinada de nuvem (AWS SigV4/GCS/Azure SAS) poderia vazar para logs,
  banco e IA.
- **Decisão:** política centralizada em dois módulos independentes (um
  por repositório, sem pacote compartilhado): `app/search/url_safety.py`
  (GG Oferta) e `src/cesar_core/fetch/contracts.py` (César Core,
  `reject_sensitive_query_target`/`strip_url_fragment`). Uma URL com
  parâmetro de alta confiança **nunca** é buscada (rejeição total, nunca
  mascaramento — mascarar produziria URL inválida e esconderia o
  problema); toda URL persistida/promptada tem fragment e parâmetros de
  alta confiança removidos (nunca mascarados), preservando parâmetros
  legítimos de e-commerce (`id`, `sku`, `ref`, `page`, `category`, `code`,
  `key` — deliberadamente nunca tratados como sensíveis). `FetchRequestPayload`
  (César Core) ganhou `extra="forbid"`. OmniRoute recebeu
  `APP_LOG_LEVEL: warn` (mecanismo oficial, sem fork) para reduzir o log
  de acesso que expunha a URL completa.
- **Documento canônico:** `C:\cesar-core\docs\security\
  fetch-data-leakage-hardening.md` — detalha as duas funções por
  repositório, os testes novos (35 casos GG + 30 casos Core + testes de
  rota), o efeito no contrato HTTP (`/v1/fetch` passa a rejeitar URL
  sensível com 422, mesmo padrão do erro de URL malformada pré-existente)
  e as pendências abaixo.
- **Fechamento:** auditoria integral `SELECT` em DEV (0 assessments) e PROD
  (5/5 assessments, 50 URLs recursivas em `evidence`, mais
  `historical_low_source`/`last_error`) encontrou zero fragmento, parâmetro
  sensível, URL assinada, família sensível, erro suspeito ou possível segredo
  real. `_interpret_evidence` deixou de enviar markdown ilimitado: preserva
  todas as fontes/URLs, limita título a 500 e descrição a 2.000 caracteres e,
  quando reduz, mantém começo/fim com marcador explícito. Identidade continua
  validada sobre o conteúdo original; só o prompt é minimizado.
- **Security Guidance:** SSRF inicial, vazamento URL→logs/evidence/prompt,
  markdown ilimitado e campos extras foram findings válidos corrigidos. O
  risco de redirect/DNS rebinding no provider terceiro permanece válido,
  não bloqueante e documentado para Firecrawl Cloud; classificar `id`/`sku`/
  `ref`/`code`/`key` isolados como segredo é falso positivo.
- **Validação:** PostgreSQL 18.4 descartável, migration `20260901_0002`,
  `tests/integration/test_market_research.py` 13/13. **FASE E.3 concluída**, sem
  commit/push e sem alteração de PROD.

## DEC-111 — SSRF no Fetch/Enrichment: encerrado para a topologia SaaS atual, com risco residual de terceiro documentado

- **Data:** 2026-09-05.
- **Classificação:** Achado de revisão automática de segurança (SSRF, HIGH)
  na capability de Fetch/Enrichment (`POST /v1/fetch` do César Core,
  `src/cesar_core/fetch/contracts.py`), investigado e encerrado nesta rodada
  — sem alteração de código nesta última verificação (a correção de código
  já tinha sido aplicada numa rodada anterior; esta rodada só confirmou/
  fechou a questão de SSRF-via-redirect que ficou em aberto).
- **Contexto:** a capability aceita uma `url` fornecida pelo GG Oferta
  (originada de resultados de Search) e a envia ao OmniRoute, que a repassa
  a um provider de scrape real (Firecrawl Cloud, hoje o único configurado).
  Duas perguntas de segurança foram levantadas em sequência: (1) a URL
  inicial pode apontar direto pra um alvo interno? (2) uma URL inicial
  pública pode redirecionar (HTTP 30x) pra um alvo interno depois da
  validação?
- **Pergunta 1 (URL inicial) — corrigida com código, rodada anterior:**
  `reject_ssrf_target()` (`src/cesar_core/fetch/contracts.py`), chamada
  explicitamente na rota `POST /v1/fetch` antes de qualquer chamada
  upstream. Resolve o host via `socket.getaddrinfo` e bloqueia loopback,
  RFC1918, link-local (inclui `169.254.169.254`, metadata de nuvem),
  reservado, multicast e não-especificado (IPv4 e IPv6); rejeita
  credenciais embutidas na URL (`user:pass@`) e limita a porta a 80/443.
  Testes: `tests/test_fetch_ssrf_guard.py` (14 casos, só literais de IP,
  sem DNS/rede real) e um teste de rota real em `tests/test_api_routes.py`
  provando 400 antes de o `FetchManager` ser acionado.
- **Pergunta 2 (redirect pós-validação) — investigada nesta rodada, sem
  alteração de código:** auditoria do código-fonte real do OmniRoute
  (`open-sse/handlers/webFetch.ts` e os 5 executors de provider —
  `firecrawl-fetch.ts`, `jina-reader-fetch.ts`, `tavily-fetch.ts`,
  `tinyfish-fetch.ts`, `context7-fetch.ts`) confirmou que **nenhum dos 5
  faz `fetch()` usando a URL do chamador como destino da requisição** —
  cada um sempre chama um domínio fixo e conhecido da própria API do
  provider (ex.: `api.firecrawl.dev`, `r.jina.ai`), passando a URL alvo
  como dado (corpo JSON ou segmento de path codificado), nunca como host
  da conexão. Quem de fato resolve/conecta no host da URL alvo (e portanto
  quem segue um eventual redirect dela) é o backend do provider terceiro,
  fora do código do César Core e do OmniRoute.
- **Evidência oficial do provider (verificada pelo usuário):** advisory
  Firecrawl `GHSA-vjp8-2wgg-p734` — a vulnerabilidade original era
  exatamente SSRF via alvo malicioso que redirecionava pra um endereço
  local; o Firecrawl declara essa correção aplicada ao **Cloud service**
  em 27/12/2024. Uma ressalva posterior (13/03/2026) permanece
  principalmente para instalações **Playwright OSS/self-hosted**, onde o
  próprio fornecedor recomenda proxy seguro adicional. A topologia atual
  usa exclusivamente **Firecrawl Cloud** (`api.firecrawl.dev`), não
  self-hosted.
- **Decisão: RESOLVIDO PARA A TOPOLOGIA ATUAL, COM RISCO RESIDUAL DE
  TERCEIRO DOCUMENTADO.** Divisão de responsabilidade registrada
  explicitamente:
  1. **César Core** — valida a URL inicial (loopback/privado/link-local/
     reservado/metadata bloqueados, credenciais embutidas rejeitadas,
     porta limitada a 80/443); defesa em profundidade, não depende de
     nenhuma camada abaixo pra isso.
  2. **OmniRoute** — não acessa diretamente o host alvo; envia a URL como
     dado pra uma API fixa e conhecida do provider.
  3. **Firecrawl Cloud** — executa de fato o acesso ao alvo (incluindo
     qualquer redirect); o fornecedor declara a correção do SSRF por
     redirect nesse serviço (`GHSA-vjp8-2wgg-p734`, Cloud, 27/12/2024).
- **Limites explícitos, não resolvidos e não resolvíveis por este
  repositório:** o guard do Core (`reject_ssrf_target`) não é garantia
  absoluta contra DNS rebinding (a resolução de DNS que ele faz pode
  divergir da resolução feita, momentos depois, pelo Firecrawl); a
  segurança final de quem executa o fetch (o provider terceiro) continua
  sendo uma trust boundary externa a este projeto, não uma garantia deste
  código.
- **Gatilho de reabertura:** se no futuro houver migração de Firecrawl
  Cloud pra Firecrawl self-hosted (ou troca do provider padrão configurado
  em `CESAR_CORE_FETCH_DEFAULT_PROVIDER`), esta análise deve ser reaberta e
  proxy/egress filtering deve ser avaliado, conforme a própria recomendação
  do Firecrawl para instalações self-hosted/OSS.
- **Não implementado nesta rodada, por decisão explícita:** proxy próprio,
  qualquer chamada HTTP direta do Core ao alvo, ou qualquer mudança de
  arquitetura. Nenhum código alterado; nenhuma suíte de testes repetida.

## DEC-110 — FASE E.1: enrichment migrado para trás do César Core, fechando a pendência do DEC-109

- **Data:** 2026-09-05.
- **Classificação:** Implementar agora, continuação direta do `DEC-109` (mesma
  sessão de retomada, 2ª rodada). Implementação real nos dois repositórios,
  validada por testes automatizados; sem commit/push/PROD nesta rodada.
- **O que foi implementado:** César Core ganhou o Central Web Fetch/
  Enrichment Gateway (`src/cesar_core/fetch/`: contratos, policy, manager,
  adapter OmniRoute, rota `POST /v1/fetch`, mesmo padrão de Identity/
  Capability/Quota/Usage/Tracing já usado por AI/Search), traduzindo para o
  contrato real `POST /v1/web/fetch` do OmniRoute (Firecrawl é um dos
  providers reais dele, confirmado contra o código-fonte oficial do
  OmniRoute nesta mesma investigação). Nova migration SQLite
  (`0002_add_fetch_capability.sql`) amplia os CHECKs de capability para
  incluir `fetch`. GG Oferta substituiu `FirecrawlScrapeProvider` por
  `CesarCoreFetchProvider` (`backend/app/search/cesar_core_fetch.py`) em
  `worker.py`/`market_research/service.py`, reusando a credencial de
  aplicação já existente (nenhum secret novo no GG). Confirmado que não
  havia mais consumidor do cliente Firecrawl direto antes de remover
  `backend/app/search/firecrawl.py`, `tests/test_firecrawl_scrape.py`,
  `firecrawl_api_key(_file)` do `Settings`, `compose.yaml` e
  `backend/.env.example`, e o secret correspondente de
  `scripts/manage_collection_worker_config.ps1`. `scripts/check.ps1` também
  parou de provisionar os secrets órfãos de Gemini/Groq identificados no
  `DEC-109`.
- **Resultado:** GG Oferta e worker nativo não dependem mais diretamente de
  nenhum provider externo (Gemini, Groq, OpenRouter, Firecrawl) para AI,
  grounding, Search ou enrichment — a dependência é sempre o César Core.
  Testes: Core, 77 novos/ajustados passando a partir de um ambiente limpo
  (o `.env` real de DEV do `cesar-core` colide com um bug pré-existente de
  `pydantic-settings`, não corrigido aqui — ver runbook); GG, suíte completa
  não-integração com 1766 passed, mesmas 6 falhas pré-existentes do
  `DEC-109` sem relação com esta fase. Ruff limpo nos arquivos tocados nos
  dois repositórios (débito pré-existente em arquivos não tocados
  preservado, não corrigido por não fazer parte do escopo).
- **Não implementado nesta rodada:** E2E real contra Firecrawl de verdade.
  O stack Docker do César Core (`cesar-core-cesar-core-1` e vizinhos) já
  estava em execução com uma imagem publicada anterior a esta mudança;
  rebuildar/reiniciar esse serviço persistente para carregar o código novo
  fica pendente de autorização explícita do usuário, por afetar um ambiente
  já em uso (não um stack descartável como o de TASK-118H).
- **Próxima ação:** ~~decisão do usuário sobre autorizar o E2E real~~ —
  **superada**: usuário autorizou explicitamente; ver adendo abaixo.

### Adendo — E2E real executado e aprovado (mesma data, 3ª rodada)

Usuário autorizou rebuild/restart do stack DEV do César Core para o E2E real.
Executado: `docker build` de `cesar-core:local` a partir do código atual;
`docker compose up -d --no-deps cesar-core` (só esse serviço recriado).
Redis/SearXNG nunca tocados; OmniRoute recriado só para carregar a variável
`INITIAL_PASSWORD` (arquivo local `.secrets/omniroute-admin.env`, nunca em
`.env`/Git) e a publicação de porta `127.0.0.1:20128` (achado: essa
publicação já existia no container rodando antes desta sessão, mas nunca
tinha sido declarada em `compose.yaml` — corrigido, sem alterar nada além
disso). Nenhum volume resetado; nenhum dado apagado.

**Onboarding administrativo do OmniRoute DEV concluído** (nunca tinha sido
feito antes — instância ficava na tela inicial de setup): senha forte gerada
localmente com `secrets.choice`, persistida só em
`C:\cesar-core\.secrets\omniroute-admin-password` (fora do Git, nunca exibida
no chat). Login confirmado via `POST /api/auth/login`.

**Credencial OmniRoute dedicada para enrichment**: criada via
`POST /api/keys` (`ggoferta-fetch`) e restrita via
`PATCH /api/keys/{id}` a `allowedEndpoints: ["web-fetch"]` — confirmado por
teste direto que essa chave é aceita em `/v1/web/fetch` e **rejeitada** em
`/v1/search` (prova de escopo mínimo real, não só declarado). `ggoferta-ai`/
`ggoferta-search` não foram lidas, alteradas nem tiveram seu valor
reaproveitado — `.secrets/ggoferta-fetch` (que antes era uma cópia
provisória da chave de AI, prática explicitamente vetada pelo usuário) foi
substituído pelo valor da chave dedicada nova.

**Achado real de arquitetura do OmniRoute**: uma API key só é restrita a
categorias de endpoint (`allowedEndpoints`) quando essa lista é
explicitamente preenchida — vazia/ausente permite qualquer categoria
(default retrocompatível). As chaves pré-existentes do projeto já eram
restritas cada uma à sua própria categoria; por isso reaproveitar valor
entre capabilities nunca teria funcionado, mesmo que o arquivo/nome fosse
diferente — confirma que credenciais por capability aqui não são só
higiene de "arquivo separado", são uma autorização real e distinta no
OmniRoute.

**Firecrawl como provider do OmniRoute**: a instância OmniRoute DEV nunca
tinha uma conexão de provider Firecrawl configurada
(`/v1/web/fetch` retornava 400 "No credentials configured for web-fetch
provider: firecrawl" mesmo com a API key correta). Registrada via
`POST /api/providers` reaproveitando a chave real de Firecrawl que já
existia em `C:\AIShoppingAgent\AIShoppingAgent\.secrets\firecrawl_api_key`
(provisionada para o GG antes desta fase, hoje sem nenhum consumidor de
código) — passa a viver exclusivamente no OmniRoute, o único lugar da
arquitetura onde uma credencial de provider concreto deve existir.

**Prova real obtida**: `POST /v1/web/fetch` direto no OmniRoute com a chave
dedicada retornou conteúdo real (Wikipedia, artigo da RTX 50 series) via
Firecrawl. `CesarCoreFetchProvider.scrape_basic()` (GG, código de produção,
não mock) contra o César Core recém-recriado devolveu o mesmo conteúdo
normalizado (~20 KB, respeitando `max_content_length`). Teste focado real de
`app.market_research.service._search_with_enrichment` (Search real via
SearXNG + enrichment real, `min_items` forçado alto para exercitar o ramo de
enrichment) devolveu evidência real de domínios reais (`kabum.com.br`,
`adrenaline.com.br`), respeitando o teto de 3 URLs — nenhuma mudança de
código nessa função nesta rodada. `/ready` e `/v1/capabilities` (`fetch:
available`) confirmados depois de cada restart.

**Resultado**: FASE E.1 concluída integralmente, incluindo E2E real. Próxima
fase: FASE F.

## DEC-109 — FASE E (recuperação GG↔Core): manter Firecrawl `/v2/scrape` direto foi interpretação incorreta; enrichment fica pendente

- **Data:** 2026-09-05.
- **Classificação:** Correção de rumo (documentação apenas nesta rodada;
  nenhum código de scrape/enrichment alterado). Retomada de sessão do Codex
  (limite atingido) pelo Claude, no meio da FASE E da "recuperação" GG↔César
  Core (fases 0/A–F, distintas de TASK-118A–H, já concluídas e publicadas).
- **Contexto:** a FASE E remove os legados diretos de AI/Search do GG Oferta.
  A auditoria desta retomada confirmou AI e Search corretamente concluídos:
  `AIProviderManager`/`WebSearchManager` só constroem `CesarCore*Provider`;
  Gemini/Groq/OpenRouter diretos, suas factories, flags `cesar_core_*_enabled`
  e o disaster fallback antigo foram removidos do `Settings`; `WebSearchManager`
  não tem mais fallback Firecrawl. Suíte completa (exceto integração/E2E)
  reexecutada: 1761 passaram; as 6 falhas restantes (autenticação e contrato
  de schema de `products`/`users`) são pré-existentes, sem relação com esta
  fase. Ruff/format aplicados só nos arquivos tocados pela FASE E.
- **O que estava errado:** a mesma rodada da FASE E documentou (em
  `docs/internal/project-context.md`, `docs/architecture/
  cesar-core-integration.md` deste repositório e `docs/architecture/
  gg-oferta-core.md` do `cesar-core`) que manter `FirecrawlScrapeProvider`
  chamando `https://api.firecrawl.dev/v2/scrape` diretamente do GG Oferta
  (Market Research e worker nativo, credencial Firecrawl própria) era o
  estado final aceitável da FASE E. Isso contraria o mesmo invariante já
  aplicado a AI/Search: GG Oferta e worker nativo não devem depender
  diretamente de provider externo — a dependência deve ser sempre o César
  Core, com o provider concreto abaixo dele.
- **Correção aplicada nesta rodada:** documentação dos dois repositórios
  corrigida para não apresentar mais o Firecrawl scrape direto como decisão
  final — está marcado como pendência real da FASE E. Nenhum código de
  scrape/Market Research foi alterado ou revertido (o comportamento
  funcional é idêntico ao de antes desta sessão). `CLAUDE.md`/`AGENTS.md`
  dos dois repositórios ganharam a frase explícita do invariante ("GG Oferta
  e seu worker nativo nunca devem depender diretamente de um provider
  externo... a dependência é sempre o César Core").
- **Achado relevante para a decisão pendente:** `DEC-107` (pré-flight real do
  OmniRoute v3.8.51) já registra que o OmniRoute expõe `POST /v1/web/fetch`
  e que esse endpoint **já reconhece Firecrawl como um dos seus providers**.
  Ou seja, existe um caminho plausível para fechar essa pendência sem
  inventar integração nova (`César Core → OmniRoute /v1/web/fetch →
  Firecrawl`), mas ele exige uma extensão real do César Core (contrato
  neutro de enrichment, adapter OmniRoute, migração do consumidor GG, testes
  e documentação nos dois repositórios) que ainda não foi implementada nem
  autorizada.
- **Próxima ação:** decisão explícita do usuário sobre implementar essa
  extensão agora (fechando de vez a FASE E) ou tratá-la como item separado,
  antes de declarar a FASE E integralmente concluída. Sem commit/push/PROD
  nesta rodada, por instrução explícita.

## TASK-118H — início do preflight DEV

Classificação: **Implementar agora**, etapa da TASK-118 explicitamente solicitada
após publicação da 118G. Atualizar status e validar resiliência/rollback em DEV
isolado; reutilizar arquitetura e contracts, sem migrar grounding, ativar Claudião
ou alterar PROD. A validação herdada não equivale à aprovação da nova rodada.
Deployment PROD depende de autorização explícita, topologia e credenciais próprias.

Continuação autorizada: restart do processo Core e rollback configuracional real
sem reiniciar GG para recuperar dependência. Reload de Settings/factories para
rollback equivale ao restart direcionado do consumidor documentado no runbook.
Não persistir quota, alterar transporte de produção ou criar integração Claudião
nesta validação. HTTP injetado é identificado separadamente de API externa real.

## TASK-118G — Search versus enriquecimento

Classificação: **Implementar agora**, autorizado pelo usuário. SearXNG
certificado para market_research, limite obrigatório de saída, sem promessa
de aquisição externa limitada. Firecrawl Search somente em indisponibilidade;
scrape separado preserva o teto de 3 URLs. ADR-017 e TASK-118G detalham a decisão.

## DEC-108 — Auditoria GG Oferta (subtask 3): condição Novo/Usado consolidada nas 6 lojas, supersede `DEC-076` para Pichau/Terabyte/KaBuM!

- **Data:** 2026-08-30.
- **Classificação:** Correção de regra de negócio (código + testes), sem
  migration. Escopo: só condição Novo/Usado; não altera matching, imagem
  ou qualquer outra parte do pipeline de coleta.
- **Decisão anterior (`DEC-076`, 2026-08-22), citada aqui sem reescrever
  história:** *"Na Amazon, a validação real confirmou a regra da
  plataforma: ausência desses marcadores na oferta principal significa
  `new`; **demais providers continuam `unknown` sem evidência**."* Ou
  seja, `DEC-076` só previu o fallback "sem evidência = novo" para a
  Amazon; todas as demais lojas (à época: Pichau, Terabyte, KaBuM!)
  deveriam permanecer `unknown` sem evidência explícita. `TASK-104B`
  (Mercado Livre) já havia estendido esse mesmo fallback para o Mercado
  Livre, também documentado e não alterado aqui.
- **Decisão nova, aprovada explicitamente pelo usuário nesta auditoria:**
  Pichau, Terabyte e KaBuM! não vendem usado no escopo atual do GG
  Oferta — a condição dessas três lojas passa a ser **sempre `NEW`**,
  nunca `unknown`. Isto **substitui `DEC-076` especificamente para essas
  três lojas** (Amazon e Mercado Livre continuam exatamente como
  `DEC-076`/`TASK-104B` já definiam — nenhuma mudança nelas).
- **Motivo:** decisão de produto — o próprio catálogo dessas lojas, no
  escopo comercial atual da V1, não inclui oferta usada; `unknown` nessas
  três só gerava ruído (rótulo "condição não identificada" sem nenhum
  caso real de ambiguidade por trás).
- **Comportamento atual (código):** `_FIXED_NEW_CONDITION = "Novo"` em
  `backend/app/collection/providers/stores.py`, atribuído em `extract()`
  de `PichauProvider`/`TerabyteProvider`/`KabumProvider` — nenhuma
  tentativa de detecção, ao contrário de Amazon/Mercado Livre/Magalu (que
  continuam procurando evidência real de usado/seminovo/recondicionado
  antes de assumir novo). Teste de regressão dedicado:
  `tests/test_store_providers.py::test_pichau_terabyte_kabum_are_always_new_condition`.
- **Magalu — não é regressão, é a spec (`TASK-104A`) que ficou desatualizada:**
  o fallback "sem evidência de usado = novo" da Magalu já estava correto
  no código antes desta auditoria (`_magalu_card_condition`, mesmo
  comportamento de Amazon/Mercado Livre); é o texto de `TASK-104A`
  ("condição... só é preenchida com evidência real") que contradizia essa
  regra e foi corrigido nesta data — ver nota no próprio arquivo.
- **Magalu — evidência real de badge, registrada de forma durável (não
  depende de nenhum arquivo local continuar existindo):**
  - **Origem:** captura real ao vivo da própria sessão de validação da
    TASK-104A, em 2026-08-22 (`offer.badges` do JSON SSR é capturado
    desde então, mas nunca havia sido inspecionado para condição). A
    captura original ficou em `tmp/magalu-edge-next-data.json`, uma pasta
    **não versionada** — este parágrafo, e não o arquivo, é o registro
    permanente do achado.
  - **Valor observado:** `text: "produtousado"` (badge concatenado, sem
    espaço) dentro de `badges: [{"imageUrl": "...", "text":
    "produtousado"}]`.
  - **Amostra:** 39 ofertas capturadas; **32 delas** traziam esse badge.
  - **Correlação:** as **32** ofertas com o badge também tinham "Usado:"
    no início do título. **Nenhum caso de badge USED isolado** (badge
    presente sem "Usado:" no título, ou vice-versa) foi observado nesta
    amostra.
  - **Badge promocional, não de condição:** o único outro valor visto,
    `"fazum21"`, é claramente uma campanha (mesmo padrão de selo já
    documentado para cupons) — nunca tratado como sinal de condição.
  - **Preservação da evidência:** o objeto do badge real foi copiado byte
    a byte (confirmado por comparação direta) para dentro de um teste
    versionado — `tests/test_store_providers.py::
    test_magalu_used_badge_from_real_captured_evidence_marks_condition_used`
    (badge real, título sem "Usado:", prova que o badge é fonte
    independente) e `::test_magalu_promotional_badge_is_not_mistaken_for_condition`
    (badge `"fazum21"`, confirma que não é lido como condição). Esses
    testes são a evidência permanente; `tmp/magalu-edge-next-data.json`
    pode ser apagado sem perda de rastreabilidade.
  - **Implementação:** `_magalu_badge_condition()` em `stores.py`, checada
    antes do título — defesa em profundidade para um título futuro sem o
    prefixo "Usado:" (cenário não observado nesta amostra, mas plausível).
- **Se uma auditoria futura encontrar Pichau/Terabyte/KaBuM! como
  `unknown` de novo:** isso é regressão desta decisão (`DEC-108`), não um
  comportamento a "restaurar" — a intenção correta e vigente é sempre
  `NEW` para essas três lojas.

## DEC-107 — TASK-118: pré-flight do OmniRoute com fonte oficial fixada e contratos reais extraídos do código

- **Data:** 2026-08-30.
- **Classificação:** Pré-flight (nenhum código/config/secret deste
  repositório alterado) — primeira investigação real da TASK-118.
- **Regra de fonte, fixada explicitamente pelo usuário**: só o
  repositório oficial do OmniRoute (`https://github.com/diegosouzapw/
  OmniRoute`, site `https://omniroute.online`, wiki oficial) conta como
  referência arquitetural. Forks/mirrors/repositórios de terceiros com o
  mesmo nome (achado real: `ChrisCompton/omniroute` apareceu numa busca
  inicial) são tratados como não-oficiais até prova em contrário. Em
  conflito: código do repo oficial > docs do repo oficial > wiki oficial
  > site oficial.
- **Commit de referência fixado**: `1f4dc830f3290a5507b5350417ae1547f825aefc`
  (branch `release/v3.8.51`, pushed `2026-08-30T13:39:01Z`). Clonado
  localmente (fora deste repositório e do repositório do Coupon
  Collector, pasta de investigação isolada) especificamente pra ler o
  `docs/openapi.yaml` oficial (spec OpenAPI real, ~9.670 linhas) e o
  código-fonte direto, nunca resumo de terceiro.
- **Achado central**: o OmniRoute é **self-hosted** (não é uma API SaaS
  remota) — precisa rodar como infraestrutura própria. Publica imagem
  Docker oficial (`diegosouzapw/omniroute:X.Y.Z`, porta `20128`).
  **Correção de arquitetura (mesma data, por instrução explícita do
  usuário — a primeira leitura, que dizia "se encaixa direto no
  `compose.yaml` do GG Oferta", estava errada)**: o OmniRoute é
  infraestrutura **central compartilhável**, nunca acoplada
  arquiteturalmente ao repositório/compose do GG Oferta. A cadeia
  aprovada é `GG Oferta → AIProviderManager/WebSearchManager → César
  Core → OmniRoute` — `cesar-core` é um **repositório novo e separado**
  (`https://github.com/jhonnatancesar/cesar-core.git`, confirmado
  existente e vazio em 2026-08-30; mesmo padrão já aplicado ao Coupon
  Collector, `DEC-105`), dono da integração com o OmniRoute; o GG Oferta
  só terá adapters/client do
  César Core, nunca fala com o OmniRoute diretamente. Implantação pode
  coexistir no mesmo host físico desde o início, mas serviços/repos/
  compose ficam claramente separados.
- **Contratos reais confirmados e citados por caminho exato** (detalhe
  completo em `docs/tasks/TASK-118.md`, "Pré-flight §2"): autenticação
  por chave de inferência `sk-…` (nunca as outras 3 famílias de
  credencial, que são pra gestão/dashboard, e essa chave ficaria com o
  César Core, não com o GG Oferta); `POST /api/v1/chat/completions`
  compatível com OpenAI, com headers de observabilidade prontos (custo,
  tokens, provider resolvido, decisão de roteamento, fallback
  attempts); `POST /api/v1/search` e scraping avançado separado em
  `/v1/web/fetch` (que já reconhece Firecrawl como um dos seus
  providers); `GET /api/health` liveness simples; 3 camadas de
  resiliência já prontas no servidor (circuit breaker por provider,
  cooldown por chave, model lockout) documentadas com caminho de código
  real (`src/shared/utils/circuitBreaker.ts`, `src/sse/services/auth.ts`);
  formato de erro uniforme (`{error: {message, type}, requestId}`).
  **Fallback real de busca, confirmado no código + testes (correção
  mesma data — a primeira leitura, "AnySearch é o fallback gratuito
  nativo", estava incorreta)**: o fallback zero-configuração de verdade
  é `duckduckgo-free` (`authType: "none"`, promovido automaticamente com
  `credentials = {}` quando nenhum provider credenciado está
  disponível — `src/app/api/v1/search/route.ts`, confirmado por
  `tests/unit/search-handler-duckduckgo.test.ts`). A AnySearch é
  gratuita (`costPerQuery: 0`) mas **ainda exige uma API key
  configurada** (`authType: "apikey"`) — não é um fallback sem
  configuração nenhuma.
- **Atualização mesma data — pré-flight aprovado conceitualmente,
  decisões arquiteturais fechadas pelo usuário** (detalhe completo em
  `docs/tasks/TASK-118.md`, "Pré-flight §4/§5"):
  1. **Protocolo GG Oferta ↔ César Core**: HTTP interno com API
     versionada (`POST /v1/ai/generate`, `POST /v1/search`, `GET
     /health`, `GET /ready`, `GET /v1/capabilities`) -- nunca biblioteca
     Python compartilhada (César Core precisa servir futuramente outras
     stacks). Fila não é o transporte padrão pra IA/Search síncrono.
  2. **Escopo do César Core**: multi-aplicação desde o nascimento
     (`gg_oferta = ACTIVE`, `claudiao = RESERVED/NOT_CONFIGURED`) -- GG
     Oferta é só o primeiro consumidor, pergunta fechada em definitivo.
  3. **Contrato de Web Search**: genérico e neutro -- César Core nunca
     conhece conceitos de domínio do GG Oferta (`MarketPriceAssessment`,
     `Offer`, `Product`, `Mission`); tradução fica no `WebSearchManager`
     do GG Oferta.
  4. **Credenciais de provider**: Gemini/Groq/OpenRouter/etc. ficam no
     OmniRoute; GG Oferta não armazena permanentemente essas chaves após
     a migração. Durante rollout, a cascata direta antiga pode continuar
     temporariamente como rollback/disaster fallback -- documentado
     explicitamente como estado de transição, nunca arquitetura final.
  5. **Secrets do César Core**: próprios, nunca compartilham diretório
     com os do GG Oferta; V1 = arquivos locais, `*_FILE`, ACL própria,
     nunca Git/log.
  - **Arquitetura de fallback decidida**: durante a migração, `GG Oferta
     → AIProviderManager → CesarCoreAIProvider → César Core → OmniRoute
     → providers`, com rollback temporário pra cascata antiga só se o
     César Core/OmniRoute estiver estruturalmente indisponível --
     **nunca fallback duplicado** (se o OmniRoute já executou seu
     próprio fallback interno, o GG Oferta não repete Gemini/Groq/
     OpenRouter por cima). Mesma filosofia pra Search
     (`CesarCoreSearchProvider` → `FirecrawlSearchProvider` direto como
     rollback transitório). Estado final desejado, sem a camada de
     rollback: `GG Oferta → AIProviderManager → César Core → OmniRoute
     → providers`.
  - Isso fecha as perguntas 2 (fallback), 4 (granularidade do Web
    Search), 5 (reconciliação com a cascata gratuita) e 6 (onde ficam as
    chaves) das "7 perguntas originais".
- **Atualização mesma data — as últimas 3 decisões fechadas, lista de
  bloqueadores zerada** (detalhe completo em `docs/tasks/TASK-118.md`,
  "Pré-flight §6 a §10"):
  6. **Qualidade/capacidade (Policy Layer)**: GG Oferta nunca escolhe
     provider/model, só envia requisitos neutros separados em
     `application`/`service`/`purpose`/`requirements`
     (`structured_output`/`reasoning`/`vision`/`tool_calling`) mais um
     `service_class` neutro (`economy`/`standard`/`quality`). A Policy
     Layer, dentro do César Core, resolve a combinação. Contrato
     precisa continuar válido pro futuro consumidor "Claudião", nunca
     hardcoded pro GG Oferta.
  7. **Política de custo**: César Core é a autoridade que sabe quanto
     cada `application` pode gastar (`FREE_ONLY`/`FREE_PREFERRED`/
     `PAID_ALLOWED`, `max_cost_per_request`/`daily_budget`/
     `monthly_budget`) -- só o contrato/modelo precisa estar preparado
     nesta rodada, não a gestão financeira completa. Divisão de
     responsabilidade explícita: César Core decide autorização/política/
     limites; OmniRoute continua dono de disponibilidade de provider,
     quota, cooldown, circuit breaker, fallback entre providers e
     roteamento em si -- César Core nunca reimplementa isso, só traduz
     a política permitida pro `combo`/rota do OmniRoute (conceito real,
     confirmado no pré-flight §2 via `/api/combos`).
  8. **Topologia PROD V1**: mesmo Windows Server físico, três
     deployments independentes (`C:\App\AIShoppingAgent`,
     `C:\App\cesar-core`, `C:\App\omniroute`), cada um com repo/config/
     secrets/lifecycle próprios, sem compose compartilhado. Rede Docker
     externa conceitual `cesar-platform` entre os três. César Core
     expõe endpoint acessível pelo host local (não só rede Docker
     interna), porque o `collection_worker` do GG Oferta roda nativo no
     Windows (TASK-109) e precisa alcançá-lo via `localhost`.
  9. **Exposição pública**: nenhuma (sem `core.ggoferta.com`/
     `omniroute.ggoferta.com`, sem Cloudflare pra APIs internas) --
     Internet só alcança `ggoferta.com` → GG Oferta; César Core e
     OmniRoute são infraestrutura interna.
  10. **Supervisão**: lifecycle próprio por container
      (`restart: unless-stopped` + healthcheck); `/ready` do César Core
      distingue Core vivo / OmniRoute alcançável / capacidade de IA /
      capacidade de Search -- falha de um provider upstream isolado
      nunca derruba o César Core inteiro (já isolado por provider no
      circuit breaker do próprio OmniRoute, §2).
  - **Lista de decisões arquiteturais bloqueadoras da TASK-118: vazia.**
    Nenhuma incompatibilidade real encontrada entre essas decisões e os
    contratos reais do OmniRoute confirmados no pré-flight. Restam só
    detalhes de implementação (esquema exato de payload do contrato
    César Core, tradução exata `service_class`→`combo`, mecanismo de
    deploy/supervisão do César Core no Windows) -- nenhum bloqueia
    começar a implementar.
- **Nada implementado em nenhuma das três rodadas**: só leitura/registro
  do pré-flight, por instrução explícita ("por enquanto você só vai
  atualizar documentação e fazer pré-flight"; depois, "não implementar
  código ainda" repetido em cada rodada).

## DEC-106 — TASK-106: Coupon Collector evolui pra auto-configuração real, validado nas 4 lojas

- **Data:** 2026-08-30.
- **Classificação:** Continuação do `DEC-105` (repositório separado) --
  registra a evolução real do Coupon Collector numa sessão intensa de
  investigação ao vivo + implementação, tudo fora deste repositório
  (`AIShoppingAgent-cupom`, já publicado em
  `https://github.com/jhonnatancesar/AIShoppingAgent-cupom`, branch
  `master`, HEAD `447de95`). Nenhum código deste repositório foi
  alterado.
- **Ponto de partida:** headless causava bloqueio 403 real em Magalu/
  Mercado Livre (achado real, corrigido pra `--start-minimized`, mesma
  escolha já validada pelo `EdgeCdpSupervisor` do `collection_worker`
  principal -- nunca headless). Depois desse fix, Amazon e Magalu ainda
  não achavam nenhum cupom real.
- **Achados reais que exigiam investigação, não suposição** (diagnóstico
  ao vivo do DOM real, sessão autenticada com conta de pesquisa dedicada
  -- nunca a pessoal do usuário):
  1. **Bug de arquitetura**: `browser.new_context()` por loja criava
     contexto isolado tipo anônimo, sem os cookies do perfil dedicado --
     login manual nunca "aparecia" pro scanner. Corrigido pra reaproveitar
     `browser.contexts[0]` (contexto real do perfil), uma página nova por
     loja, contexto nunca fechado.
  2. **Amazon mudou de UI** desde a auditoria original do `DEC-093`: hoje
     mostra só "Você paga R$X com o cupom" (preço final, clip coupon
     automático, sem valor de desconto isolado declarado) -- o regex
     antigo (`Cupom de R$X de desconto`) nunca batia com o fraseado real
     atual. Nunca infere o desconto comparando com outro preço ambíguo do
     card -- só a evidência literal do preço final.
  3. **Magalu nunca tinha fonte de busca** (só home/ofertas, sem campanha
     ativa no momento da investigação) -- as páginas de busca real
     (mesma UX que o `MagaluSearchTransport` do projeto principal já usa)
     têm cupom o tempo todo em várias categorias, e nunca eram visitadas.
     Faltava também o regex de percentual (só valor fixo existia).
  4. **Mercado Livre**: seletor genérico de produto nunca batia com os
     cards REAIS de cupom (`div.coupon-card` na página `/cupons`, ~73 por
     página; `.poly-card:has(.poly-coupons__wrapper)` no carrossel da
     home). 4 fraseados reais confirmados (fixo/percentual, cupom antes/
     depois do valor), incluindo campos extras já presentes na página
     ("Compra mínima"/"Limite de") agora extraídos como
     `minimum_purchase_amount`/`maximum_discount_amount` reais. Carrossel
     tem lazy-load real (só 2 de 23 cards apareciam sem rolar a página).
  5. **Marcador de bloqueio "captcha" com falso positivo**: qualquer
     rodapé padrão "Protegido por reCAPTCHA" (ex.: tela de login do ML)
     disparava bloqueio incorretamente. Marcadores agora exigem
     linguagem de desafio ativo; matching de marcador ganhou normalização
     de acento (NFKD) -- os marcadores em português nunca bateriam contra
     o texto real acentuado antes disso.
- **Decisão do usuário sobre ritmo**: fila sempre sequencial (já era),
  mais pausa configurável entre interações
  (`delay_between_requests_seconds`) e cooldown próprio na troca de loja
  (`cooldown_between_stores_seconds`) -- nunca framed como evasão
  anti-bot (que continua fora de escopo, `TASK-106.md`), e sim como ritmo
  humano/respeitoso; a cadência normal já é de 1h/30min, sem pressa
  nenhuma pra render qualquer coisa mais rápido.
- **Decisão do usuário sobre "aprender"**: não bastava alertar sobre uma
  possível fonte de cupom nova (ex.: badge "AQUI TEM 9.9", que só existe
  durante campanha sazonal) -- tinha que **auto-configurar de verdade**,
  sem precisar editar `config.json` manualmente (a janela de uma
  promoção passa rápido demais pra depender de intervenção humana).
  Implementado como descoberta + verificação determinística: link
  candidato (texto curto + palavra-chave, nunca link de produto
  individual) é visitado NA MESMA RODADA, roda a mesma extração de
  evidência real já usada em qualquer fonte, e só é adotado (passa a ser
  escaneado sozinho toda rodada futura) se achar cupom de verdade --
  nunca confia no texto do link isolado, nunca é "IA decidindo", é
  comparação determinística de evidência literal. Corrigido no caminho:
  o mesmo banner promocional gerava uma URL de rastreamento diferente a
  cada carregamento de página (`click1.mercadolivre.com.br/.../count?a=
  <token>` mudando toda vez), o que fazia o mesmo candidato "descobrir"
  de novo a cada rodada -- identidade do candidato passou a ser o texto
  do badge (estável), e a URL adotada é o destino final já resolvido
  (depois de seguir o redirecionamento de verdade), nunca o link frágil
  original.
- **Outras duas melhorias menores pedidas pelo usuário**: cupom `active`
  que não é confirmado numa rodada completa (sem bloqueio/erro) vira
  `expired` automaticamente (comparação real de histórico, não
  inferência); texto literal de esgotamento/validade ("Está esgotando!",
  "Vence amanhã", "Válido até X"), quando a página escrever isso, é
  capturado e anexado à evidência -- genérico pra qualquer loja, nunca
  calcula data relativa nem decide status estruturado sozinho.
- **Validado ao vivo, sessão autenticada real** (não só sintaxe/lógica):
  Amazon 0→18 evidências reais, Kabum 90 (já funcionava, mantido),
  Magalu 0→24, Mercado Livre 0 (bloqueado)→148+ (carrossel + área de
  cupons + descoberta automática). Login manual implementado via
  `login_manual.py` -- abre o perfil dedicado numa URL pública, usuário
  loga manualmente com conta de pesquisa dedicada (nunca a pessoal); o
  script nunca digita nenhuma credencial.
- **Login manual — nota de segurança**: o usuário confirmou
  explicitamente que usou uma conta de pesquisa separada (não a pessoal)
  justamente para isolar qualquer risco de bloqueio/banimento dessa
  investigação da conta real.
- **Publicado**: todos os commits (12 ao todo, do transporte Edge/CDP
  inicial até a auto-configuração e a documentação atualizada do README)
  já estão em `origin/master` do `AIShoppingAgent-cupom`.

## DEC-105 — TASK-106: Coupon Collector vive em repositório separado, nunca em `app/coupons/`

- **Data:** 2026-08-30.
- **Classificação:** Decisão arquitetural, retificando o `DEC-093`
  (2026-08-22), que propunha o Coupon Collector como módulo
  `app/coupons/` dentro deste repositório.
- **Contexto:** entre o `DEC-093` e agora, o Coupon Collector foi
  prototipado de forma independente, numa pasta própria fora deste
  repositório (`C:\AIShoppingAgenteCupom`), reimplementando scanner/
  evidência/persistência do zero (sem reaproveitar `BrowserSession`/Store
  Providers), originalmente com Firefox+Playwright visando uma Raspberry
  Pi 3B+. A lógica de evidência foi validada contra a auditoria real do
  `DEC-093`/`TASK-106.md` (reconhece literalmente as mesmas strings
  encontradas: `"Cupom de R$ 20,00 de desconto"`, `"SELO: CUPOM
  GAMER10"`, `"Cupom R$ 100 OFF"`, `"15% OFF com Cupom"`).
- **Decisão do usuário:** a rota Raspberry Pi/Firefox foi descartada; o
  Coupon Collector passa a rodar no mesmo Windows Server da produção,
  usando Microsoft Edge real via CDP dedicado (mesmo padrão do
  `collection_worker`, TASK-109: subprocess direto + `connect_over_cdp`,
  porta/perfil próprios e distintos — `9224` vs `9223` da produção,
  nunca `.launch()` gerenciado). **Mas continua como repositório
  totalmente separado**, nunca incorporado a este repositório como
  `app/coupons/` — decisão explícita: precisa ser possível
  desacoplar/mover para outra máquina sem tocar em nada deste projeto.
  Repositório: `https://github.com/jhonnatancesar/AIShoppingAgent-cupom.git`.
- **O que isso significa na prática:** o Coupon Collector não reaproveita
  `EdgeCdpSupervisor`, `asyncpg`/sessão de banco, nem a ferramenta de
  deploy Windows deste repositório (`manage_collection_worker_task.ps1`)
  — tem sua própria versão simplificada de cada um desses (ciclo de
  vida do Edge por rodada, não por lease/idle-timeout; persistência
  SQLite local, com `PostgresCouponStore` planejada como próximo passo
  quando o usuário fornecer as credenciais reais do Postgres de
  produção; `install.ps1`/`manage_coupon_worker_task.ps1` próprios,
  inspirados no padrão deste repositório mas sem importar nada dele).
  Nenhum código deste repositório foi alterado por esta decisão.
- **Validado ao vivo:** smoke test real contra Kabum (Edge dedicado
  sobe via CDP, navega, encontra cupons reais `GAMER10`/
  `ASUSCOMPREJUNTO`/`KABUMPASS`, persiste, RAM do Edge dedicado medida
  corretamente). Amazon/Magalu/Mercado Livre ainda não revalidados
  contra o novo transporte Edge (só Kabum, nesta rodada).
- **Superado do `DEC-093`:** só a localização do código (`app/coupons/`
  vs. repositório externo) e o transporte (Firefox/Pi vs. Edge/Windows).
  Toda a arquitetura de negócio do `DEC-093` (varredura por loja,
  regra de evidência, escopo, separação do `collection_worker`,
  aplicabilidade/notificação como fase futura do lado do backend)
  permanece válida e não foi alterada.

## DEC-104 — padronização da configuração do worker Windows nativo (`v1.2.2`)

- **Data:** 2026-08-28.
- **Classificação:** Correção operacional escopada + fechamento de dívida
  documentada (achado durante o deploy de produção da V1.2, seguindo
  `DEC-103`).
- **Contexto:** `docs/architecture/windows-collection-worker.md`
  (`v1.2.1`) admitia explicitamente que o "mecanismo de armazenamento
  local [dos secrets do worker] ainda não estava padronizado" -- na
  prática, o worker crashava no primeiro start real em produção por
  faltar `AISHOPPING_DATABASE_PASSWORD`/`AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`
  (sem eles, `build_database_url`/`build_admin_dev_ai_provider_manager`
  lançam exceção no startup).
- **Achado à parte, corrigido na mesma janela**: a auditoria da ACL de
  `C:\App\AIShoppingAgent\.secrets\` encontrou `BUILTIN\Users` com
  `ReadAndExecute` herdado (qualquer usuário local conseguia ler os
  secrets) e SIDs órfãos com `Modify`/`FullControl` não relacionados à
  aplicação. Corrigido para exatamente três identidades:
  `CESAR-SERVER\Administrator`, `BUILTIN\Administrators`,
  `NT AUTHORITY\SYSTEM` -- cobre os três consumidores reais (Docker
  Desktop, roda como `Administrator` nesta máquina; `AIShoppingAgentOpsAgent`,
  roda como `LocalSystem`; a Scheduled Task do worker, `LogonType
  Interactive` como `Administrator`).
- **Decisão:** secrets do worker usam exclusivamente `*_FILE` apontando
  para os arquivos já existentes em `.secrets\` (mesma fonte que os
  containers Docker já usam via `secrets:` do `compose.yaml`) -- nunca
  duplicados para outro diretório, nunca em `backend\.env`, nunca como
  valor direto de variável de ambiente. Configuração não secreta
  (`AISHOPPING_DATABASE_HOST`, `_PORT`, `AISHOPPING_EDGE_CDP_URL`) e as
  referências `*_FILE` vivem em variáveis de ambiente de **Máquina** do
  Windows, geridas de forma reproduzível por
  `scripts\manage_collection_worker_config.ps1` (`-Action
  Install|Update|Status|Remove`, `-WhatIf`, preflight que nunca inventa
  valor para secret obrigatório ausente). Tabela completa de settings
  consumidos pelo worker, classificados obrigatório/opcional/secreto, em
  `docs/architecture/windows-collection-worker.md`.
- **Achado técnico confirmado ao vivo**: variáveis de Máquina gravadas via
  `[Environment]::SetEnvironmentVariable(..., "Machine")` **não**
  aparecem em processos-filho de uma sessão shell já aberta (herança de
  ambiente do processo pai), mas o Task Scheduler monta o ambiente do
  zero a cada disparo -- confirmado nesta PROD: gravar as variáveis e, na
  mesma sessão já logada, disparar `Start-ScheduledTask` (via Windows Ops
  Agent) já iniciou o worker com a config nova, sem logoff/reboot/restart
  de serviço. Por isso nenhum launcher/wrapper intermediário foi
  necessário.
- **Publicado como `v1.2.2`** (compose.yaml inalterado desde `v1.2.1`;
  mudança é script PowerShell novo + documentação -- sem alteração de
  código Python, sem rebuild de imagem, sem migration).

## DEC-103 — deploy V1.2.0: `host.docker.internal` não resolvia dentro dos containers (Ops Controller ↔ Windows Ops Agent)

- **Data:** 2026-08-28.
- **Classificação:** Correção operacional escopada (achado durante o
  deploy de produção da V1.2.0, gate de validação `ops_controller` ↔
  Windows Ops Agent).
- **Achado real (não hipótese)**: neste servidor, `host.docker.internal`
  não resolve dentro de nenhum container (testado em rede `bridge`
  padrão, na rede *user-defined* do projeto e nos containers reais `api`/
  `ops_controller` -- todos com `NXDOMAIN`/`gaierror`). Causa raiz
  confirmada por leitura de `C:\Users\Administrator\.docker\daemon.json`:
  o servidor mantém `{"dns": ["1.1.1.1", "8.8.8.8"]}` fixo desde o
  incidente de 2026-08-20 (Tailscale/MagicDNS interferindo na resolução
  de `files.pythonhosted.org` durante builds, resolvido fixando DNS
  externo). Esse override é herdado pelo resolvedor DNS embutido
  (127.0.0.11) de cada container, que passa a encaminhar também os nomes
  mágicos `*.docker.internal` para os servidores externos -- que
  corretamente devolvem NXDOMAIN, pois não são domínios públicos reais.
- **Decisão:** não reverter o override de DNS do `daemon.json` (reabriria
  o incidente do Tailscale). Em vez disso, `ops_controller` -- único
  consumidor de `WINDOWS_OPS_AGENT_URL` -- ganha
  `extra_hosts: ["host.docker.internal:host-gateway"]` escopado só nele
  (`compose.yaml`). `host-gateway` é resolvido pelo próprio Docker Engine
  (independe do proxy DNS do Docker Desktop), validado empiricamente
  nesta máquina: resolve para `192.168.65.254`, TCP conecta em
  `192.168.65.254:8021` e uma chamada HMAC `STATUS` real (mesmo código de
  produção, secrets reais) completou com sucesso (`200`,
  `{"service":"collection_worker","status":"stopped"}`); requisições sem
  cabeçalhos de autenticação (`422`) e com assinatura inválida (`401`)
  continuam rejeitadas. Publicado como `v1.2.1` (compose apenas -- sem
  mudança de código Python, sem nova migration).
- **Por que não bind `0.0.0.0` no Ops Agent nem `daemon.json` global**: o
  bind em `127.0.0.1:8021` já era alcançável via `host-gateway` sem
  qualquer mudança de superfície de exposição; alterar o DNS global ou o
  bind do Ops Agent teria blast radius maior (todo o host / todo o
  serviço) para resolver um problema que afeta só um consumidor.

## DEC-102 — TASK-113: avaliação inteligente de preço e checkpoint de alerta

- **Data:** 2026-08-27.
- **Classificação:** Nova capacidade (base: `DEC-045`/`DEC-048`/`DEC-097`, TASK-097/TASK-112).
- **Decisão:** `evaluate_price_alerts` ganha checkpoint opcional
  (`AlertCheckpoint`, espelha a tabela nova `MissionProductAlertState`,
  PK `(mission_id, product_id)` -- nunca `offer_id`, para o mesmo Product
  em duas lojas dentro da mesma Mission compartilhar o checkpoint).
  `checkpoint is None` preserva 100% o comportamento anterior (os 17
  testes já existentes de `tests/test_price_alerts.py` continuam
  passando sem alteração); com checkpoint, a decisão exige melhoria
  material vs. `best_notified_amount` (caminho B) ou REARM + janela +
  `MarketPriceAssessment` `GOOD_DEAL`/`EXCELLENT_DEAL` (caminho C) --
  nunca mais um alerta só por cair vs. a observação imediatamente
  anterior. `MarketPriceAssessment` (`app.market_research`, tabela nova
  `market_price_assessments`) é a avaliação de mercado externa
  (Firecrawl `/v2/search` + `/v2/scrape` básico como fallback, IA via
  `AIProviderManager` convencional -- nunca grounding nativo), chaveada
  por `Product.identity_key` (nunca `MonitoringItem`, que agrupa N
  Products distintos), com single-flight crash-safe (claim atômico via
  `INSERT ... ON CONFLICT ... WHERE`, estados `processing`/`ready`/
  `failed` com lease/retry_after/TTL) para nunca duplicar pesquisa entre
  Missions/workers concorrentes.
- **Achado real durante a implementação (decisão de arquitetura, não só
  código)**: a decisão final de alerta acontece dentro da MESMA seção
  crítica que `_persist_phase_c` (`app.collection.orchestration`) já
  mantinha para `MissionOfferRelevance` -- o `SELECT missions ... FOR
  UPDATE` no topo da função já serializa toda a Fase C por `mission_id`,
  então um lock adicional dedicado em `MissionProductAlertState` seria
  redundante (nunca duas transações da mesma Mission avançam em
  paralelo). Provado sob concorrência real (duas conexões `asyncpg`
  simultâneas, `asyncio.gather`), não só por comentário --
  `tests/integration/test_alert_checkpoint.py::
  test_two_stores_same_mission_product_never_double_alert`.
- **Dois bugs reais corrigidos durante a própria escrita dos testes de
  integração** (`app/market_research/service.py`): (1) o UPSERT do
  single-flight comparava `lease_until`/`retry_after`/`expires_at`
  contra `now()` do Postgres (relógio real da máquina), não contra o
  `now` lógico passado pelo chamador -- em produção isso nunca
  divergiria de forma visível (o `now` de produção é sempre "agora"
  mesmo), mas quebrava tanto testabilidade quanto a disciplina de "um
  único relógio por operação" já seguida pelo resto do projeto
  (`effective_now`); corrigido para `:now` (parâmetro vinculado). (2) o
  fallback de busca engolia `FirecrawlSearchError` da própria chamada de
  busca inicial (indisponibilidade do SERVIÇO Firecrawl) e seguia como
  se a pesquisa tivesse rodado normalmente sem achar nada
  (`INSUFFICIENT_EVIDENCE`, cacheado pelo TTL inteiro) -- corrigido para
  propagar essa falha para `mark_assessment_failed`/`retry_after`,
  nunca fabricar um resultado "pesquisado com sucesso" que nunca
  aconteceu.
- **Próxima ação:** suíte de integração focada (`test_market_research.py`,
  `test_alert_checkpoint.py`, 13 testes) mais as suítes preexistentes de
  `shared_collection`/`collection_orchestration` (51 testes, 1 regressão
  encontrada e corrigida -- assinatura de um monkeypatch de teste que não
  aceitava os novos parâmetros opcionais) rodando 100% verdes contra
  PostgreSQL real. Pendências reais registradas em `docs/tasks/
  TASK-113.md` §39: suíte de integração exaustiva do §33.27 (além dos 13
  testes focados já escritos) e validação com uma chave Firecrawl real
  (nunca testada contra a API de verdade nesta rodada) ficam para quando
  o recurso for ativado em produção.

## DEC-101 — TASK-112 fase 3B: fila justa unificada (fairness_owner) + política de cadência

- **Data:** 2026-08-27.
- **Classificação:** Nova capacidade (base: `DEC-098`/`DEC-099`/`DEC-100`).
- **Decisão:** o scheduler de produção (`CollectionOrchestrator`) passa a
  usar `claim_due_work` (`app/collection/orchestration.py`), que unifica
  fairness entre o caminho antigo (por `Mission`) e o novo (por
  `MonitoringItem`) numa única fila -- um usuário conta como "dono" no
  máximo uma vez por ciclo, seja por missão solta due ou por ser
  `fairness_owner` de um ou mais claims compartilhados. `claim_due_
  collections`/`_select_due_schedules_for_batch` continuam existindo,
  quase sem mudança (só anti-join contra `MissionMonitoringItem`) --
  compatibility API, nunca mais usada pelo orchestrator de produção
  (auditado: só testes chamam direto, nenhum outro runtime real).
- **Desenho revisado em 6 rodadas de revisão antes de qualquer código**
  (histórico completo na conversa de implementação, resumo abaixo é só o
  desenho final). Rodadas anteriores tentaram: token de fairness
  comparado por igualdade (rejeitado -- não impede duas execuções
  concorrentes com `now` diferentes de creditarem o mesmo usuário);
  seleção travando candidatos no momento do scan (rejeitado -- trava
  candidatos que o corte de `max_users` vai descartar); scan explodido
  por vínculo usuário×item (rejeitado -- um item popular consumiria toda
  a janela de scan sozinho); ordem de locks "loja depois usuário"
  (rejeitado -- permite deadlock cruzado entre transações concorrentes,
  provado com cenário concreto).
- **Reserva de fairness — mecanismo final:** `app.collection.fairness.
  _reserve_fairness_owners` trava `UserCollectionQueueState` dos
  candidatos via `SELECT ... FOR UPDATE SKIP LOCKED`, em ordem ASCENDENTE
  de `user_id`, SEMPRE antes de qualquer lock de loja -- é este lock
  contínuo (mantido até o commit/rollback da transação inteira), não uma
  comparação de token, que impede duas execuções concorrentes de
  `claim_due_work` de reservarem o mesmo usuário ao mesmo tempo, e a
  ordem fixa (usuário sempre antes de loja, loja sempre em ordem de
  `store_id`) que torna o deadlock cruzado estruturalmente impossível
  (nenhuma transação jamais faz loja-antes-de-usuário). Só DEPOIS da
  reserva os recursos são de fato reivindicados, numa lista única
  ordenada por `(store_id, due_at, kind, resource_id)` -- nenhum caminho
  tem prioridade estrutural sobre o outro na mesma loja. Cooldown
  (`_commit_fairness_turn_for_owner`, `UPDATE` simples, sem CAS -- o lock
  da reserva já garante exclusão mútua) só é gravado para donos com >= 1
  claim real; dono reservado sem nenhum claim real nunca paga cooldown.
  `UserCollectionQueueState.last_fairness_turn_id` (coluna nova) é só
  rastro de auditoria, não o mecanismo de corretude.
- **`CollectionRun.fairness_owner_user_id`** (coluna nova): gravado na
  MESMA transação/savepoint do claim compartilhado -- trilha de auditoria
  completa numa linha (`CollectionRun → monitoring_item_id → store_id →
  fairness_owner_user_id`). `NULL` no caminho antigo (reforçado por
  `CHECK`) e também no caminho compartilhado STANDALONE (`collect_
  monitoring_item_store` chamada fora do orchestrator -- script/ADMIN/
  teste -- "execução shared fora da fila de fairness", nunca "esqueceram
  de gravar"); nunca `NULL` no caminho orquestrado. `ondelete=RESTRICT`
  auditado contra `app.privacy.service.deidentify_account` -- nunca apaga
  a linha `User`, só remove credenciais/`telegram_user_id`, então a FK
  nunca fica pendurada na prática.
- **Seleção somente-leitura, sem explosão por vinculados:** `_select_due_
  work_for_batch` conta `MonitoringItemStore` ÚNICO como candidato
  (nunca 1 vínculo Mission/User = 1 candidato -- um item com 100
  vinculados nunca consome mais que 1 posição da janela de scan).
  `candidate_scan_limit` (default 1000) é só para descobrir trabalho/
  donos, nunca um teto de execução. `EXPLAIN ANALYZE` contra 5000
  `MonitoringItemStore` sintéticos (2% due) confirmou o índice novo (`ix_
  monitoring_item_stores_due`) em uso via Bitmap Index Scan, execução
  sub-milissegundo -- `next_eligible_at` não entrou no índice por
  desenho (seletividade dominada por `next_run_at`), não "no escuro".
- **Sweep de fan-out** (`sweep_shared_collection_fan_out`, chamado no
  INÍCIO de todo `run_batch`, antes de qualquer claim novo): orçamento em
  duas dimensões (`target_scan_limit`/`task_budget`, nunca "todos os
  pendentes"), alocado em rodadas via `per_target_task_cap` -- um alvo
  com backlog grande nunca monopoliza o ciclo enquanto alvos menores
  também estão devidos. `recover_stale_fan_out_tasks` roda 1x por sweep
  (nunca 1x por alvo). Achado real corrigido nesta fase: `_process_
  pending_fan_out` ordenava por `mission_id` (arbitrário) -- agora
  `COALESCE(next_retry_at, created_at)`, entre alvos e dentro de cada
  alvo, para que retry antigo nunca seja starvado por tarefas novas.
  `attempted_task_count` (contagem real de tarefas reivindicadas) é o
  contrato de orçamento, nunca a soma dos buckets de resultado (não
  cobrem retry transitório).
- **Política de cadência** (`app/collection/cadence.py`, módulo novo) --
  nova camada, distinta de fairness (decide QUEM) e de `StoreThrottleState`
  (rajada de curtíssimo prazo). Prioridade fixa: backoff (DEC-046) sempre
  vence > `PROMO_CALENDAR`/`HIGH_ACTIVITY` (30-45min, piso absoluto de
  30min) > `NORMAL` (45-75min, alvo ~60). `PromotionalWindow` (tabela
  nova) é calendário promocional como dado, não código -- ADMIN insere/
  remove janelas sem nenhuma migration nova. Atividade comercial alta é
  POR LOJA, sinal determinístico já durável (nova `PriceObservation` só
  existe quando o estado comercial mudou de verdade, TASK-093/DEC-097 --
  contar linhas novas numa janela já é contar mudanças reais, sem schema
  novo para a contagem). `StoreActivityState` (tabela nova) guarda só a
  histerese (`high_activity_until`) para não alternar modos a cada ciclo
  bem na borda do limiar.
- **Migration `20260826_0001`** (aditiva, única): `UserCollectionQueueState.
  last_fairness_turn_id`, `CollectionRun.fairness_owner_user_id` + FK +
  `CHECK`, índice `ix_monitoring_item_stores_due`, tabelas `promotional_
  windows`/`store_activity_state`.
- **Organização de código:** dois módulos novos, neutros --
  `app/collection/fairness.py` e `app/collection/shared_claim.py`
  (claim compartilhado, movido de `shared_collection.py`) -- eliminam a
  dependência circular que existiria se essa lógica vivesse em
  `orchestration.py` ou `shared_collection.py` diretamente (que já
  depende de `orchestration.py` para ~17 símbolos privados reaproveitados
  desde a fase 3A). O único sentido restante de dependência
  (`CollectionOrchestrator` chamando execução do caminho compartilhado)
  usa injeção de dependência no construtor, mesmo padrão de `identity_
  resolver` (TASK-083).
- **Fora de escopo, deliberadamente:** backfill de missões antigas para
  o caminho compartilhado (§14 do `docs/tasks/TASK-112.md`) -- coexistência
  permanente confirmada (missão nunca vinculada continua para sempre no
  caminho antigo); painel ADMIN de calendário promocional (hoje só
  inserção direta de linha, sem UI); qualquer mudança em TASK-093
  (dedupe) ou TASK-107 (cotas) -- cota do usuário continua sem relação
  com velocidade de scraping da mesma busca.
- **Validação:** suíte de integração completa (152 testes) verde contra
  PostgreSQL real, incluindo toda a suíte pré-existente de fase 3A/
  TASK-108 sem NENHUMA modificação -- prova que o caminho antigo
  degenera exatamente no comportamento de sempre quando não há
  trabalho compartilhado. 15 testes de integração novos dedicados
  (exclusão de vinculada, `claim_due_collections` sem efeito shared,
  rider nunca avança, dono sem claim não paga cooldown, owner NULL só
  standalone, concorrência real sem duplicar claim, cadência NORMAL/
  PROMO/backoff/atividade alta). Suíte unitária (não-DB) completa
  também verde, com 2 testes pré-existentes ajustados para os campos
  novos de `CollectionBatchResult`.

## DEC-100 — TASK-112 fase 3A: coleta compartilhada durável com fan-out individual

- **Data:** 2026-08-26.
- **Classificação:** Nova capacidade (base: `DEC-098`/`DEC-099`). Commit
  `471e898`.
- **Decisão:** uma necessidade `(MonitoringItem, store)` executa UMA
  coleta real (provider chamado 1x, `Offer`/`PriceObservation`
  persistidos 1x -- nunca N vezes confiando no dedupe da TASK-093 como
  rede de segurança) e distribui o resultado para as `Mission`s
  vinculadas via fan-out individual. `CollectionCriteria` canônico
  (`app.products.identity.canonical_collection_criteria`) nasce só de
  `MonitoringItem.canonical_identity`, nunca do texto cru de nenhuma
  Mission vinculada -- corrigido um bug de VRAM no extrator de GPU nessa
  auditoria (`_gpu`/`_gpu_vram`): "RTX 5070 Ti" e "RTX 5070 Ti 16GB"
  nunca compartilhavam `monitoring_key` corretamente antes da correção.
- **`CollectionRun`/`CollectionRequest`:** `CollectionRun` ganha
  `monitoring_item_id` + `CHECK ck_collection_runs_ownership_xor`
  (`mission_id` XOR `monitoring_item_id`, nunca os dois, nunca nenhum).
  `CollectionRequest` corrigido como contrato explícito (mesma regra
  XOR), nenhum caller mais reaproveita `mission_id` para carregar um
  `monitoring_item_id` (hack eliminado).
- **Persistência comercial exatamente uma vez:** roda em
  `_persist_shared_offers_and_finish`, nunca uma vez por Mission do
  fan-out (correção de desenho intermediária desta fase -- a primeira
  versão chamava a resolução comercial por Mission, um uso indevido do
  dedupe da TASK-093/`DEC-097`, que deduplica ENTRE coletas no tempo,
  não entre beneficiários da MESMA coleta). O fan-out reaproveita sem
  alteração o pipeline já existente de checkpoint/pré-lista/alerta por
  Mission (TASK-079).
- **Fan-out durável e resumível:** `SharedCollectionOffer` (qual oferta/
  observação pertenceu a qual coleta compartilhada, inclusive quando a
  observação foi reaproveitada/redundante) e `SharedFanOutTask` (item de
  trabalho durável por Mission) são criados ATOMICAMENTE na MESMA
  transação que persiste `Offer`/`PriceObservation` e marca a
  `CollectionRun` compartilhada `SUCCEEDED` -- ou tudo commita junto ou
  nada commita (run continua `RUNNING`, seguro repetir o provider).
  `resume_shared_collection_fan_out` retoma só tarefas pendentes sem
  nunca rechamar o provider.
- **Máquina de estados final do fan-out** (`SharedFanOutStatus`:
  `pending`/`processing`/`done`/`skipped`/`attention_required`/
  `terminal_failed`): erro nunca vira terminal sem prova -- só os dois
  casos deterministicamente irrecuperáveis (`SharedFanOutTerminalError`:
  Mission/critério sumiu, produto sumiu) viram `terminal_failed` de
  imediato; qualquer outro erro é RETRYABLE por padrão, e esgotar
  `_MAX_FAN_OUT_ATTEMPTS=5` tentativas reais vira `attention_required`
  (auditável via `last_error`/`attempt_count`, reprocessável, nunca
  perda silenciosa), nunca `terminal_failed` por essa via. Elegibilidade
  da Mission (`_mission_still_eligible_for_fan_out`: existe, `ACTIVE`,
  mesmo `MonitoringItem`, ainda tem `MissionSource` da loja) é
  revalidada logo após o claim atômico da tarefa, antes de qualquer
  efeito -- pause/cancel/relink entre a coleta e o fan-out marca a
  tarefa `skipped`, nunca gera alerta indevido. Tarefa presa em
  `processing` além do lease é recuperada automaticamente dentro de
  `resume_shared_collection_fan_out` (primeiro passo, sempre), sem
  intervenção manual. Notificação usa o outbox idempotente já existente
  da TASK-080 (`claim_unconsumed_events_async`/
  `record_consumption_attempt_async`), nunca um envio direto dentro do
  processamento -- confirmado, não criado nesta fase.
- **Concorrência:** claim real via `CollectionRun.monitoring_item_id` +
  índice único parcial `uq_collection_runs_running_monitoring_item_store`
  (mesma técnica já usada por `mission_id`), nunca mutex em memória --
  dois workers nunca executam o mesmo `(MonitoringItem, store)`
  simultaneamente. Claim da `SharedFanOutTask` também atômico (`UPDATE
  ... WHERE status='pending' ...`).
- **Migrations locais** `20260825_0001..0004` (head único, cadeia
  linear, `downgrade()` reversível em todas), incluindo backfill
  determinístico de `MissionOfferRelevance.last_observation_id` para
  linhas pré-existentes (reconstruído a partir de `CollectionRun.
  mission_id`).
- **Fora de escopo, explicitamente adiado para a fase 3B:** integração
  real com o scheduler de produção (quando/com que frequência chamar
  `resume_shared_collection_fan_out`/`recover_stale_fan_out_tasks` em
  produção -- hoje só chamadas isoladamente, testáveis, não plugadas em
  nenhum loop real) e `fairness_owner`/fila justa por claim compartilhada
  (TASK-108 revisada, mencionada no desenho original do roadmap).
- **Achado incluído nesta fase, sem relação direta:** corrigido o teste
  desatualizado da TASK-111 (`test_same_variant_from_all_stores_reuses_
  one_global_product` esperava só 4 lojas, faltavam Magalu/Mercado
  Livre) -- reapareceu na regressão desta fase, corrigido junto no mesmo
  commit.

## DEC-099 — TASK-112 fase 2: Shared Monitoring entre missões equivalentes

- **Data:** 2026-08-25.
- **Classificação:** Nova capacidade (base: `DEC-098`). Commit `5d05767`.
- **Decisão:** `MonitoringItem`/`MissionMonitoringItem`/
  `MonitoringItemStore` novos para que missões com a mesma
  `monitoring_key` compartilhem a necessidade real de coleta, sem
  duplicar agendamento por loja. Vínculo/relink/desvínculo centralizados
  em `reconcile_mission_monitoring_item(_async)` (`app/missions/
  monitoring.py`) -- único ponto de entrada para todo caller que pode
  alterar a identidade relevante de uma missão (criação, seleção de
  variante, confirmação pós-coleta, desidentificação de conta).
- **Identidade efetiva e `scope`:** resolvida com precedência
  determinística (`Product` selecionado > `VariantSelectionMode.ALL`/
  família > texto), sempre pelo mesmo núcleo de hashing
  (`_build_monitoring_identity`). `monitoring_key` sobe para v2 e passa
  a levar `scope` explícito (`SPECIFIC`/`FAMILY`/`GENERIC` --
  `MonitoringScope`) para que "variante específica" e "qualquer variante
  da família" nunca colidam mesmo descrevendo a mesma família de
  produto. `VariantSelectionMode.ALL` gera escopo `FAMILY` (correção
  durante a própria fase: a versão anterior forçava `ANY`
  incondicionalmente e apagava restrição real já especificada, ex.
  "iPhone 17 128GB" em modo ALL perderia o 128GB) -- a única diferença
  de `SPECIFIC` é que atributo bloqueante ausente não falha fechado;
  toda restrição que o texto de fato especificou continua preservada.
  `variant`/`attributes` nunca chegam a `None` no payload canônico --
  sempre `"ANY"` explícito, nunca ausência/`null`. `GENERIC_CATEGORY`
  mantém contrato fail-closed explícito (reservado, hoje inalcançável).
- **Lifecycle:** pause/resume/cancel deriva `is_enabled` por (item,
  loja) com serialização real (`SELECT ... FOR UPDATE` + reconsulta
  pós-lock, lojas travadas em ordem determinística por `store_id`) --
  corrige corrida onde duas missões pausando o mesmo vínculo quase ao
  mesmo tempo poderiam deixar nenhuma desabilitar a loja compartilhada;
  coberto por teste de concorrência real.
- **Migration** aditiva, head `20260824_0003`.
- **Fora de escopo, explicitamente adiado para a fase 3:** scheduler
  compartilhado, fan-out e `fairness_owner`.

## DEC-098 — TASK-112 fase 1: Product Identity Engine genérico com CPU/GPU

- **Data:** 2026-08-25.
- **Classificação:** Nova capacidade (evolução aditiva de `app/products/
  identity.py`, TASK-097 -- `identity_key`/`family_key`,
  `ProductRequestKind` e o comportamento de iPhone/Galaxy S continuam
  intocados). Commit `1dca734`.
- **Decisão:** motor de identidade determinístico e versionado via
  `CategoryDefinition`/`AttributeDefinition` -- registry plugável,
  atributo bloqueante (exige resolução, mesmo papel de `storage_gb`
  hoje) vs default `ANY` (nunca bloqueia, sempre explícito na chave,
  nunca omissão silenciosa). 23 categorias registradas; `cpu`/`gpu`/
  `smartphone` com extractor de texto funcionando e testado, as demais
  (tablet, notebook, desktop, monitor, tv, ram, ssd, hdd, motherboard,
  psu, case, cooler, keyboard, mouse, headset, console, controller,
  camera, router, printer) com ontologia declarada, inertes até
  ganharem extractor.
- **Extractors novos** (`_cpu`, `_gpu`) registrados no mesmo
  `_EXTRACTORS` de sempre -- `resolve_product_variant`/
  `classify_product_request` (produção real) passam a reconhecer CPU/
  GPU também, de graça. `family` do CPU sempre derivado do 2º dígito do
  código do modelo (esquema público da AMD, verificado Zen2-5:
  9950X3D/7950X3D/5950X/3950X = Ryzen 9, X800X3D/X700X = Ryzen 7,
  X600X = Ryzen 5) -- nunca do texto ao redor, para "9950x3d"/"ryzen
  9950x3d"/"amd ryzen 9 9950x3d" convergirem para a mesma identidade.
- **`resolve_monitoring_identity`:** `monitoring_key` versionada
  (`v1:sha256`), só gerada depois da identidade canônica completa
  (categoria + todo atributo bloqueante resolvido); fail-closed em
  qualquer ambiguidade.
- **`ProductIdentityAlias`** (migration `20260824_0002`): fundação
  persistida e determinística de aliases (status `active`/`candidate`)
  -- a IA nunca decide equivalência, só pode sugerir candidato; só alias
  `active` participa da `monitoring_key`.
- **Fora de escopo, explicitamente adiado para a fase 2:** Shared
  Monitoring, fan-out, `fairness_owner`, backfill sobre TASK-108.

## DEC-097 — Dedupe da TASK-093 explícito na fonte; avaliação de alerta é etapa derivada da coleta

- **Data:** 2026-08-24.
- **Classificação:** Correção de bug arquitetural (não TASK-108, achado
  durante a retomada da TASK-108 — trabalho mantido em commit separado).
- **Bug confirmado:** a TASK-093 permite legitimamente reaproveitar a
  mesma `PriceObservation` quando o estado comercial de uma oferta não
  muda entre duas coletas. `_persist_phase_c` reconstruía `current`/
  `previous` a partir de `pending.observation_id`/`previous_observation_id`
  e comparava via `evaluate_price_alerts(...)` sem saber se essa
  reutilização tinha acontecido — quando o reaproveitamento coincidia com
  a própria observação anterior da missão (`current.id == previous.id`),
  o guard de `app/alerts/evaluator.py::_validate_entities`
  (`"observations must be distinct"`, corretíssimo e mantido intocado)
  disparava `PriceAlertEvaluationError`, capturada pelo `except Exception`
  genérico de `_process_claim` — que marcava o `CollectionRun` inteiro
  como `FAILED`, mesmo com a coleta e a persistência da Fase A já
  corretas e já commitadas. Confirmado com prova empírica (worktree
  isolado contra `origin/main` limpo, antes de qualquer código da
  TASK-108) em dois testes de integração legados
  (`test_source_backoff_lifecycle_across_batches`,
  `test_prelist_ready_fires_once_then_errata_corrects_a_cheaper_late_offer`)
  que recoletam a mesma oferta sem mudança de preço.
- **Decisão 1 — dedupe explícito na fonte:** `_persist_phase_a` (onde a
  TASK-093 decide `observation`/reaproveitamento) agora calcula e devolve
  explicitamente, por oferta pendente (`_PendingOffer`):
  `observation_created: bool` (uma linha nova foi persistida vs.
  reaproveitada) e `alert_comparison: PriceObservationComparison`
  (`FIRST_OBSERVATION` / `CHANGED` / `UNCHANGED_REUSED`). `_persist_phase_c`
  nunca mais infere isso comparando IDs reconstruídos por acidente — só
  lê o campo.
- **Decisão 2 — contrato de 3 casos para o evaluator:** primeira
  observação (`previous is None`) mantém a semântica já existente
  (evaluator recebe `previous=None`, pode gerar `PRICE_TARGET_REACHED` já
  na primeira leitura); mudança real (`CHANGED`, IDs distintos) chama o
  evaluator normalmente; estado reaproveitado (`UNCHANGED_REUSED`) **não
  chama o evaluator** — não é uma nova comparação, é reconfirmação do
  mesmo estado já avaliado antes por aquela missão, resultado normal, zero
  alertas, não é erro.
- **Decisão 3 — avaliação de alerta é etapa derivada, não atômica à
  coleta:** a oferta já foi coletada e persistida corretamente (Fase A,
  transação própria já commitada) antes de a Fase C sequer tentar avaliar
  alertas. Um erro real e inesperado do evaluator (não o caso estrutural
  do item 2, resolvido na fonte) passou a ficar isolado por oferta —
  `try/except` ao redor só da chamada a `evaluate_price_alerts`, log
  estruturado (`price_alert_evaluation_failed`, mesmo padrão já usado
  para falha de enriquecimento em `_process_claim`), sem publicar
  eventos falsos e sem propagar a exceção. `finish_collection_run(...,
  SUCCEEDED, ...)`, reset de backoff e avaliação de pré-lista continuam
  rodando normalmente mesmo se uma oferta específica tiver erro de
  avaliação de alerta.
- **Auditoria de callers:** `evaluate_price_alerts` tem exatamente um
  chamador em código de produção (`_persist_phase_c`); nenhum outro
  caminho compara `PriceObservation`s ou infere mudança por IDs.
- **Fora de escopo desta correção:** TASK-108 (fila justa/throttle por
  loja/config ADMIN) permanece em commit separado, testes próprios
  intocados por este fix. Achado à parte, não relacionado: dois testes
  unitários de `test_collection_orchestration_async.py`
  (`test_claim_due_schedule_creates_runs_and_advances`,
  `test_claim_due_collections_skips_mission_already_running`) referenciam
  `find_due_schedules_async`, renomeada para `_select_due_schedules_for_batch`
  pela própria TASK-108 — quebra de teste do escopo da TASK-108, não desta
  correção; sinalizado, não corrigido aqui.

## DEC-096 — TASK-109: migrar collection_worker para Windows nativo com Edge

- **Data:** 2026-08-22.
- **Classificação:** Preflight + plano de migração; nenhum código escrito.
- **Decisão:** tirar o `collection_worker` do Docker/Linux (Playwright/
  Chromium) e rodá-lo como processo Windows nativo controlando um Edge
  real via CDP loopback — generaliza o padrão já validado para Magalu
  (`DEC-090`) e Terabyte (esta sessão, TASK-105) para todas as seis
  lojas. API, PostgreSQL, Telegram notifier, `ops_controller` e
  observabilidade continuam em Docker.
- **Achado favorável do preflight:** a abstração operacional já existe
  pronta para isso -- `app/admin/service_ops.py`
  (`ServiceOps`/`ManagedService`) é runtime-neutro, e
  `app/ops_controller.py` já isola Docker num único
  `DockerOpsAdapter` ("Único ponto que conhece Docker"). A mudança para
  suportar um worker Windows fica local a `ops_controller.py` (novo
  adapter + dispatch por serviço); o painel ADMIN não muda nada.
- **Sem Browser Bridge:** como o worker passa a rodar na mesma máquina
  do Edge, controla via CDP loopback diretamente -- um bridge de rede só
  faria sentido em hosts diferentes.
- **Execução:** Task Scheduler (não Windows Service/Session 0) -- o
  servidor já faz login automático e bloqueia a tela; Session 0 isolaria
  o Edge da sessão interativa que ele pode precisar.
- **Ordem de validação antes de remover Chromium:** Magalu/Terabyte
  (já comprovadas) → Mercado Livre (Edge/CDP como primário, hoje só
  fallback) → Amazon/Kabum/Pichau (nunca testadas em Edge/CDP, mesma
  auditoria real sem evasão já usada nas outras). Chromium só sai depois
  das seis confirmadas.
- **Riscos/pontos em aberto registrados em `docs/tasks/TASK-109.md`:**
  canal de status/controle do `ops_controller` para o worker Windows
  (WSL2 → host) ainda sem solução única óbvia; suposição não confirmada
  de que Postgres/API em `127.0.0.1` do Docker Desktop/WSL2 aparecem no
  `127.0.0.1` do host Windows; comportamento de Edge/CDP com sessão
  Windows bloqueada (não desconectada) não documentado ainda.
- **Fora de escopo:** implementação, deploy, remoção do Chromium antes
  da validação completa das seis lojas.
- **Fechamento (2026-08-23):** as seis lojas confirmadas via Edge/CDP;
  `BrowserSession`/Chromium removidos de todo caminho real de coleta
  (`app/collection/providers/base.py`/`stores.py`) -- sem `cdp_transport`
  configurado, cada provider falha explícito (`EdgeCdpTransportError`),
  nunca mais abre Chromium gerenciado como fallback silencioso.
  `BrowserSession` permanece só como infraestrutura de teste hermética
  (obter um `Page` real para parsing de HTML estático local, sem rede),
  decisão tomada durante o audit ao constatar que ~40 testes a usam
  exatamente para isso, sem relação com o fallback de coleta removido.
  `collection_worker` saiu do `compose.yaml`; Dockerfile parou de instalar
  Chromium/Xvfb (nenhum serviço Docker restante abre navegador); binário
  do Chromium continua necessário só para rodar a suíte de testes
  local/CI (`playwright install chromium`), fora da imagem de produção.
  `ops_controller` ganhou `WindowsOpsAgentAdapter`, resolvendo o "ponto em
  aberto" do canal de status/controle: HTTP loopback assinado
  (HMAC+timestamp+nonce) via `host.docker.internal`, mesmo esquema já
  usado pelo `DockerOpsAdapter`, sem shell genérico. Documentação nova:
  `docs/architecture/windows-collection-worker.md` (arquitetura completa
  do runtime DEV) e `docs/architecture/playwright.md`/
  `docs/architecture/service-operations.md` atualizados. Nenhum deploy
  feito -- produção continua nos sete serviços Docker
  (`docs/installation/windows-server.md` inalterado de propósito).
- **Fechamento, parte 2 (2026-08-24):** usuário pediu zero Chromium
  também na suíte de testes (não só na coleta real). Auditoria dos ~40
  testes que ainda usavam `BrowserSession`: todos eram parsing de HTML
  local (`page.set_content`), nenhum navegava para URL externa --
  categoria "precisa de browser real" ficou vazia. `BrowserSession`
  passou a lançar o Microsoft Edge da máquina
  (`Playwright.chromium.launch(executable_path=...)`) em vez do Chromium
  baixado pelo Playwright; nenhum teste precisou de reescrita (interface
  inalterada). `discover_edge_executable` extraído para
  `app/collection/edge_discovery.py` (fora do pacote `providers`, evita
  import circular com `browser.py`). `python -m playwright install
  chromium` removido de todos os passos de setup (`README.md`,
  `docs/development/dependencies.md`,
  `docs/development/local-pipeline.md`). Resultado: zero binário de
  Chromium em qualquer lugar do projeto, produção e teste na mesma
  direção arquitetural (Edge). **Superada pela parte 3 abaixo** --
  `chromium.launch(executable_path=...)` ainda é `chromium.launch()`.
- **Fechamento, parte 3 (2026-08-24):** usuário apontou a inconsistência:
  mesmo apontando pro Edge, `chromium.launch()` continuava sendo chamado
  -- queria a mesma arquitetura de produção também em teste
  (`Playwright -> connect_over_cdp() -> Edge`, nunca `.launch()` de
  nenhum tipo). `BrowserSession` passou a conectar via
  `EdgeCdpTransport.open_blank_page()` (reaproveitado sem duplicar
  lifecycle) contra um Edge dedicado da suíte, porta/perfil exclusivos
  (`EDGE_SESSION_CDP_URL`/`EDGE_SESSION_PROFILE_DIR`,
  `app/collection/browser.py`) -- nunca a porta/perfil de um Edge real de
  dev/produção. `tests/conftest.py` (novo) sobe esse Edge uma vez por
  sessão de teste (`EdgeCdpSupervisor.ensure_started()`, novo método
  público -- inicia sem lease/monitor/timer) e derruba no fim
  (`close_via_cdp()`, novo -- fecha só via comando CDP `Browser.close`,
  sem depender do handle do subprocesso). Achado técnico durante a
  implementação: setup e teardown do fixture de sessão rodam em
  `asyncio.run()` separados (loops de evento diferentes); tanto
  `lease()` (cria `asyncio.Task` presas ao loop) quanto `stop()`
  (`process.wait()` no handle do subprocesso, também preso ao loop que o
  criou) quebram ao serem reaproveitados de um loop diferente --
  `ensure_started()`/`close_via_cdp()` foram desenhados especificamente
  para nunca tocar em nenhum objeto asyncio preso a um loop que já
  fechou. Nenhum dos ~40 testes precisou de reescrita (interface de
  `BrowserSession` inalterada de novo). `EdgeCdpSupervisor.stop()`
  (produção, sempre um único `asyncio.run()`/loop) não muda.
- **Aprovação e publicação (2026-08-24):** usuário aprovou tecnicamente a
  TASK-109 completa após revisão do diff. Commits publicados em
  `origin/main` (12 commits desde `docs(worker): formaliza TASK-109`,
  `c16c9af`, até o fechamento, parte 3). TASK-108 (`task/108-fair-queue-wip`)
  permanece isolada na própria branch, nada dela entrou em `main`.
  Deploy em produção continua fora de escopo -- feito só quando uma TASK
  de deploy dedicada for aberta.

## DEC-095 — TASK-108: fila justa por usuário, cooldown individual, sem monopolização

- **Data:** 2026-08-22.
- **Classificação:** Planejamento/documentação; nenhum código escrito.
- **Contexto real levantado antes de propor desenho:** `run_batch`/
  `claim_due_collections` (`app/collection/orchestration.py`) hoje não têm
  nenhum conceito de usuário — claims de várias missões/usuários entram
  misturadas no mesmo batch e rodam em paralelo sob um único
  `asyncio.Semaphore(max_concurrency)` global. O único isolamento
  existente é por missão (lock de seção crítica) e por `(mission_id,
  store_id)` (backoff, `DEC-046`) — preservado sem alteração.
- **Decisão:** introduzir fila FIFO/round-robin por `user_id`, com
  `max_concurrent_user_batches=1` (só um usuário processado por vez) e
  cooldown individual por usuário (`user_cooldown_min/max_seconds`,
  **1–3 min** com jitter, corrigido pelo usuário -- proposta original de
  10–15 min era longa demais) depois que o lote desse usuário termina;
  esse usuário volta para o **fim da fila**. Cooldown nunca bloqueia
  outros usuários elegíveis, que seguem imediatamente -- confirmado
  explicitamente pelo usuário, não é mais suposição. `coupon_worker`
  (`DEC-093`) permanece inteiramente fora dessa fila. Pesquisa Web
  recebe rate-limit (não fila de processamento própria, confirmado),
  reaproveitando `max_daily_searches` da `DEC-094` -- não consome
  recursos de coleta/provider, então não precisa de fila.
- **Duas camadas de proteção, confirmado pelo usuário:** a proteção
  principal contra excesso de requisições continua sendo por
  provider/loja (circuit breaker por `source_code`, backoff
  `next_eligible_at`/`consecutive_blocks` em `MissionSource`, `DEC-046`)
  -- já existe, intocada. A fila por usuário desta TASK é uma segunda
  camada, ortogonal, contra monopolização do worker por um único
  usuário; um provider em cooldown nunca trava desnecessariamente os
  demais.
- **Fora de escopo:** qualquer alteração nos limites por provider já
  existentes, sistema de planos (`DEC-094`/`DEC-073`).
- **Implementação (2026-08-24), retomada sobre a arquitetura final da
  TASK-109:** correção à premissa acima -- auditoria confirmou que a
  "proteção principal por provider/loja" NÃO era global entre usuários
  como o texto original supunha. `MissionSource.next_eligible_at`
  (`DEC-046`) é backoff por `(mission_id, store_id)`, não por loja
  isolada -- duas missões de usuários diferentes na mesma loja não
  compartilham nada. O único mecanismo genuinamente global por provider
  era o circuit breaker (`app.core.resilience.CIRCUITS`), e só em
  memória, perdido a cada restart. Nenhum `next_allowed_at`
  persistido e cross-usuário existia. Implementado como terceira camada
  nova, explícita: `StoreThrottleState` (`store_id` como chave, `Postgres`,
  `FOR UPDATE SKIP LOCKED` na mesma transação curta de
  `claim_due_collections`) -- intervalo mínimo global entre QUALQUER duas
  claims da mesma loja, aplicado imediatamente por claim (não em lote no
  fim do batch), para que nenhuma troca de usuário dentro do mesmo ciclo
  fure o intervalo. `DEC-046`/`MissionSource` continuam absolutamente
  intocados -- camadas independentes, nunca fundidas.
  - **Reaproveitamento do WIP** (`task/108-fair-queue-wip`, commit
    `21f1c05`): modelo (`UserCollectionQueueState`), migration, a
    seleção justa em si (`_select_due_schedules_for_batch`/
    `_advance_user_queue_state` em `orchestration.py`) e os 2 testes de
    fairness reaplicaram sem conflito (nenhum dos arquivos tinha
    divergido de `main` desde que o WIP foi criado). O wiring de
    `worker.py` da branch WIP era da arquitetura pré-TASK-109 (Docker) --
    descartado e reaplicado manualmente contra o `worker.py` atual
    (nativo Windows).
  - **Config persistida e editável pelo ADMIN, sem depender só de `.env`**
    (pedido explícito): `CollectionQueueConfig` (linha única, overrides
    nulos por padrão), resolvida a cada `run_batch`
    (`resolve_queue_config`) -- uma mudança do ADMIN vale no próximo
    ciclo, nunca precisa de restart do worker. `GET/PATCH
    /api/v1/admin/queue` (`admin_router.py`) -- mesmo padrão tri-state
    (`model_fields_set`) já usado pelos overrides de cota por usuário
    (`DEC-094`), mesma auditoria (`AuditEntry`).
  - **Achado real durante os testes, documentado e não perseguido:** dois
    testes de integração legados (`test_source_backoff_lifecycle_across_batches`,
    `test_prelist_ready_fires_once_then_errata_corrects_a_cheaper_late_offer`)
    continuam falhando -- já falhavam exatamente assim no WIP original
    ("CONHECIDO QUEBRADO" no commit `21f1c05`, nunca corrigido). O
    cooldown por usuário e o novo throttle de loja foram neutralizados
    explicitamente nesses dois testes (não têm relação com o que eles
    testam -- backoff por provider para um único usuário), mas ambos
    continuam falhando por uma causa DIFERENTE e mais profunda,
    diagnosticada nesta rodada mas não corrigida (fora do pedido):
    `_persist_phase_a` confirma `CollectionRun.status == RUNNING`
    corretamente, mas `_persist_phase_c` (mais adiante, depois da Fase B
    de IA) encontra o mesmo run já fora de `RUNNING` e retorna `False` --
    o intervalo exato em que isso acontece não foi encontrado. Não é
    causado por nenhum código desta TASK (`_persist_phase_a`/
    `_persist_phase_c` não foram tocados); afeta só esses dois testes
    específicos (todos os outros 19 do arquivo, incluindo os 3 novos de
    throttle de loja/config do ADMIN, passam). Decisão de correção fica
    para o usuário -- instrução explícita foi não alterar a lógica da
    fila para satisfazer esses testes, e uma investigação mais profunda
    é trabalho novo, não desta rodada.
  - **Resolvido (2026-08-24), em commit separado:** investigação mais
    profunda (worktree isolado contra `origin/main` limpo, sem nenhum
    código da TASK-108) provou causa raiz diferente e mais precisa da
    hipótese acima -- não é inconsistência de status entre Fase A/C, é
    `PriceAlertEvaluationError` (`app/alerts/evaluator.py`, guard
    "observations must be distinct"): dedupe da TASK-093 pode reaproveitar
    a mesma `PriceObservation` como `current`/`previous` da mesma missão,
    e `_persist_phase_c` não sabia disso. Bug pré-existente, não causado
    pela TASK-108 (reproduzido em `origin/main` limpo). Corrigido em
    `DEC-097` (commit `742dcf2`, separado deste). Os dois testes voltaram
    a passar depois de adaptados para `_select_due_schedules_for_batch`
    (troca mecânica de `find_due_schedules_async`, sem enfraquecer
    nenhuma asserção).
  - **Terceiro achado, sem relação com esta TASK:** rodar a suíte de
    integração inteira (não só este arquivo, só para checagem cruzada)
    revelou `test_product_identity.py::test_same_variant_from_all_stores_reuses_one_global_product`
    também falhando -- asserção hardcoded assume só 4 lojas seed
    (`amazon`/`kabum`/`pichau`/`terabyte`), desatualizada desde que Magalu
    e Mercado Livre foram seedados (TASK-104A/B). Anterior a esta TASK,
    sem relação com fila/throttle -- só registrado aqui por ter aparecido
    durante a validação.

## DEC-094 — TASK-107: cotas por usuário, sem plano/tier novo

- **Data:** 2026-08-22.
- **Classificação:** Planejamento/documentação; nenhum código escrito.
- **Decisão:** limitar por USER `max_active_missions=5`,
  `max_store_slots=18` (1 loja de missão `ACTIVE` = 1 slot) e
  `max_daily_searches=30`, com revalidação em `resume` e em edição de
  lojas de missão ativa. `PAUSED`/`CANCELLED`/`EXPIRED`/`COMPLETED` nunca
  consomem quota. Nenhuma pausa/cancelamento automático em nenhuma
  circunstância — toda liberação de capacidade é ação explícita do
  usuário. UX sempre visível (uso/limite), aviso antes do limite, e
  explicação com ações contextuais ao bater a quota (nunca só "limite
  excedido"). DEV/ADMIN pode sobrescrever quota por usuário no painel já
  existente (`admin_router.py`), com a mesma auditoria já aplicada a toda
  mutação admin.
- **Compatibilidade com `DEC-073`:** não introduz `user_roles`, múltiplos
  papéis, RBAC avançado nem planos FREE/PLUS/PRO — esses continuam
  reservados para a V2. Os defaults ficam como constantes de sistema
  (`Settings`) com override por usuário como único mecanismo de
  diferenciação hoje; isso deixa o caminho pronto para um futuro sistema
  de planos aplicar valores por tier sem redesenhar o mecanismo de
  verificação/consumo.
- **Base real no schema:** `MissionStatus` (string enum já existente,
  `active`/`paused`/`cancelled`/`expired`/`completed`/`draft`),
  `MissionSource` (`mission_sources`, uma linha por `(mission, store)`,
  contada só para missões `ACTIVE` do usuário — diferente da contagem já
  existente em `_mission_prelist_round_complete`, que não filtra por
  status). `max_daily_searches` não tem infraestrutura hoje; padrão mais
  próximo a adaptar é `reserve_telegram_update`
  (`app/telegram/limits.py`), trocando a janela de 1 minuto por 1 dia.
- **Fora de escopo:** sistema de planos, implementação de código nesta
  rodada.

## DEC-093 — Cupom é subsistema independente, nunca acoplado ao StoreProvider/coleta

- **Data:** 2026-08-22.
- **Classificação:** Correção arquitetural da TASK-106, antes de qualquer
  código (mesmo espírito da `DEC-069`, que corrigiu o desenho do
  parcelamento antes de consolidar).
- **Decisão:** cupom não é um campo a mais coletado durante a busca normal
  de oferta. É um subsistema próprio, com collector, persistência e
  avaliação de aplicabilidade independentes do `CollectionOrchestrator`/
  `collection_worker`. Falha, bloqueio ou lentidão do coletor de cupons
  nunca afeta a coleta de preço; e a coleta de preço nunca espera ou abre
  navegação extra por causa de cupom.
- **Motivação:** a auditoria real (mesmo dia) mostrou que cupom não vive
  no mesmo lugar que a oferta — Amazon/Kabum expõem no card de busca (a
  mesma abertura já usada), mas Magalu/Mercado Livre só mostram na
  **home** da loja, fora do fluxo de busca por produto. Acoplar cupom à
  coleta normal criaria navegação extra por Offer (ex.: abrir a home a
  cada oferta), exatamente o tipo de carga repetitiva que gerou o
  bloqueio observado na própria Shopee horas antes. Separar os dois
  processos evita esse acoplamento estrutural.
- **Arquitetura aprovada (detalhada em `docs/tasks/TASK-106.md`):**
  1. **Coupon Collector** — processo próprio, frequência própria, varre
     fontes oficiais por loja (não por missão/produto); reaproveita
     transporte/provider de cada loja só como infraestrutura (função de
     navegação/parsing), nunca a orquestração do `collection_worker`.
  2. **Persistência** — modelo `Coupon` próprio (não é campo de `Offer`),
     com todos os atributos só quando houver evidência real; nunca
     persistido sem evidência.
  3. **Aplicabilidade** — processo separado cruza cupons ativos com
     ofertas relevantes das missões (`MissionOfferRelevance`), decide
     deterministicamente se um cupom se aplica; regra ambígua nunca vira
     afirmação — fica "possivelmente aplicável".
  4. **Notificação** — só dispara quando o cupom transforma a oferta em
     oportunidade relevante; sempre mostra preço atual, desconto, preço
     estimado, código, regra relevante, link e evidência; deixa explícito
     que o preço é estimado até a aplicação real no checkout.
- **Fora de escopo, mantido de propósito:** nenhuma técnica de evasão;
  Firecrawl e agregadores de terceiro continuam fora desta versão
  (`DEC-088`); nenhum código de provider ou collector foi escrito ainda.

## DEC-092 — Adiar TASK-104C (Shopee) por bloqueio anti-bot mesmo autenticado

- **Data:** 2026-08-22.
- **Classificação:** Bloqueio externo confirmado — reação a proteção
  anti-bot real, não decisão de arquitetura do provider. Mesmo *padrão de
  resposta* usado no `DEC-070` original (diagnosticar sem evasão,
  documentar, adiar), **não** o mesmo resultado: a Terabyte foi resolvida
  trocando o transporte para Edge/CDP (TASK-105); a Shopee já foi testada
  exatamente por esse mesmo caminho — Edge/CDP normal, com e sem login —
  e o bloqueio persistiu (ver "Diagnóstico" abaixo). Não há, hoje, uma
  solução equivalente já validada para a Shopee.
- **O que foi tentado, sem evasão:** busca pública sem login (bloqueada,
  `error: 90309999`, redirecionamento para tela de login); Edge/CDP normal
  (mesmo padrão já validado para Magalu/Terabyte) sem login (mesmo
  bloqueio); login real via Google numa sessão Edge/CDP persistente e
  dedicada (perfil próprio, loopback) — login funcionou e a sessão
  persistiu de fato entre fechar/reabrir o Edge (cookies `SPC_ST`/`SPC_U`
  confirmados), mas tanto a busca quanto uma repetição isolada (sessão
  "fria", uma única requisição) foram redirecionadas para
  `shopee.com.br/verify/captcha?...scene=crawler_item...` (CAPTCHA de
  arrastar peça). A API `search_items` respondeu `error: 90309999` mesmo
  autenticada.
- **Diagnóstico:** diferente da Terabyte (bloqueio por características do
  Chromium gerenciado, resolvido trocando para Edge/CDP -- `DEC-070`/
  TASK-105), a Shopee classifica como `crawler_item` mesmo com Edge normal
  via CDP e sessão logada -- o sinal parece estar na própria conexão
  automatizada (CDP/Playwright), não em headless, IP ou ausência de login.
  Não existe hoje um transporte já validado no projeto capaz de contornar
  isso sem técnica de evasão (stealth, CAPTCHA solver, fingerprint).
- **Decisão:** `TASK-104C` fica formalmente **adiada** (não cancelada) --
  nenhum código de provider foi escrito; login humano não é reproduzido
  automaticamente em nenhum fluxo. Fica para estudo futuro quando houver
  uma abordagem sem evasão (ex.: API oficial/parceria, ou mudança de
  comportamento da própria Shopee). Nenhuma técnica de evasão foi
  cogitada ou implementada.
- **Fora de escopo, mantido de propósito:** resolver o CAPTCHA (manual ou
  automatizado), stealth, spoof de fingerprint, proxy, rotação de IP,
  cookies copiados. Credencial usada no teste não foi registrada em
  código, log ou documentação.

## DEC-091 — Mercado Livre usa Edge/CDP somente como fallback final

- **Data:** 2026-08-22.
- **Decisão:** TASK-104B mantém Playwright normal/headed como aquisição
  primária. Somente bloqueio, circuito aberto ou falha de navegação admite uma
  tentativa pelo Edge/CDP loopback já supervisionado; não há retry próprio.
- **Desacoplamento:** os dois transportes entregam a mesma `Page` ao mesmo
  `MercadoLivreProvider.extract()`. Edge/CDP não conhece seller, condição,
  avaliação, identidade, relevância ou ranking.
- **Carga:** sucesso primário nunca toca o Edge. O fallback abre uma página,
  coleta todos os cards necessários e encerra somente essa página; não cria nem
  encerra o Edge dedicado. Enriquecimento de detalhe reúne seller, entrega,
  condição, disponibilidade e avaliação na mesma abertura comum e limitada.
- **Falha segura:** endpoint CDP é restrito a loopback, timeouts são explícitos,
  falha final fica isolada na origem Mercado Livre e não bloqueia outras lojas.
- **Validação real:** por decisão operacional, será feita uma única abertura
  final no Edge, depois de toda preparação offline.

## DEC-090 — Magalu separa transporte do parser SSR e admite Edge/CDP loopback

- **Data:** 2026-08-22.
- **Decisão:** TASK-104A separa aquisição, parser e enriquecimento. O provider
  recebe uma porta `MagaluSearchTransport`; quando configurado, o adapter atual
  conecta a um Edge normal via CDP loopback e entrega o HTML final ao mesmo
  parser `#__NEXT_DATA__`. Edge/CDP é o único transporte operacional desta
  versão; HTTP e Playwright foram removidos do fluxo Magalu.
- **Auditoria:** a carga inicial observada não chamou API pública separada de
  catálogo. O endpoint Next derivável do `assetPrefix` também respondeu 403 no
  runtime do backend; não será tratado como contrato estável.
- **Falha segura:** erro no enriquecimento preserva os resultados básicos e a
  falha da fonte continua isolada por claim no orquestrador. Ausência de nota e
  quantidade permanece `NULL`.
- **Segurança:** configuração rejeita CDP remoto, público, HTTPS, sem porta ou
  com credenciais; somente `127.0.0.1`, `localhost` e `::1` são aceitos. Não há
  headers especiais, stealth, fingerprint, CAPTCHA, proxy ou evasão.
- **Validação real:** Edge 151 normal/CDP loopback retornou HTTP 200; parser SSR
  encontrou 39 itens e o provider real devolveu múltiplas ofertas completas.
  HTTP direto/Playwright gerenciado seguem bloqueados, mas já não são requisito
  quando o transporte CDP está configurado.
- **Operação:** o worker mantém um supervisor dedicado, perfil próprio e CDP
  loopback; reinicia o Edge após queda. Timeouts separados limitam conexão,
  navegação, documento SSR e leitura. Erros não são classificados como
  navegação transitória, evitando retry agressivo e preservando isolamento.
- **Smoke real:** Edge ausente iniciou automaticamente; após encerrar os 8
  processos do perfil dedicado, o supervisor recuperou com novo PID e a busca
  posterior retornou 20 ofertas.

## DEC-089 — Expansão de lojas dividida em TASK-104A/B/C

- **Data:** 2026-08-22.
- **Decisão:** dividir a expansão em TASK-104A Magalu, TASK-104B Mercado Livre
  e TASK-104C Shopee, permitindo implementação e validação real independentes
  sem duplicar domínio ou consumidores.
- **Magalu/Mercado Livre:** `platform` somente quando houver evidência
  explícita de venda pela própria plataforma; parceiro e fulfillment são
  classificados separadamente, e ausência permanece `unknown`.
- **Shopee:** vendedor oficial depende do selo explícito da página e será um
  atributo próprio, tri-state, do `Seller`; nunca será confundido com
  `seller_kind=platform` ou entrega pela Shopee.
- **Navegação:** vendedor, entrega, condição e avaliação aproveitam a mesma
  abertura/enriquecimento da oferta. Nenhuma loja ganha navegação adicional
  exclusiva para esses campos.
- **Ordem:** 104A, 104B e 104C; cupons vêm depois e TASK-098 permanece no fim.

## DEC-088 — Expansão de lojas precede cupons na V1.2

- **Data:** 2026-08-22.
- **Decisão:** após a TASK-103, a próxima prioridade é integrar Magalu,
  Mercado Livre e Shopee pela arquitetura comum de Store Providers. Pesquisa
  de cupons passa ao item seguinte.
- **Limites preservados:** AliExpress continua fora da V1.2 e a TASK-098
  permanece reservada como último item da versão.

## DEC-087 — Comparação exige Product específico resolvido e ownership por Offer

- **Data:** 2026-08-22.
- **Decisão:** TASK-103 compara somente Offers que apontam para o mesmo
  `Product.id` com `identity_key` resolvida pela TASK-097. Título, família,
  missão e IA nunca definem equivalência.
- **Acesso:** Offer âncora e candidatas exigem relevância `MATCH` ou
  `POSSIBLE_MATCH` em missão do USER; `NO_MATCH` e missão alheia falham
  fechados.
- **Comercial:** última observação e avaliação da própria origem; até cinco por
  loja, com ordenação determinística equivalente à pré-lista.
- **Sem expansão:** nenhuma migration, coleta, histórico ou gráfico.

## DEC-086 — Operações ADMIN usam serviços lógicos, não o runtime Docker

- **Data:** 2026-08-22.
- **Decisão:** TASK-102 reúne dashboard, administração e operações rotineiras.
  UI/backend usam `ServiceOps` com allowlist lógica e nunca aceitam shell,
  container ou comando arbitrário.
- **Runtime atual:** controlador independente assinado, rede privada e
  socket-proxy restrito; somente `collection_worker` e `telegram_notifier`.
- **Evolução:** Docker é adapter substituível. Instalação direta futura troca
  somente por `WindowsServiceOpsAdapter`/supervisor equivalente.
- **Dados:** remoção de usuário usa tombstone e preserva todo histórico; API
  keys ficam estruturadas, porém emissão e autenticação seguem desabilitadas.

## DEC-085 — Minha conta edita somente dados existentes da própria sessão

- **Data:** 2026-08-22.
- **Classificação:** implementar agora, como TASK-101 e item 10 da V1.2.
- **Decisão:** `/app/account` usa exclusivamente o `User` resolvido pela
  `WebSession`; a API não recebe `user_id`. Perfil e notificações permanecem
  separados pelas permissões já existentes e todos os métodos mutáveis herdam
  CSRF de `require_web_session`.
- **Reuso:** nome, e-mail, lojas/categorias preferidas e os dois flags de
  notificação são os campos atuais de `User`. A Web e o Telegram passam a
  editar/consumir o mesmo estado persistido.
- **Extensibilidade:** opções válidas são fornecidas pelo backend, sem catálogo
  paralelo no React.
- **Vínculo opcional:** a Web emite uma challenge de alta entropia, armazena só
  o hash SHA-256 por 10 minutos e a consome uma única vez após `/vincular` no
  chat privado autenticado do próprio Telegram. O cliente Web nunca fornece IDs
  Telegram. Desvincular revoga apenas o canal Telegram e preserva a WebSession,
  conta, missões e ofertas.
- **Persistência mínima necessária:** `telegram_link_tokens` é exclusiva para a
  prova de posse; não é uma segunda identidade nem duplica `User`.
- **Fora de escopo:** IA, username, papel, senha e exclusão de conta.

## DEC-084 — Área USER de ofertas usa EXISTS sobre relevância do proprietário

- **Data:** 2026-08-22.
- **Classificação:** implementar agora, como TASK-100 e item 9 da V1.2.
- **Decisão:** `/app/offers` lista Offers já acessíveis ao USER por
  `MissionOfferRelevance → Mission.user_id`, aceitando somente `MATCH` e
  `POSSIBLE_MATCH`. A consulta usa `EXISTS`, não join de saída, para uma Offer
  ligada a várias missões aparecer uma única vez.
- **Estado comercial:** filtros e cards usam somente a última
  `PriceObservation` da própria Offer. Ordenação/paginação são determinísticas.
- **Sem expansão:** nenhuma coleta, tabela, migration, IA, comparação ou
  agregação global de produto. O detalhe continua sendo a TASK-095.

## DEC-083 — Pesquisa Web read-only; missão somente após “Monitorar”

- **Data:** 2026-08-22.
- **Classificação:** implementar agora, como TASK-099 e item 8 da V1.2.
- **Correção:** o desenho inicial criava missão no ato de pesquisar e foi
  rejeitado antes da aprovação. `/app/search` agora consulta de forma read-only
  somente `Product`, `Offer` e a última `PriceObservation` persistidos.
- **Decisão:** pesquisar nunca cria missão, run, observação ou coleta. Após ver
  resultados e variantes, somente “Monitorar” chama o endpoint/service de
  criação existente. Famílias permitem uma, várias ou todas as variantes;
  categorias genéricas continuam válidas sem identidade forçada.
- **Justificativa:** separa exploração de monitoramento sem criar scraper, fila,
  tabela ou estado temporário paralelo e preserva integralmente os três fluxos
  determinísticos da TASK-097.
- **Sem mudança:** providers, IA, Telegram, schema e produção não foram
  alterados. A área geral de ofertas permanece no item 9.

## DEC-082 — Priorizar toda a Web restante antes da comparação

- **Data:** 2026-08-22.
- **Decisão:** adiar comparação entre lojas e executar primeiro o bloco Web
  ainda ausente. A próxima atividade passa a ser pesquisa de produtos pelo site.
- **Auditoria local:** hoje existem `/app`, missões, criação/detalhe de missão e
  detalhe de oferta; `/admin` possui somente a casca inicial. Ainda faltam a
  pesquisa, a área geral de ofertas, minha conta e as áreas administrativas.
- **Nova ordem:** pesquisa vira item 8; ofertas USER, item 9; minha conta, item
  10; dashboard DEV/ADMIN, item 11; administração de dados, item 12; controles
  operacionais, item 13. Comparação entre lojas fica no item 14. Cupons, novas
  lojas e histórico externo passam aos itens 15, 16 e 17. TASK-098 continua
  como último item, agora o 18.
- **Próxima ação:** formalizar a pesquisa de produtos pelo site com o próximo
  número global quando o usuário mandar iniciar.

## DEC-081 — Mover a TASK-098 para o fim da V1.2

- **Data:** 2026-08-22.
- **Decisão:** manter a TASK-098 formalizada e com o mesmo número, mas adiar sua
  execução para o último item da V1.2.
- **Nova ordem:** a ordem aqui registrada foi posteriormente substituída pela
  `DEC-082`; TASK-098 continua sendo o último item da fase.
- **Justificativa:** o usuário decidiu deixar gráficos para o fechamento da
  fase. A dependência técnica da identidade global da TASK-097 continua
  atendida e o escopo interno da TASK-098 não muda.
- **Próxima ação:** substituída pela `DEC-082`, que prioriza a Web restante.

## DEC-080 — Reordenar a V1.2: lojas Magalu/Mercado Livre/Shopee; autenticação comercial e lives na V2

- **Data:** 2026-08-22.
- **Decisão:** remover da V1.2 a consulta autenticada de frete/parcelamento,
  inclusive a modalidade restrita a DEV/ADMIN, e mover integralmente essa
  capability para a V2. Mover também a pesquisa de ofertas em lives para a V2.
- **Novas lojas na V1.2:** o item de expansão de fontes passa a abranger Magalu,
  Mercado Livre e Shopee, cada uma como Store Provider aderente à arquitetura
  comum. AliExpress não entra nesta etapa e permanece futuro.
- **Contagem e ordem:** com a retirada de dois itens, a V1.2 volta de 18 para
  16 itens. O item de novas lojas passa a ser o 15 e menor preço histórico
  externo passa a ser o 16. TASKs já formalizadas até a TASK-098 não mudam.
- **Separação de escopo:** o provider público da Shopee na V1.2 não inclui
  Shopee Live. Coleta autenticada e lives exigem desenho operacional e de
  segurança próprio na V2.
- **Próxima ação:** esta indicação foi posteriormente substituída pela
  `DEC-081`, que moveu a TASK-098 para o fim da V1.2.

## DEC-079 — Separar identidade global (TASK-097) de histórico e gráficos (TASK-098)

- **Data:** 2026-08-22.
- **Decisão:** a TASK-097 passa a tratar exclusivamente identidade global de
  produto/variante e resolução determinística de pedidos específicos/genéricos.
  A TASK-098 fica formalmente reservada para histórico e gráficos e depende da
  identidade concluída pela TASK-097.
- **Identidade:** evoluir `Product` para variante global referenciada pelas
  `Offer`, com chave versionada formada por categoria, marca, família, modelo,
  variante e atributos normalizados relevantes. Cada categoria declara os
  atributos obrigatórios; ausência não é wildcard e impede união entre lojas.
- **Interação:** pedido específico rejeita chave diferente. Pedido genérico
  é distinguido entre `PRODUCT_FAMILY`, que apresenta variantes deduplicadas
  para escolha única, múltipla ou todas na Web e no Telegram, e
  `GENERIC_CATEGORY`, que continua válida sem escolha obrigatória e nunca une
  produtos distintos. IA pode interpretar a entrada inicial, nunca equivalência,
  ordenação ou seleção final. A TASK-098 só compara `SPECIFIC_PRODUCT` resolvido.
- **Compatibilidade:** nenhum `Product` antigo é unido somente pelo título.
  Backfill ocorre apenas com identidade completa determinística; ambiguidades
  permanecem não resolvidas e missões/ofertas/histórico existentes são
  preservados.
- **Próxima ação:** implementar somente a TASK-097. Não antecipar endpoints,
  consultas, métricas ou gráficos da TASK-098.

## DEC-078 — TASK-096: avaliações pertencem à Offer/Store e compartilham a abertura de detalhe

- **Data:** 2026-08-22.
- **Decisão de domínio:** persistir em `Offer` o snapshot atual completo
  (`rating_average`, `review_count`, `rating_observed_at`). Não é nota global de
  Product e não integra `PriceObservation`, pois sua mudança não representa
  mudança de preço/estado comercial nem deve criar observações redundantes.
- **Evidência:** aceitar somente nota e contagem explicitamente declaradas em
  card, JSON-LD `AggregateRating` ou microdata. Ausência/ambiguidade não apaga
  snapshot anterior; contagem abreviada não é convertida em número exato.
- **Navegação e extensibilidade:** `enrich_offer_details` combina vendedor,
  condição, parcelamento e avaliação na mesma abertura, no máximo uma vez por
  oferta. Terabyte permanece só-card sob o bloqueio atual. Uma loja nova adere
  pelo contrato Raw e hooks do provider, sem mudanças nas camadas consumidoras.
- **Apresentação:** página USER e Telegram mostram a origem; sem textos de
  reviews, histórico de notas, agregação, IA ou influência no ranking.

## DEC-077 — TASK-095: primeira página rica é centrada em Offer e exige relevância ligada a missão do usuário

- **Data:** 2026-08-22.
- **Decisão:** criar `GET /api/v1/offers/{offer_id}` e
  `/app/offers/{offer_id}` sobre a `Offer` existente. `Product` fornece o
  título, mas não é raiz da página: a coleta atual cria uma Product por nova
  Offer e ainda não existe canonicalização global suficiente para uma página
  agregada de produto.
- **Ownership:** WebSession + `MISSION_READ`; o SQL exige uma
  `MissionOfferRelevance` `MATCH`/`POSSIBLE_MATCH` ligada a uma `Mission` do
  usuário. `NO_MATCH`, recurso inexistente e recurso alheio são indistinguíveis
  na resposta fail-closed.
- **Snapshot comercial:** usar a última `PriceObservation` por
  `observed_at DESC, id DESC` e somente suas opções de parcelamento. Reutilizar
  Product/Offer/Store/Seller; não expor evidência bruta ou IDs operacionais.
- **Escopo:** detalhe individual e links pelas missões. Sem migration, tabela,
  IA, coleta, reviews, gráficos, comparação entre lojas, cupons ou admin.

## DEC-076 — TASK-094: pré-lista seleciona até cinco ofertas relevantes por loja com ranking comercial determinístico

- **Data:** 2026-08-22.
- **Decisão:** a pré-lista deixa de representar cada loja por seu menor preço
  absoluto e passa a carregar até cinco ofertas por loja. A ordem final é
  relevância persistida (`MATCH` antes de `POSSIBLE_MATCH`), condição
  (`new` > `refurbished` > `used` > `unknown`), vendedor
  (`platform` > `marketplace_partner` > desconhecido), disponibilidade,
  preço/valor total e identificador estável. `NO_MATCH` nunca entra e
  relevância pendente continua bloqueando a primeira publicação.
- **Pool intermediário:** todos os providers usam a mesma pré-seleção de até
  oito candidatos por loja. Foi removido o colapso exclusivo da Amazon para o
  menor preço; oito preserva diversidade suficiente para o top 5 sem enviar os
  até 20 cards de uma loja para a classificação já existente.
- **Condição histórica:** `OfferCondition` percorre
  `RawCollectedOffer` → `NormalizedCollectedOffer` → `PriceObservation`.
  Só evidência explícita do provider classifica recondicionado/usado. Na Amazon,
  a validação real confirmou a regra da plataforma: ausência desses marcadores
  na oferta principal significa `new`; demais providers continuam `unknown`
  sem evidência. A migration `20260822_0002` adiciona o
  campo não nulo com backfill conservador, e a equivalência comercial da
  TASK-093 passa a considerar condição.
- **Eventos e entrega:** novas publicações usam
  `mission.prelist_ready.v2`/`mission.prelist_errata.v2`, com coleção ordenada
  de snapshots reais. V1 permanece aceito. O Telegram agrupa normalmente uma
  mensagem por loja, repartindo apenas pelo limite técnico; a errata reutiliza
  exatamente o mesmo ranking comercial e não considera uma oferta usada mais
  barata uma melhora sobre uma nova.
- **Roadmap:** esta melhoria torna-se o item 4 da V1.2, antes da página rica de
  produto/oferta. O escopo passa de 16 para 17 itens. A ausência do arquivo
  formal da TASK-093 foi apenas registrada, sem reconstrução retroativa.
- **Classificação:** evolução funcional da V1.2, com migration e eventos V2;
  sem nova tabela paralela, nova decisão por IA ou mudança em produção.

## DEC-075 — TASK-092: gerenciamento de missões pela web reaproveita `app.missions` sem nenhuma regra nova; sessão assíncrona própria; correção de `actor_type` na auditoria

- **Data:** 2026-08-22.
- **Ideia:** item 2 da V1.2 -- levar criar/listar/detalhar/editar/pausar/
  retomar/cancelar missão para `/app`, reaproveitando inteiramente o
  domínio já existente (`app.missions.service`/`app.missions.query`),
  nunca um segundo sistema de missões. Preflight com o usuário decidiu
  dois pontos: cancelamento exige confirmação no cliente (React), sem
  mudança de backend/domínio; listagem padrão mostra ativas + pausadas,
  com filtro de status cobrindo todos os estados + "todas".
- **Endpoints chamam só o serviço existente:** `backend/app/webapp/missions_router.py`
  (7 endpoints sob `/api/v1/missions`) não implementa nenhuma regra de
  negócio própria -- só monta request/response em torno de
  `create_mission_from_criteria_async`/`transition_mission_async`/
  `edit_mission_criteria` (já existentes, usados pelo webhook Telegram
  desde a TASK-079/TASK-069) e de 3 funções novas em `app.missions.query`
  (`list_missions_for_user_by_status`, `count_missions_for_user_by_status`,
  `get_mission_detail_for_user` -- leitura pura, mesma camada). Toda
  autenticação/CSRF vem de `Depends(require_web_session)` (TASK-091/
  DEC-074) -- nenhuma configuração extra por endpoint.
- **`get_web_async_session` (novo, `app.database.dependency`):** os
  endpoints web chamam funções assíncronas de missão, mas `webapp/router.py`
  (TASK-091) só tinha sessão síncrona. Em vez de reaproveitar
  `get_telegram_async_session` (que propositalmente NÃO comita automático,
  porque o webhook Telegram precisa controlar a fronteira de transação
  em torno de `await`s de IA/Telegram -- TASK-079), a nova dependência
  reaproveita o mesmo engine assíncrono de processo
  (`get_telegram_async_engine`, sem pool novo) mas comita automaticamente
  no sucesso, como `get_session`: os endpoints de missão não têm nenhum
  `await` de I/O externo no meio da transação, então uma única transação
  por requisição é segura e mais simples.
- **Correção de auditoria encontrada na validação real:** `create_mission_from_criteria(_async)`
  sempre gravava a transição inicial `draft→active` com
  `actor_type="telegram"` hardcoded -- uma missão criada pela web
  aparecia com auditoria incorreta. Corrigido com um parâmetro
  `actor_type: str = "telegram"` (default preserva os dois chamadores
  existentes -- Telegram e `scripts/validate_collection_worker.py`); o
  endpoint web passa `actor_type="web"`. Confirmado no container real
  antes e depois da correção.
- **Classificação:** Segunda interface sobre o domínio de missões já
  existente (TASK-092, item 2 da V1.2) -- nenhuma feature de negócio nova
  além do que o Telegram já faz, nenhuma migration.
- **Justificativa:** o princípio já registrado em `DEC-072`/`v1.2-scope.md`
  ("nunca um segundo sistema de missões") só se sustenta se toda regra de
  negócio ficar de fato numa única camada -- daí a resistência em
  duplicar validação/transição/edição no router web, mesmo quando isso
  significou adicionar pequenas funções de leitura em `app.missions.query`
  em vez de compor queries ad hoc dentro do router.
- **Próxima ação:** nenhuma além da implementação já feita. TASK-092
  aguardando revisão/aprovação do usuário antes do commit.

- **Ideia (rodada 2 — auditoria arquitetural de 21 pontos, 2026-08-22):** o
  usuário revisou a primeira entrega e não aprovou o commit, pedindo uma
  auditoria formal de concorrência, posse, contrato de listagem, UX de
  conflito, agnosticismo de canal e cobertura de teste real (PostgreSQL +
  container). A auditoria confirmou dois bugs reais (não hipotéticos) e
  formalizou quatro pontos que já estavam corretos na prática mas nunca
  tinham sido decididos explicitamente:
  1. **`state_version` -- semântica definitiva corrigida (bug real):**
     `edit_mission_criteria` checava `expected_state_version` mas nunca
     incrementava `mission.state_version` no sucesso -- uma segunda edição
     concorrente na mesma versão nunca era rejeitada (perda silenciosa de
     escrita). A semântica documentada em `docs/architecture/mission-criteria.md`
     ("edição nunca mexe em `status`/`state_version`") descrevia essa lacuna
     como desenho intencional; era proteção incompleta, não escolha. Corrigido:
     `edit_mission_criteria` agora incrementa `state_version` como qualquer
     transição de ciclo de vida -- o campo controla a missão inteira (edição
     de critério + transições), não só o lifecycle. Provado com teste de
     integração real (`test_lost_update_is_prevented_by_state_version`,
     `tests/integration/test_webapp_missions.py`): cliente A edita
     `target_amount` na versão N, cliente B tenta editar `sources` na mesma
     versão N -- B recebe `409`, o valor de A persiste sozinho.
     `docs/architecture/mission-criteria.md` e `docs/database/schema.md`
     atualizados para descrever a semântica corrigida (histórico anterior
     preservado, não apagado).
  2. **`InvalidMissionTransitionError` não tratado no router (bug real,
     introduzido nesta própria TASK):** `_run_command` só capturava
     `MissionNotFoundError`/`MissionVersionConflictError`/
     `MissionTransitionConditionError` -- um comando inválido para o estado
     atual (ex.: `resume` numa missão `cancelled`) levantava
     `InvalidMissionTransitionError`, não capturada, produzindo `500` sem
     detalhe. Corrigido (import + inclusão no tupla de exceções). Só foi
     encontrado porque o usuário pediu explicitamente um teste direto contra
     o endpoint (não só a UI) para `CANCELLED→RESUME`/`CANCELLED→PAUSE` --
     confirma o valor de testar a transição terminal no nível HTTP, não só
     no domínio.
  3. **Posse centralizada:** nova função `get_mission_for_user(session, *,
     user_id, mission_id) -> Mission | None` em `app.missions.query`,
     reaproveitada pelo router web (`_require_owned_mission`) -- ausência e
     posse de outro usuário retornam o mesmo `None`, nunca distinguidos.
     Decisão explícita: **não** migrar os pontos de chamada já existentes do
     Telegram para esta função nesta TASK (código já validado em produção,
     sem necessidade funcional de mexer) -- só o caminho novo (web) usa a
     função nova.
  4. **`actor_type` passa a ser obrigatório (sem default):** o default
     `"telegram"` em `create_mission_from_criteria(_async)` (adicionado na
     primeira rodada desta TASK) foi reavaliado -- um default mascarava
     silenciosamente qualquer chamador futuro que esquecesse de passar o
     valor certo. Todo o código-base já segue essa convenção em toda outra
     função equivalente (`transition_mission_async`, autenticação,
     autorização, privacidade) -- os três chamadores reais (Telegram,
     endpoint web, `scripts/validate_collection_worker.py`) já passavam o
     valor explicitamente, então a correção certa era remover o default, não
     trocar seu valor.
  5. **Contrato de listagem/paginação formalizado:** `limit`/`offset`
     (padrão 20, máximo 100, `422` acima disso), ordenação estável por
     `updated_at DESC` -- já implementado na primeira rodada, agora coberto
     por teste de integração real para cada um dos 6 filtros de status
     (incluindo `expired`, alcançado via transição real `EXPIRE`, nunca
     seed direto).
  6. **UX real de `409` no frontend:** a SPA nunca força a mudança nem
     ignora o conflito -- mostra uma mensagem explicando que a missão mudou,
     recarrega os dados automaticamente e obriga o usuário a revisar antes
     de tentar de novo (`MissionDetailPage.tsx`; formulário de edição
     remonta via `key={mission.state_version}` para não reter estado local
     obsoleto).
- **Validação real (container, 2026-08-22, pós-correções):** imagem
  reconstruída (`docker compose build api`), stack subida com Postgres
  descartável, migrações aplicadas até `20260821_0001` (sem migration nova),
  dois usuários descartáveis criados só para o teste. Confirmado via HTTP
  direto: ciclo completo criar→pausar→editar (`state_version` avança
  2→3)→retomar→cancelar; edição em `ACTIVE` rejeitada (`409`); `resume`/`pause`
  em missão `CANCELLED` rejeitados (`409`, não `500` -- confirma a correção
  do ponto 2 acima); posse indistinguível entre "não existe" e "não é sua"
  (`403 mission_access_denied`, corpo byte-a-byte idêntico); `409` de versão
  obsoleta reproduzido de propósito; filtro padrão exclui cancelada,
  `?status=cancelled`/`?status=all` incluem. Confirmado em navegador real
  (não só `curl`): tela de login, estado vazio ("nenhuma missão encontrada
  para este filtro" -- não é erro), redirecionamento para login quando
  não autenticado, lista populada e troca de filtro pela interface.
  Container e volume descartáveis removidos ao final (`docker compose down
  -v`); nenhum dado de teste ficou para trás.
- **Ajuste final antes do commit (2026-08-22, aprovação do usuário):** a
  ordenação de `list_missions_for_user_by_status` estava em `created_at`
  decrescente (implementação original, nunca formalizada como decisão --
  o ponto 8 da auditoria só pedia "o domínio deve decidir o campo
  correto"). O usuário pediu explicitamente `updated_at DESC, id DESC`:
  pausar/retomar/editar/cancelar devem subir a missão na lista, não só
  criá-la; `id DESC` é o desempate determinístico para `updated_at`
  colidido, necessário para paginação estável por `offset`. Ajustado em
  `app.missions.query.list_missions_for_user_by_status`; teste dedicado
  adicionado (`tests/test_mission_query.py`, assert no SQL compilado).
  Pipeline completo (1414 unitários/90,42%, 73 integração PostgreSQL)
  reexecutado e aprovado após o ajuste.
- **Próxima ação (atualizada):** nenhuma. TASK-092 aprovada pelo usuário
  para commit.

## DEC-074 — Endurecimento da TASK-091: CSRF acoplado a `require_web_session` (não a nenhum router), frontend em TypeScript, whitelist de rotas da SPA, catch-all por qualquer método, empacotamento Docker multi-stage

- **Data:** 2026-08-21/22 (quatro rodadas de revisão do usuário antes do
  commit).
- **Ideia (rodada 1):** antes de aceitar a fundação web (TASK-091) como
  pronta para commit, o usuário pediu uma auditoria de segurança formal
  com 4 pontos bloqueantes: sessão/cookie, CSRF, empacotamento Docker real
  e limpeza de contas de teste. A auditoria confirmou que geração/hash do
  token de `WebSession` (`secrets.token_urlsafe(32)`, 256 bits, só o
  SHA-256 vai pro banco) e os atributos do cookie de sessão (`HttpOnly`,
  `Secure` por ambiente, `SameSite=Lax`, `Path=/`) já estavam corretos
  desde a implementação original; achou e corrigiu 3 lacunas reais: CSRF
  nunca implementado, frontend nunca empacotado na imagem Docker, e uma
  rota de API inexistente caindo incorretamente no catch-all da SPA (200
  `index.html` em vez de 404).
- **Ideia (rodada 2 — 3 correções sobre a rodada 1):** o usuário revisou a
  primeira rodada e não aprovou o commit ainda, apontando 3 problemas
  concretos na própria correção: (a) a decisão de preflight foi React +
  **TypeScript** + Vite, mas a implementação saiu em JavaScript puro
  (`.jsx`/`.js`); (b) CSRF só cobria `POST`/`DELETE` por `Depends` em rota
  individual — `PUT`/`PATCH` de TASKs futuras poderiam nascer sem
  proteção, sem ninguém perceber; (c) o isolamento de `/api/*` usava
  blacklist de prefixos de backend, frágil por construção (uma rota nova
  esquecida na lista vira `200 index.html` silenciosamente) — o usuário
  pediu o inverso, whitelist explícita do que pertence à SPA.
- **Ideia (rodada 3 — escopo do CSRF corrigido de novo):** a rodada 2
  trocou o `Depends` por rota por um middleware ASGI sobre todo
  `POST`/`PUT`/`PATCH`/`DELETE` cujo caminho começasse com `/api/v1/`. O
  usuário apontou que isso era amplo demais: (a) interceptava antes de
  saber se a rota existia, mascarando `404` de rota inexistente como `403
  csrf_invalid`; (b) protegeria erroneamente qualquer endpoint futuro sob
  `/api/v1` mesmo que autenticado por Bearer/service token -- um canal sem
  cookie, sem risco de CSRF, para o qual essa defesa não faz sentido. A
  regra correta é: CSRF protege requisição mutável que depende de
  autenticação automática por cookie da `WebSession` (incluindo o próprio
  login, antes da sessão existir) -- não é uma política global de
  `/api/v1`.
- **CSRF — double-submit cookie girado na fronteira de login, escopado ao
  router da WebSession (não middleware, não `Depends` por rota
  individual):** `require_csrf` (`backend/app/webapp/csrf.py`) é
  registrada **uma única vez**, como dependência do próprio router
  (`app.webapp.router.router = APIRouter(prefix="/api/v1", tags=["webapp"],
  dependencies=[Depends(require_csrf)])`). Esse router É o canal
  WebSession/cookie da aplicação web (login, logout, sessão atual) --
  amarrar a defesa a ele, em vez de a uma string de prefixo de URL,
  resolve as duas lacunas da rodada 2 de uma vez:
  - Rota inexistente nunca chega a nenhum router (FastAPI/Starlette
    resolve `404` antes de qualquer dependência rodar) -- `403` nunca mais
    mascara ausência de rota.
  - Um canal de autenticação diferente (Bearer, service token, webhook do
    Telegram com segredo de header) simplesmente vive em outro router e
    nunca passa por `require_csrf`, mesmo estando montado na mesma
    aplicação.
  - Qualquer endpoint mutável futuro do canal web (edição de missão pela
    SPA, etc.) que for adicionado a este mesmo router (ou a outro que
    também declare a mesma dependência) herda a proteção automaticamente
    -- sem exigir que o desenvolvedor lembre de anotar `Depends(require_csrf)`
    rota por rota; e sem arriscar proteger de mais um canal que não usa
    cookie.
  `require_csrf` ignora `GET`/`HEAD`/`OPTIONS` internamente, já que o
  mesmo router também registra `GET /web-sessions/current`. Cookie
  `aishopping_csrf`, não-`httpOnly` (a SPA precisa ler o valor),
  `Secure`/`SameSite`/`Path` no mesmo padrão do cookie de sessão. Emitido
  de forma anônima (sem sessão) por `register_spa` sempre que a casca
  (`index.html`) é servida e o cliente ainda não tem um — é isso que
  protege o próprio `POST /api/v1/web-sessions` (login) contra CSRF de
  login, já que a SPA sempre carrega a casca antes de qualquer JS rodar.
  `create_web_session` gira o cookie de novo após autenticar (mesmo
  princípio de nunca atravessar uma fronteira de privilégio com um
  identificador reaproveitado, já aplicado à própria `WebSession`).
  Preferido a synchronizer token stateful (exigiria coluna nova em
  `WebSession` e uma consulta a mais por requisição) por já bastar para
  uma SPA same-origin sem introduzir estado adicional no banco.
- **Catch-all da SPA agora casa com qualquer método HTTP, não só `GET`:**
  efeito colateral descoberto ao validar a correção acima contra o
  container real -- com o catch-all registrado só para `GET`, o Starlette
  via o padrão de caminho bater (`/{full_path:path}` casa com qualquer
  string) mas o método não, devolvendo `405 Method Not Allowed` em vez de
  `404` para `PATCH`/`PUT`/`DELETE` numa rota de API inexistente. Não era
  mascaramento de CSRF (o `403` já não acontecia mais depois da correção
  acima), mas também não era o `404` esperado. `register_spa` agora
  registra o catch-all via `app.api_route(..., methods=["GET", "HEAD",
  "POST", "PUT", "PATCH", "DELETE"])` e responde `404` imediatamente para
  qualquer método que não seja `GET`/`HEAD` -- a SPA em si só serve
  navegação `GET`, mas o catch-all precisa "existir" para todos os
  métodos para que o Starlette prefira `404` a `405` quando nenhuma rota
  real casar.
- **Ideia (rodada 4 — CSRF finalmente acoplado à autenticação, não a
  nenhum router):** a rodada 3 amarrou CSRF ao router de `web-sessions`
  (`dependencies=[Depends(require_csrf)]` no `APIRouter`). O usuário
  apontou que isso continuava incompleto pelo motivo oposto ao da rodada
  2: agora era estreito demais -- só protegeria endpoints daquele router
  específico. Um endpoint futuro de missões/ofertas/admin, em outro
  router (o desenho natural conforme a V1.2 crescer), não herdaria nada.
  A regra arquitetural definitiva: **CSRF acompanha a autenticação por
  `WebSession`, não o router nem o domínio funcional onde o endpoint
  mora.**
- **CSRF acoplado à dependência `require_web_session`, não a router
  nenhum:** `app.webapp.dependency` ganhou dois níveis --
  `_resolve_web_session` (interno: só resolve cookie -> hash ->
  `WebSession` -> `User`, `401` se ausente/inválida/expirada/revogada,
  nunca aplica CSRF) e `require_web_session` (pública: depende de
  `_resolve_web_session`, e só para métodos mutáveis chama
  `app.webapp.csrf.validate_csrf`). Qualquer endpoint, de qualquer
  router/módulo, que declare `Depends(require_web_session)` herda
  autenticação por cookie **e** CSRF automaticamente -- a composição da
  árvore de dependências do FastAPI garante a ordem correta sozinha
  (sessão resolvida antes do corpo de `require_web_session` executar,
  então `401` sempre precede `403`, nunca o inverso). O helper interno
  não é exportado para uso fora do módulo -- só a dependência pública, que
  é seguro por padrão.
  `require_admin_web_session` passou a compor sobre `require_web_session`
  (antes compunha sobre o antigo `get_current_web_user`, sem CSRF): um
  endpoint administrativo mutável futuro herda autenticação -> CSRF ->
  autorização ADMIN só por declarar essa dependência, sem implementar nada
  disso de novo.
  `app.webapp.router` deixou de ter `dependencies=[Depends(require_csrf)]`
  a nível de `APIRouter` -- login continua como exceção explícita
  (`Depends(validate_csrf)` só nessa rota, já que ainda não existe
  `WebSession` nesse ponto), logout e a consulta de sessão atual passaram
  a depender de `require_web_session` como qualquer outro endpoint do
  canal web, sem nenhuma configuração específica de router. Efeito
  colateral do logout agora exigir sessão válida via `require_web_session`
  (antes tolerava ausência de cookie como no-op `204`): sem sessão válida,
  a resposta agora é `401 not_authenticated`, nunca mais um sucesso
  silencioso nem `403 csrf_invalid`.
  Prova arquitetural dedicada (`tests/test_webapp_dependency.py`): um
  router propositalmente sem relação nenhuma com `app.webapp.router`
  (simulando missões), com um endpoint usando só
  `Depends(require_web_session)`, exige CSRF do mesmo jeito -- mutação sem
  CSRF -> `403`; com CSRF válido -> chega ao handler. `spa.py` também
  passou a reaproveitar `_resolve_web_session` (em vez de duplicar a
  resolução de sessão) na checagem de `/admin`.
- **Frontend em TypeScript, não JavaScript:** todos os arquivos fonte
  (`.jsx`/`.js` → `.tsx`/`.ts`), `tsconfig.json`/`tsconfig.app.json`/
  `tsconfig.node.json` no padrão do scaffold oficial `react-ts` do Vite,
  `npm run build` agora roda `tsc -b && vite build` (falha o build se
  houver erro de tipo, não só de bundling). Tipos explícitos para o
  usuário da sessão (`WebSessionUser`, `UserRole`), para o cliente HTTP
  (`ApiError`, `request<T>`) e para o contexto de autenticação
  (`AuthContextValue`). Corrige a implementação para bater com a decisão
  de preflight já aprovada — não é uma decisão nova, é a mesma sendo
  cumprida corretamente.
- **Whitelist de rotas da SPA no lugar da blacklist de prefixos de
  backend:** `register_spa` agora testa `full_path` contra
  `_SPA_OWNED_TOP_LEVEL_SEGMENTS = {"", "login", "app", "admin"}`
  (espelha exatamente as rotas de `frontend/src/App.tsx`) — só esses
  caminhos (e seus descendentes via roteamento client-side) viram
  `index.html`; qualquer outro caminho é `404` por padrão, mesmo que
  ninguém tenha atualizado nenhuma lista para incluí-lo. Inverte a
  responsabilidade: antes, uma rota de backend nova exigia lembrar de
  adicioná-la à blacklist para não vazar como `200 index.html`; agora uma
  rota de backend nova simplesmente nunca aparece na whitelist da SPA, e o
  comportamento seguro (`404`) é automático.
- **Fixação de sessão — já coberta pelo desenho original, agora testada
  explicitamente:** `issue_web_session` nunca aceita um identificador
  vindo de fora (não existe parâmetro pra isso na assinatura) e sempre
  revoga toda sessão ativa do usuário antes de emitir a nova. Não havia
  lacuna real; a auditoria adicionou testes que provam isso
  explicitamente (`tests/test_authentication_service_web.py`).
- **Docker — build multi-stage, Node só em build-time:** `Dockerfile`
  movido para a raiz do repositório (antes `backend/Dockerfile`), com um
  estágio `frontend-build` (`node:22-alpine`, `npm ci` + `npm run build`,
  agora incluindo a checagem de tipos TypeScript) cujo `dist/` é copiado
  para o estágio Python final (`COPY --from=frontend-build`). O contexto
  de build dos três serviços que compartilham a imagem (`api`,
  `telegram_notifier`, `collection_worker`) muda de `./backend` para `.`
  (raiz) em `compose.yaml` — nenhuma imagem duplicada, nenhum serviço
  novo. Só o `api` recebe `AISHOPPING_SPA_DIST_DIR=/app/frontend-dist` (só
  ele serve HTTP/SPA); `spa_dist_dir` no `Settings` continua `None` por
  padrão fora do container (resolve o caminho relativo ao checkout
  local). Validado com `docker compose build` real seguido de
  `docker compose up` real contra Postgres containerizado — não só
  `npm run build` no host.
- **Classificação:** Correção/endurecimento de segurança da fundação web
  (TASK-091, item 1 da V1.2) — nenhuma feature de negócio nova, nenhuma
  mudança de escopo além dos pontos pedidos nas quatro rodadas.
- **Justificativa:** cookie de sessão por si só nunca é suficiente contra
  CSRF em nenhuma aplicação autenticada por cookie; o usuário explicitou
  isso como bloqueador antes de aceitar a fundação. O empacotamento Docker
  do frontend não podia ficar para "quando a aplicação for implantada" —
  sem ele a arquitetura aprovada (SPA same-origin servida pelo mesmo
  backend) simplesmente não existe fora do ambiente de desenvolvimento
  local.
- **Próxima ação:** nenhuma — TASK-091 aguardando aprovação do usuário
  para commit com o endurecimento aplicado.

## DEC-073 — Esclarecer três pontos da reorganização da V1.2 (`DEC-072`): DEV/ADMIN exclusivo sem multi-papel, avaliações sempre por origem, `PriceObservation` redundante é semântica

- **Data:** 2026-08-21.
- **Ideia:** o usuário aprovou a direção da `DEC-072` e pediu três ajustes
  documentais pontuais, antes do commit, para evitar ambiguidade
  arquitetural futura — sem mudar a ordem nem o conteúdo de nenhum dos 16
  itens da V1.2.
- **Ajuste 1 (DEV/ADMIN exclusivo, não multi-papel):** a divisão USER x
  DEV/ADMIN da V1.2 (item 1 de `docs/internal/v1.2-scope.md`) continua sendo
  só uma fronteira de rotas (`/app` x `/admin`) sobre a autorização já
  existente (`app.authorization`, papel único `USER ⊂ ADMIN ⊂ DEV`,
  `DEC-034`) — nunca uma reformulação dela. Explicitado que a V1.2 **não**
  introduz `user_roles`, múltiplos papéis simultâneos, hierarquia complexa
  de roles, RBAC avançado nem planos FREE/PLUS/PRO; isso permanece
  integralmente na V2 (`docs/internal/backlog.md`, "Papéis e planos da V2").
  A V1.2 só precisa de autorização suficiente para garantir que USER nunca
  acesse recursos administrativos e que DEV/ADMIN acesse a área
  administrativa exclusiva do desenvolvedor/administrador atual.
- **Ajuste 2 (avaliações sempre por origem):** reforçado no item 5 que
  `rating_average`/`review_count` nunca representam uma avaliação global do
  produto — pertencem sempre à loja/origem da oferta (ex.: Amazon ⭐4,8,
  KaBuM! ⭐4,9, Pichau ⭐4,7, nunca um `RTX 5070 Ti ⭐4,8` único), salvo regra
  de agregação explicitamente aprovada no futuro. A modelagem definitiva
  (colunas, tabela nova ou reaproveitada) permanece decidida só quando a
  TASK for aberta — nenhuma entidade nova inventada agora.
- **Ajuste 3 (`PriceObservation` redundante é semântica, não só preço):**
  corrigida a redação do item 3 para não sugerir "preço igual = não grava
  observação" — critério simplista demais. A regra registrada é evitar
  observação **semanticamente redundante**: uma `PriceObservation` nova é
  necessária quando qualquer parte do estado relevante mudar (preço,
  disponibilidade, moeda, vendedor/fulfillment relevante, condição
  comercial relevante, ou outro estado que o domínio considerar histórico),
  não só o preço isoladamente. Exemplo registrado: mesmo preço com
  disponibilidade diferente (`AVAILABLE` → `UNAVAILABLE`) **não** é
  redundante. A lista definitiva de campos comparados continua dependendo
  de auditar `backend/app/collection/models.py` na própria TASK.
- **Classificação:** Versão futura (esclarecimento documental da `DEC-072`
  — `docs/internal/v1.2-scope.md`; nenhuma TASK criada, nenhuma
  implementação, migration, frontend, endpoint, commit de código, push ou
  redeploy).
- **Justificativa:** os três pontos eram fonte real de ambiguidade
  arquitetural para quando cada item virar TASK — sem o esclarecimento,
  "DEV/ADMIN" poderia ser mal interpretado como um convite a desenhar RBAC
  completo agora, "avaliações" poderia levar a uma nota global inventada
  sem aprovação, e "reduzir `PriceObservation` redundante" poderia virar
  uma deduplicação ingênua por preço que perderia mudanças reais de
  disponibilidade/condição comercial no histórico.
- **Próxima ação:** nenhuma além da documentação já ajustada. Revisão de
  consistência entre `docs/internal/v1.2-scope.md`, `docs/internal/backlog.md`,
  `docs/internal/roadmap.md`, `docs/internal/project-context.md` e este log
  feita nesta mesma rodada, sem alterar nenhum bloco histórico/datado
  anterior.

## DEC-072 — Reorganizar a V1.2 em torno de uma aplicação web completa (USER/DEV-ADMIN); Telegram passa a canal de alertas; itens de e-mail movidos para V2

- **Data:** 2026-08-21.
- **Ideia:** o usuário decidiu reorganizar oficialmente o roadmap: a V1.2
  deixa de ser uma lista solta de evoluções incrementais sobre o Telegram e
  passa a ter como objetivo central transformar o AIShoppingAgent numa
  plataforma web completa de monitoramento e comparação de preços. A
  aplicação web se torna o núcleo da experiência, com duas áreas
  conceituais (`/app/...` para USER, `/admin/...` para DEV/ADMIN) — mesma
  aplicação, mesmo backend, mesmo banco, autorização por papel real no
  backend (nunca só escondida na interface). O Telegram continua existindo
  e continua controlando as mesmas missões, mas sua função central passa a
  ser alertar rapidamente o usuário, deixando de precisar carregar sozinho
  toda a experiência do produto.
- **Classificação:** Versão futura (reorganização de escopo da V1.2 e do
  backlog da V2 — `docs/internal/v1.2-scope.md`, `docs/internal/backlog.md`,
  `docs/internal/roadmap.md`, `docs/internal/project-context.md`; nenhuma
  TASK criada, nenhuma implementação, migration, frontend, endpoint, commit,
  push ou redeploy nesta rodada — só documentação).
- **Justificativa:** o Telegram, como único canal, satura rápido para
  apresentar dado rico (páginas de produto, gráficos de histórico,
  comparação entre lojas, avaliações, dashboard técnico) — a interface de
  chat não é o formato certo para essas necessidades já registradas como
  ideias soltas no backlog (dashboard web, analytics, painel administrativo
  dividido em quatro itens incrementais). Consolidar tudo isso numa única
  aplicação web, com o Telegram reposicionado como canal de alerta,
  resolve a fragmentação sem duplicar o domínio (missões, ofertas,
  histórico, coleta continuam sendo os mesmos, servidos por uma segunda
  interface). A separação USER/DEV-ADMIN pela mesma aplicação (em vez de
  dois sistemas) evita duplicar autenticação/autorização/sessão e mantém a
  matriz de permissão fail-closed já existente (`app.authorization`,
  `DEC-034`) como única fonte de verdade, sem depender da interface para
  esconder recursos administrativos.
- **Itens preservados, renumerados dentro da nova ordem da V1.2** (nenhuma
  ideia já aprovada foi descartada): redução de `PriceObservation`
  redundante (`DEC-053`), Magalu como quinta loja (`DEC-054`), comparação
  de menor preço histórico externo/interno estilo Steam Inventory Helper
  (`DEC-056`), pesquisa de ofertas em lives — YouTube e Shopee Live
  (`DEC-056`), pesquisa de cupons e consulta autenticada de frete/
  parcelamento restrita a DEV/ADMIN (`DEC-045`). O painel administrativo,
  antes desmembrado em quatro itens incrementais e ainda tratado como
  conceito à parte, passa a ser simplesmente a área `/admin` da mesma
  aplicação web — mesma ideia, sem retrabalho, só sem mais precisar de uma
  seção própria separada da V1.2.
- **Itens removidos da V1.2, movidos para V2** (nenhum descartado, só
  adiado): opt-in de notificação por e-mail no cadastro e notificações por
  e-mail de fato — ambos exigiam a V1.2 original pedir e-mail no `/cadastro`
  antes de qualquer entrega de valor por e-mail; com o novo núcleo da V1.2
  sendo a aplicação web (que não depende de e-mail para existir), o usuário
  decidiu adiar toda a capability de e-mail inteira para a V2, unificada com
  a confirmação/verificação de e-mail que já estava lá.
- **Princípios arquiteturais registrados para a V1.2** (a valer quando cada
  item virar TASK): (1) web, Telegram e futuros clientes usam o mesmo
  domínio/backend sempre que possível; (2) nunca duplicar regras de missão
  entre Telegram e web; (3) nunca duplicar `Offer`/`PriceObservation` numa
  tabela de "anúncio" só para apresentação — a página de produto é
  construída sobre os dados reais já existentes; (4) dados financeiros vêm
  sempre dos collectors/banco, nunca da IA; (5) gráficos e métricas
  históricas (menor/maior preço do período, média, variação percentual) são
  determinísticos, calculados localmente pelo backend/PostgreSQL, nunca por
  IA; (6) avaliações (`rating_average`/`review_count`) permanecem vinculadas
  à loja de origem, nunca misturadas numa nota global sem decisão explícita
  futura; (7) USER e DEV/ADMIN têm autorização real checada no backend,
  nunca só escondida na interface; (8) operações administrativas perigosas
  (ex.: restart de PostgreSQL) recebem proteção maior que operações de
  menor risco (ex.: restart de um worker); (9) qualquer recurso que aumente
  muito o scraping (ex.: avaliações, histórico externo) é avaliado
  criticamente antes de implementar — por isso a V1.2 começa só com
  `rating_average`/`review_count`, sem coletar texto de review individual;
  (10) cada um dos 16 itens da nova V1.2 só vira TASK quando o usuário pedir
  explicitamente — esta reorganização não abre nenhuma TASK sozinha.
- **Próxima ação:** nenhuma. Documentação atualizada
  (`docs/internal/v1.2-scope.md`, `docs/internal/backlog.md`,
  `docs/internal/roadmap.md`, `docs/internal/project-context.md`); aguardar
  revisão do usuário e só então, se solicitado, abrir a primeira TASK (item
  1, fundação da aplicação web) pelo workflow oficial (`AGENTS.md`).

## DEC-071 — Comandos manuais de missão reaproveitam a seleção múltipla da TASK-085; edição encadeia direto no menu após pausar

- **Data:** 2026-08-21.
- **Classificação:** Correção de bug real (pausa sem retomada) e extensão
  de UX determinística, sem introduzir IA em nenhum ponto novo.
- **Contexto:** usuário reportou que editar uma missão `ACTIVE` a deixava
  `PAUSED` sem nenhuma forma de voltar a `ACTIVE`, que não havia comando
  manual dedicado para pausar/retomar, e que `/cancelar_missao` só
  cancelava uma missão por vez.
- **Decisão (reuso de infraestrutura):** `/pausar` e `/retomar` (novos) e
  `/cancelar_missao` (reescrito) passam a compartilhar o mesmo caminho
  local determinístico, reaproveitando integralmente
  `stage_mission_command`/`stage_mission_command_choice`/
  `parse_multi_numbered_choice` já construídos pela TASK-085 — até esta
  TASK, essa infraestrutura só era alcançada pelo caminho ambíguo via IA.
  Nenhuma máquina de estados nova foi criada; o código próprio antigo de
  seleção única de `/cancelar_missao` foi removido, não duplicado.
- **Decisão (UX de `/editar_missao`):** pausar uma missão `ACTIVE` para
  poder editá-la (pré-condição já existente do domínio) deixou de
  terminar numa mensagem pedindo para reenviar `/editar_missao` — agora
  encadeia diretamente no menu de edição na mesma resposta. Um campo
  novo, `auto_paused`, propagado por todo o `pending_intent` da edição,
  distingue essa origem de uma missão que já estava `PAUSED` antes,
  apenas para variar o texto final; nenhum dos dois casos retoma a
  missão sozinho. É uma "solução mínima coerente" (só mais um campo no
  JSON existente), não uma máquina de estados nova.
- **Garantia mantida:** os três comandos manuais e a edição continuam
  100% determinísticos — comprovado por teste com adapter poison-pill
  (`AssertionError` se a IA for chamada) e `assert adapter.calls == []`
  explícito.
- **Fora de escopo, mantido de propósito:** alerta de preço-alvo
  notificando repetidamente sem queda real e busca sem correspondência
  (`iphone 16 512` vs. oferta real da Amazon) — `alerts/evaluator.py` e
  `collection/model_matching.py` não foram tocados. Ver
  `docs/tasks/TASK-090.md`.

## DEC-070 — Desativar Terabyte temporariamente e simplificar parcelamento para só-card

- **Data:** 2026-08-20.
- **Classificação:** Reação operacional a bloqueio externo confirmado
  (Cloudflare Bot Management), reduzindo o escopo da TASK-089 só para a
  Terabyte.
- **O que mudou:** diagnóstico dedicado (sem tentativa de evasão)
  confirmou `server: cloudflare`, `cf-mitigated: challenge`, cookie
  `__cf_bm` em homepage/busca/produto igualmente; Chrome comum, mesmo
  IP do servidor, carrega o site normalmente enquanto o Playwright do
  coletor recebe 403 -- aponta para característica do navegador
  automatizado, não do IP isolado. Volume de requisições subiu 2-7x
  entre 17-19/08 (mesma janela em que a TASK-089 passou a abrir até 3
  páginas individuais por busca para parcelamento detalhado), mas o
  bloqueio só começou em 20/08 05:00, mais de 2 dias depois -- fator
  agravante possível, não causa direta comprovada.
- **Decisão:** (1) `stores.is_active=false` para Terabyte -- mecanismo já
  existente, reversível, não apaga histórico/missões/`mission_sources`;
  (2) `TerabyteProvider.resolve_installment_options` removido -- a
  Terabyte não abre mais página individual só para a tabela detalhada de
  parcelamento (1x-18x); o parcelamento passa a vir só do card da busca,
  mesmo caminho já usado por Amazon/KaBuM!, sempre `is_highlighted=true`.
  Reduz de até 4 navegações (1 busca + até 3 páginas) para 1 por
  execução.
- **Fora de escopo, mantido de propósito:** nenhuma técnica de evasão
  (stealth, spoof de fingerprint, proxy, rotação de IP, CAPTCHA solver,
  cookies humanos, login) foi considerada ou implementada. Pichau, Amazon
  e KaBuM! não foram alterados -- Pichau mantém o enriquecimento
  individual completo (não apresentou o problema); Amazon/KaBuM! já
  usavam só o card.
- **V2 registrada:** investigação detalhada de faixas de parcelamento da
  Terabyte (1x-18x) fica para quando houver solução ao bloqueio que não
  envolva evasão (ex.: parceria/API oficial) -- decisão de produto, fora
  do escopo técnico. Ver `docs/tasks/TASK-089.md`, seção "Terabyte
  desativada e simplificada".
- **Atualização (TASK-105, 2026-08-22):** o diagnóstico acima permanece
  válido -- o bloqueio identificado em 20/08 foi real e não foi contornado.
  O que mudou é o transporte: diagnóstico repetido no DEV confirmou que o
  mesmo Chromium gerenciado pelo Playwright continua bloqueado, mas um
  Edge normal via CDP loopback (mesmo padrão já em produção para a
  Magalu, sem stealth/spoof/proxy) passa limpo -- busca real, 300 cards,
  `TerabyteProvider.extract()` e `resolve_product_availability` atuais
  (sem nenhuma alteração de parser) funcionaram sem bloqueio, inclusive
  em página individual. `TerabyteProvider` passou a usar
  `CdpPageFallback` (mesma infraestrutura CDP/Edge supervisionado da
  Magalu, sem supervisor/porta próprios) como transporte primário e
  único -- sem fallback de volta ao Playwright, que continua
  comprovadamente bloqueado. Ver `docs/tasks/TASK-105.md`.
- **Fechamento (TASK-105, 2026-08-22):** a desativação temporária terminou.
  Migration `20260822_0009_reactivate_terabyte.py` marca
  `stores.is_active=true` para `code='terabyte'` -- mesmo mecanismo
  reversível já usado para desativar (`downgrade()` restaura `false`),
  agora automatizado por Alembic em vez de `UPDATE` manual: a Terabyte
  sobe ativa em qualquer ambiente que rode `alembic upgrade head` a
  partir desta revisão, sem intervenção manual em produção. A variável de
  configuração também deixou de ser exclusiva da Magalu --
  `Settings.magalu_cdp_url` foi renomeada para `Settings.edge_cdp_url`
  (nome antigo/env `AISHOPPING_MAGALU_CDP_URL` continua aceito por
  compatibilidade), já que o mesmo Edge/CDP supervisionado agora atende
  Magalu, Mercado Livre e Terabyte.

## DEC-069 — Corrigir a modelagem de parcelamento da TASK-089 para relação 1:N

- **Data:** 2026-08-17.
- **Classificação:** Correção arquitetural da TASK-089/DEC-068, antes de
  qualquer código ter sido consolidado (a investigação real de campo mudou
  o entendimento do problema).
- **O que mudou:** a investigação real nas quatro lojas (Pichau, Terabyte,
  Amazon, KaBuM!) mostrou que uma oferta pode ter **várias** condições de
  parcelamento simultâneas, não uma só. Pichau expõe 1x-6x com desconto
  (percentual variável por produto, nunca fixo) mais um "12x sem juros"
  padrão com total explícito; Terabyte expõe uma faixa completa 1x-18x com
  desconto decrescente nas primeiras parcelas, "sem juros" nas
  intermediárias e **juros reais** a partir de certa quantidade. O desenho
  original de DEC-068 (`installment_total_amount`/`installment_count`/
  `installment_amount` escalares direto em `Offer`/`PriceObservation`)
  representa só UMA condição — insuficiente e, se implementado, teria
  descartado a maior parte da evidência real encontrada.
- **Decisão arquitetural:** os três campos escalares são substituídos por
  uma entidade dedicada, `OfferInstallmentOption`, em relação 1:N —
  vinculada a `PriceObservation.id` (não a `Offer.id` direto), pelo mesmo
  motivo histórico/append-only que já rege `PriceObservation`: o "estado
  atual" das opções é sempre o da observação mais recente da oferta,
  nunca por UPDATE/DELETE/flag. Amazon e KaBuM! continuam gerando no
  máximo uma opção por oferta (só o que o card mostra); Pichau e Terabyte
  podem gerar várias.
- **Guardrails que continuam valendo, agora por opção:** nunca calcular
  `installment_total_amount` a partir de `count * amount`; nunca inferir
  desconto, juros, ou usar preço riscado/"De:" como total; percentuais de
  desconto lidos sempre do texto atual da página, nunca fixados no código
  (a investigação encontrou percentuais diferentes até no mesmo produto
  Pichau, dependendo de ter ou não o selo promocional "Desconto em Até
  Nx"); ausência de parcelamento nunca invalida a oferta.
- **Sem migration destrutiva:** como nenhum código da DEC-068 havia sido
  consolidado ainda, a migration criada (`20260817_0001`) já nasce com o
  modelo 1:N — não existiu uma migration anterior de 3 campos para
  reverter.
- **Fora de escopo mantido (rodada de modelo):** nenhuma alteração em
  mensagens do Telegram/apresentação nesta rodada — só investigação,
  modelo, migration, contratos, providers e persistência. Ver
  `docs/tasks/TASK-089.md`.
- **Atualização (2026-08-17, rodada de apresentação):** o escopo acima
  foi retomado na mesma TASK-089 — alertas e pré-lista agora mostram
  `💰 À vista`/`💳 Parcelado` dinamicamente, com `is_highlighted` extra
  em `OfferInstallmentOption` para saber qual opção a loja destacou no
  card. Interpretação de "quero em Nx" pelo usuário e qualquer alteração
  no `IntentInterpreter`/fluxo de compra foram explicitamente adiadas
  para uma V2 — ver seção "V2" em `docs/tasks/TASK-089.md`.

## DEC-068 — Separar preço à vista e parcelado da classificação de vendedor

- **Data:** 2026-08-16
- **Classificação:** Nova TASK do MVP, formalizada como TASK-089.
- **Ideia:** coletar e apresentar, quando publicamente disponíveis no card ou
  página já usada pelo provider, preço à vista e preço parcelado, incluindo
  quantidade e valor das parcelas quando houver evidência explícita.
- **Justificativa:** o modelo atual possui um único `PriceObservation.amount`;
  a mudança atravessa os quatro providers, contrato bruto, normalização,
  persistência histórica, migrations, comparação/alertas e Telegram. Acoplá-la
  à TASK-077 impediria que a classificação de vendedor fosse entregue e
  validada isoladamente.
- **Guardrails:** não inferir parcelamento; não substituir silenciosamente a
  semântica vigente de `amount`; preservar histórico; distinguir ausência de
  informação de preço não aplicável; investigar evidência real por loja antes
  de congelar seletores.
- **Decisão arquitetural:** `PriceObservation.amount` permanece o preço à vista
  e a única base de preço-alvo, queda e ranking. Total parcelado, quantidade de
  parcelas e valor da parcela serão campos nullable separados na observação
  histórica; nenhum deles será calculado a partir dos demais. Não haverá
  backfill inferido para dados existentes.
- **Apresentação aprovada:** quando todos os dados forem explícitos,
  `💰 À vista: ...` e `💳 Parcelado: Nx de ... — total ...`; ausência ou
  evidência parcial nunca produz cálculo ou valor fictício.
- **Separação:** TASK-077 trata vendedor/entrega em Amazon e Kabum; TASK-089
  trata modalidades de preço nas quatro fontes. São independentes, mas devem
  ser executadas sequencialmente por compartilharem providers, persistência e
  Telegram.
- **Próxima ação:** manter TASK-089 planejada e não iniciada até pedido
  explícito do usuário.

## DEC-067 — Generalizar a evidência de vendedor/entrega para Kabum

- **Data:** 2026-08-16
- **Classificação:** Implementar agora, como refinamento da TASK-077.
- **Decisão:** além de distinguir Amazon própria de parceiro, a TASK-077 deve
  comprovar, persistir e disponibilizar na apresentação a condição
  vendido/entregue pela própria Kabum. O filtro `kabum_product=true` já limita
  a busca, mas não substitui evidência real dos cards nem persistência
  histórica da classificação.
- **Guardrails:** investigar Amazon e Kabum ao vivo antes do código; não assumir
  que as duas lojas expõem os mesmos campos; não tratar ausência como vendedor
  oficial; não alterar ranking; não ativar `Seller`/`Offer.seller_id`; revisar o
  enum proposto para não codificar `AMAZON` como conceito genérico.
- **Resultado do gate:** 48 cards Amazon e 24 cards Kabum foram inspecionados
  em buscas reais isoladas. Nenhum expôs vendedor ou responsável pela entrega;
  o seletor Amazon existente retornou `null` em todos.
- **Decisão posterior aprovada:** consultar somente páginas individuais dos
  candidatos finais, sequencialmente, sem retry e com limite três. Evidência
  real confirmou o bloco combinado `Enviado / Vendido` da Amazon com
  `Amazon.com.br` ou parceiro, e `Vendido e entregue por: KaBuM!` na KaBuM!.
  Persistir `seller_kind` e `fulfillment_kind` com enum genérico
  `platform`/`marketplace_partner`/`unknown`; `NULL` continua significando não
  avaliado. 401/403/429 interrompe o lote para não insistir contra anti-bot.

## DEC-066 — Mídia por oferta, redirect próprio e checkpoint por parte

- **Data:** 2026-08-16
- **Classificação:** Implementar agora, pela TASK-084 já planejada.
- **Decisão:** imagem nullable pertence a `Offer`; short link público e sem
  expiração mapeia token opaco único para `offer_id`; redirect valida esquema
  e compatibilidade com o host da loja; entrega Telegram mantém checkpoint por
  consumidor/evento/oferta/parte e retoma apenas partes ainda não confirmadas.
- **Evidência:** inspeção real isolada comprovou imagens nos cards das quatro
  lojas, com seletores e CDNs específicos registrados em `TASK-084.md`.
- **Guardrails:** nenhuma URL arbitrária no redirect; ausência de imagem não
  elimina oferta; falha de mídia cai para texto; ambiguidade não vira sucesso;
  nenhuma mudança em ranking, preço, frete ou classificação.


## DEC-065 — Listagem determinística de missões e menu Telegram sincronizado

- **Data:** 2026-08-16
- **Classificação:** Nova TASK do MVP.
- **Decisão:** criar a TASK-088 para adicionar `/listar_missoes`, restrito ao
  proprietário autenticado, sem IA, exibindo somente missões `active`,
  `paused` e `cancelled`; publicar o menu nativo pelo `setMyCommands` oficial.
- **Justificativa:** `/ajuda` já descreve os comandos atuais, mas o menu do
  Telegram não foi reaplicado depois do deploy. A consulta semântica por IA já
  lista missões, porém não substitui um comando explícito, previsível e barato.
- **Guardrails:** nenhuma transição, agenda, coleta, migration ou mudança de IA;
  `completed` e `expired` não entram na nova listagem.
- **Refinamento aprovado:** **Implementar agora** na própria TASK-088. A
  listagem agrupa `active` antes de `paused` e `cancelled`, mantendo as mais
  recentes primeiro em cada grupo, e apresenta os ícones oficiais
  `🟢 ativa`, `⏸️ pausada` e `❌ cancelada` junto ao status.


## DEC-064 — Revisão transversal de UX/copy como TASK própria

- **Data:** 2026-08-16
- **Classificação:** Nova TASK do MVP.
- **Decisão:** registrar a revisão aprovada dos textos visíveis como TASK-087,
  separada das TASKs funcionais, antes de alterar código.
- **Guardrail:** somente copy, layout textual e testes correspondentes; nenhum
  comando, parser, estado, TTL, autorização, regra funcional ou integração muda.
- **Resultado:** TASK-087 concluída com o catálogo aplicado, sem mudança de
  comportamento; suíte focada e não-integração aprovadas.

## DEC-063 — Diagnóstico local e seguro das falhas de coleta

- **Data:** 2026-08-16
- **Decisão:** enriquecer exclusivamente `collection_source_failed`, com
  traceback padrão limitado e mensagem somente para exceções de domínio
  consideradas seguras.
- **Motivo:** preservar diagnóstico sem ampliar o comportamento do formatter
  global nem expor texto bruto de bibliotecas externas.

## DEC-062 — Checks de Enum explícitos na metadata

- **Data:** 2026-08-16
- **Decisão:** representar `mission_command_values`,
  `store_source_type_values` e `user_role_values` como `CheckConstraint`
  explícitas e desativar a geração automática pelos respectivos `Enum`.
- **Motivo:** Alembic 1.19.1 ignora checks `_type_bound` na metadata, mas
  compara os mesmos checks refletidos do PostgreSQL, produzindo falso drift.
- **Compatibilidade:** nomes e expressões permanecem idênticos; nenhuma
  migration nem alteração no banco existente é necessária.

Este arquivo registra decisões arquiteturais e funcionais tomadas durante o desenvolvimento. Ele preserva o motivo de cada escolha e direciona a atualização documental necessária, sem substituir os ADRs para decisões arquiteturais formais.

## Processo obrigatório para novas funcionalidades

Antes de implementar qualquer funcionalidade sugerida, analisar seu impacto no escopo, nas dependências, na arquitetura, nos dados, na operação e na complexidade do projeto. A ideia deve receber exatamente uma das classificações abaixo:

- Implementar agora
- Nova TASK do MVP
- Backlog
- Versão futura
- Out of Scope
- Rejeitada

Após a classificação, registrar a decisão neste arquivo e atualizar a documentação correspondente. A classificação não autoriza implementação fora de uma TASK explicitamente solicitada.

## Formato de registro

### DEC-AAA — Título da decisão

- **Data:** AAAA-MM-DD
- **Ideia:** descrição objetiva da proposta ou decisão.
- **Classificação:** uma das seis classificações permitidas.
- **Justificativa:** impacto avaliado e motivo da classificação.
- **Próxima ação:** documento a atualizar, TASK a criar quando aplicável, ou ação de não implementação.

### DEC-060 — Ampliar o escopo da `v1.0.2` com dois itens depois da TASK-069

- **Data:** 2026-08-10
- **Ideia:** depois de aprovar a publicação da TASK-069 (que concluiu os 5
  itens do planejamento original da `v1.0.2`), o usuário pediu para
  registrar mais dois itens na mesma versão, antes do push: (1) impedir
  `/cadastro` para um usuário já autenticado/logado; (2) quando uma
  missão for criada sem nenhuma loja informada, perguntar as lojas por
  lista numerada (`1 Pichau`, `2 Terabyte`, `3 Amazon`, `4 Kabum`,
  `5 Todas`). Instrução explícita: não implementar agora, não abrir TASK
  automaticamente — só garantir que a documentação não afirme a `v1.0.2`
  inteira como concluída.
- **Classificação:** Nova TASK do MVP (dois itens registrados, escopo
  aberto — mesma classificação usada para os itens 3/4/5 adicionados por
  `DEC-057`/`DEC-055`/`DEC-058`).
- **Justificativa técnica:** mesma decisão de organização já usada nesta
  versão — o usuário prefere registrar pedidos pontuais na `v1.0.2` (release
  corretiva já em andamento) a abrir um novo documento de versão só para
  dois itens. Nenhum dos dois é infraestrutura/configuração pura, mas
  ambos já têm precedente estrutural direto: `/cadastro` já sabe detectar
  sessão ativa (`has_active_session`, TASK-046/061) e a lista numerada de
  lojas já existe em `favorite_stores`/`preferred_categories`
  (TASK-067) — nenhum dos dois exige mecanismo novo do zero.
- **Próxima ação:** itens 6 e 7 registrados em `docs/internal/v1.0.2-scope.md`; status da
  versão corrigido em todos os documentos que a citavam como "concluída"
  (`docs/internal/v1.0.2-scope.md`, `docs/internal/roadmap.md`, `docs/tasks/README.md`,
  `docs/internal/project-context.md`, `AGENTS.md`, `docs/releases/changelog.md`,
  `docs/tasks/TASK-069.md`) para deixar claro que só o planejamento
  *original* de 5 itens está concluído — a `v1.0.2` continua aberta.
  Nenhuma TASK criada para os dois itens novos; nenhuma implementação
  realizada.

### DEC-059 — Separar `v1.0.2` de V1.2 em documentos distintos

- **Data:** 2026-08-10
- **Ideia:** o usuário notou que `docs/internal/v1.2-scope.md` continha a seção da
  `v1.0.2` dentro de um arquivo cujo título e propósito declarado são só
  sobre V1.2 — mistura estrutural entre duas versões distintas (`v1.0.2`
  é release *patch* dentro da V1; V1.2 é fase funcional maior, numerada à
  parte), mesmo com as seções fisicamente separadas dentro do arquivo.
- **Classificação:** Implementar agora (correção de organização
  documental, sem mudança de conteúdo/decisão nenhuma — só o arquivo onde
  cada uma vive).
- **Justificativa técnica:** manter as duas em arquivos separados evita
  confundir as versões e deixa cada documento com um título e propósito
  únicos, sem exigir leitura de seções internas para saber a qual versão
  um item pertence.
- **Próxima ação:** conteúdo da `v1.0.2` movido para `docs/internal/v1.0.2-scope.md`
  (novo arquivo); `docs/internal/v1.2-scope.md` passa a conter só a V1.2 de verdade.
  Todas as referências cruzadas em `AGENTS.md`, `docs/internal/roadmap.md`,
  `docs/releases/changelog.md`, `docs/tasks/README.md`, `docs/internal/handoff-v1.0.2.md` e
  nas próprias entradas deste log (`DEC-052`, `DEC-055`, `DEC-057`)
  atualizadas para apontar para o arquivo certo.

### DEC-058 — Registrar pré-lista de preços sem IA como item 5 da `v1.0.2`

- **Data:** 2026-08-10
- **Ideia:** o usuário decidiu dividir a ideia original de "pré-lista de
  preços encontrados" (levantada durante a discussão do item de menor
  preço histórico) em duas fases: uma primeira versão simples, sem IA,
  mostrando só 1 preço por loja selecionada, na `v1.0.2`; e a versão com
  IA (julgamento de "vale a pena", comparação com histórico
  externo/interno) permanece só na V1.2, como já estava registrado no
  item 11 (`DEC-056`) — nada do que já tinha sido combinado para a V1.2
  foi removido ou reduzido, só ganhou uma fase anterior mais simples.
- **Classificação:** Versão futura (item 5 da `v1.0.2`, `docs/internal/v1.0.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora). Mesma nota
  dos itens 3 e 4 dessa release: é funcionalidade nova, não infra,
  registrada em `v1.0.2` por decisão explícita do usuário.
- **Justificativa técnica:** hoje o usuário só recebe alerta quando o
  preço cai ou atinge o alvo (`app/alerts/evaluator.py`) — nenhuma
  mensagem confirma que a missão está rodando nem mostra o que já foi
  encontrado. A versão sem IA é puramente informativa (apresenta dado já
  coletado, sem julgamento), preparando o terreno pra fase com IA da V1.2
  sem depender dela. Gatilho exato, formato da mensagem e template não
  decididos agora — ficam para a TASK.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` atualizado com o item 5; item 11 de
  `docs/internal/v1.2-scope.md` (`DEC-056`) atualizado só para referenciar essa fase
  anterior, sem alterar seu próprio conteúdo. Nenhuma TASK criada;
  implementação aguarda solicitação explícita futura.

### DEC-057 — Registrar edição de missão existente como item 3 da `v1.0.2`

- **Data:** 2026-08-10
- **Ideia:** o usuário perguntou se dá para editar uma missão já criada
  (trocar lojas ou preço-alvo sem recriar). Auditoria confirmou em
  `backend/app/missions/models.py`/`service.py`: `MissionCommand` só cobre
  transições de ciclo de vida (`activate`/`pause`/`resume`/`complete`/
  `cancel`/`expire`); não existe nenhum comando para alterar
  `MissionCriteria.target_amount`/`target_currency` nem as fontes
  selecionadas (`MissionSource`) — hoje só criando uma missão nova. O
  usuário pediu para registrar essa capacidade especificamente na
  `v1.0.2`, não na V1.2. Posicionada como item 3 (antes do item de
  categorias, `DEC-055`), por pedido explícito do usuário.
- **Classificação:** Versão futura (item 3 da `v1.0.2`, `docs/internal/v1.0.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora). Registrada
  em `v1.0.2` por decisão explícita do usuário, embora seja
  funcionalidade nova de verdade — mais distante do escopo original de
  "configuração/infraestrutura, sem funcionalidade nova" da `v1.0.2`
  (`DEC-052`) do que qualquer item anterior dessa mesma release (inclusive
  o item de categorias, `DEC-055`, que já era só um ajuste de UX).
  Diferença sinalizada explicitamente no `docs/internal/v1.0.2-scope.md` para não
  confundir escopo nem apagar o registro da decisão original.
- **Justificativa técnica:** o mecanismo exato (novo `MissionCommand`,
  fluxo de confirmação no Telegram, efeito sobre `mission_schedules` e
  sobre o histórico já coletado ao trocar fontes) não foi decidido —
  fica para quando a TASK for desenhada.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` atualizado com o item 3 da `v1.0.2`
  (arquivo separado de `docs/internal/v1.2-scope.md`, para não confundir as duas
  versões). Nenhuma TASK criada; implementação aguarda solicitação
  explícita futura.

### DEC-056 — Registrar comparação de menor preço histórico externo (itens 11 e 12 da V1.2)

> **Atualização (`DEC-080`/`DEC-082`, 2026-08-22):** o histórico externo
> permanece na V1.2, agora como item 17. A pesquisa de ofertas em lives foi
> movida para a V2. O texto abaixo preserva o contexto histórico original.

- **Data:** 2026-08-10
- **Ideia:** o usuário pediu inicialmente um "pré-aviso" de valores
  encontrados e perguntou se a IA já analisa preços comparando com
  histórico — resposta confirmada por auditoria de código
  (`backend/app/alerts/evaluator.py`, `backend/app/purchase/`): nenhum dos
  dois módulos importa `AIProviderManager`; a decisão de alerta hoje é
  100% determinística (queda vs. observação anterior; alvo definido pelo
  usuário). A partir dessa resposta, o usuário ampliou o pedido para uma
  capacidade nos moldes do Steam Inventory Helper: mostrar preço atual,
  menor preço histórico **externo** (independente do ano, com fonte/URL/
  data verificáveis), menor preço histórico **interno** (já existe em
  `price_observations`), e comparação percentual entre os três, com regra
  explícita e sem exceção de que a IA nunca inventa preço/data/loja/fonte/
  URL — só interpreta fatos já encontrados por pesquisa externa real ou
  pelo próprio banco. Pediu também validação de identidade de produto
  antes de comparar (mesmo exemplo já validado em produção pela TASK-063:
  "Logitech G PRO 2" ≠ "Logitech G Pro X Superlight 2") e um template de
  apresentação fixo (fornecido por ele). Separadamente, pediu para
  registrar também pesquisa de ofertas anunciadas em lives — por enquanto
  só YouTube e Shopee Live —, ainda sem nenhum registro anterior no
  projeto.
- **Classificação:** Versão futura (itens 11 e 12 da V1.2, `docs/internal/v1.2-scope.md`;
  nenhuma TASK criada, nenhuma API/motor de busca escolhido, nenhuma
  implementação autorizada agora).
- **Justificativa técnica:** a regra "IA nunca inventa dado factual, só
  interpreta" já é o princípio em produção desde a TASK-063
  (`app.collection.relevance`) e o padrão de template fixo no código já é
  usado por `app/telegram/formatting.py` — o item 11 estende os dois
  padrões já aprovados para uma nova capacidade (pesquisa externa de
  histórico de preço) em vez de introduzir uma exceção a eles. A validação
  de identidade de produto reaproveita o mesmo tipo de verificação já
  validado em produção pela TASK-063, não um mecanismo novo. Pesquisa
  externa exige uma ferramenta/capacidade nova de busca (a API/motor fica
  para quando a TASK for desenhada) e deve continuar passando pela porta
  única de IA (`AIProviderManager`, `CLAUDE.md`) ou por uma ferramenta
  dedicada a desenhar então. O item 12 (lives) é uma fonte de dado
  estruturalmente diferente das páginas estáticas dos Store Providers
  atuais (Playwright sobre HTML) e foi mantido como item separado, sem
  detalhamento, por não ter escopo definido ainda.
- **Próxima ação:** `docs/internal/v1.2-scope.md` atualizado com os itens 11 e 12. Nenhuma
  TASK criada; implementação, escolha de API/motor de busca e desenho da
  separação arquitetural (coleta / histórico interno / pesquisa externa /
  validação de identidade / dados estruturados / interpretação por IA /
  template final) ficam para quando a TASK for solicitada explicitamente.

### DEC-055 — Registrar lista numerada de categorias no `/cadastro` como item 4 da `v1.0.2`

- **Data:** 2026-08-10
- **Ideia:** o usuário pediu para trocar o texto livre de "quais categorias
  você compra" no `/cadastro` por uma lista numerada das categorias
  conhecidas dos sites, permitindo resposta por números (ex.: `1,2,7,8,11`),
  no mesmo padrão já usado pelo passo de lojas favoritas
  (`favorite_stores`). Auditoria confirmou em
  `backend/app/users/registration.py`: `preferred_categories` hoje é texto
  livre parseado por `_parse_categories`, sem lista fechada;
  `favorite_stores`, passo anterior no mesmo fluxo, já usa exatamente o
  padrão pedido (`1 Kabum`/`2 Pichau`/`3 Terabyte`/`4 Amazon`/`5 Todas`,
  resposta por números separados por vírgula). Posicionada como item 4
  (depois do item de edição de missão, `DEC-057`), por pedido explícito do
  usuário.
- **Classificação:** Versão futura (item 4 da `v1.0.2`, `docs/internal/v1.0.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora). Registrado
  em `v1.0.2` por pedido explícito do usuário, embora seja um ajuste de
  UX/comportamento do cadastro, não de configuração/infraestrutura pura
  como os itens 1 e 2 dessa mesma release — diferença sinalizada no próprio
  `docs/internal/v1.0.2-scope.md` para não confundir o escopo original da `v1.0.2`
  (DEC-052).
- **Justificativa técnica:** o padrão já existe e já é usado com sucesso no
  passo imediatamente anterior do mesmo fluxo (`favorite_stores`) — replicar
  para categorias é consistência de UX, não uma capacidade nova. A lista
  real de categorias por loja (Kabum/Pichau/Terabyte/Amazon) precisa de
  levantamento próprio contra os quatro sites e fica para quando a TASK for
  criada, não decidida agora.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` atualizado com o item 4 da `v1.0.2`
  (arquivo separado de `docs/internal/v1.2-scope.md`, para não confundir as duas
  versões). Nenhuma TASK criada; implementação aguarda solicitação
  explícita futura.

### DEC-054 — Registrar Magalu como quinta fonte de oferta, item 10 da V1.2

> **Atualização (`DEC-080`/`DEC-082`, 2026-08-22):** o item de fontes da V1.2
> foi ampliado para Magalu, Mercado Livre e Shopee e renumerado como item 16.
> AliExpress permanece futuro e fora dessa etapa.

- **Data:** 2026-08-10
- **Ideia:** o usuário pediu para registrar a Magazine Luiza (Magalu) como
  nova loja pesquisável na V1.2, além das quatro já selecionáveis na V1
  (Pichau, Terabyte, Amazon, Kabum). Auditoria confirmou que a Magalu não
  era mencionada em nenhum documento do projeto até agora — sem conflito
  com o texto fixo da V1 (`CLAUDE.md`/`docs/internal/project-context.md`), que só
  cita Mercado Livre/Shopee/AliExpress como "Futuro" apresentado pelo bot;
  a Magalu é uma inclusão nova, independente dessas três.
- **Classificação:** Versão futura (item 10 da V1.2, `docs/internal/v1.2-scope.md`; nenhuma
  TASK criada, nenhuma implementação autorizada agora).
- **Justificativa técnica:** mesma arquitetura de Store Provider já
  aprovada e usada pelas quatro fontes existentes (Playwright, normalização
  de preço/disponibilidade, integração com o filtro de relevância da
  TASK-063) — não introduz mecanismo novo, só mais uma fonte selecionável.
  Pesquisa de seletores/estrutura real do site, eventual tratamento
  anti-bot e ajuste da lista numerada de lojas do `/cadastro` ficam para a
  TASK, quando solicitada.
- **Próxima ação:** `docs/internal/v1.2-scope.md` atualizado com o item 10. Nenhuma TASK
  criada; implementação aguarda solicitação explícita futura.

### DEC-053 — Registrar redução de `PriceObservation` redundante como item 9 da V1.2

- **Data:** 2026-08-10
- **Ideia:** com a `v1.0.1` em produção real (coleta a cada 30 min por
  missão ativa, `DEC-046`), o usuário observou que o volume de
  `price_observations` vai crescer proporcional à frequência de polling,
  não à frequência real de mudança de preço — cada coleta grava uma linha
  nova mesmo quando o estado da oferta não mudou. Propôs uma regra para
  evitar observações redundantes da mesma oferta, cobrindo no mínimo preço
  e disponibilidade, pedindo antes uma auditoria dos campos reais de
  `PriceObservation` para decidir a comparação com precisão. Auditoria
  feita (`backend/app/collection/models.py`): campos comparáveis são
  `amount`, `currency`, `shipping_amount`, `availability` e `fulfillment`;
  `total_amount` é derivado, `observed_at`/`recorded_at` são timestamps,
  `raw_evidence` é evidência bruta e não deve gatear a comparação. Decisão
  final do usuário: só criar `PriceObservation` nova quando pelo menos um
  campo comparável mudar (usando `get_latest_price_observation`, já
  existente); coleta com o mesmo estado não grava linha nova, mas a oferta
  precisa continuar registrando que foi vista de novo sem gerar observação
  redundante — forma exata (`last_seen_at` ou equivalente) fica para a
  TASK. Histórico já gravado nunca é apagado nem compactado por este item.
  Compactação/arquivamento de histórico antigo já existente fica só como
  ideia futura registrada, sem decisão de implementação — exigiria abrir
  exceção explícita à regra de preservação de histórico do `CLAUDE.md`,
  decisão própria e separada desta.
- **Classificação:** Versão futura (item 9 da V1.2, `docs/internal/v1.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora).
- **Justificativa técnica:** o histórico deve representar mudanças reais de
  estado da oferta, não a cadência de polling do `collection_worker`; a
  regra não altera o desenho append-only de `PriceObservation` (nenhuma
  linha existente é apagada ou reescrita, só deixa de criar linhas
  redundantes daqui pra frente) e não conflita com a preservação de
  histórico do `CLAUDE.md`, já que nada gravado é descartado. A ideia de
  compactar observações antigas já existentes é uma exceção real a essa
  regra e foi deliberadamente separada, para não comprometer a decisão mais
  simples e não controversa (parar de gravar duplicata) com uma decisão
  mais sensível que ainda não tem justificativa de necessidade real.
- **Próxima ação:** `docs/internal/v1.2-scope.md` atualizado com o item 9, ordem de
  execução após os itens já existentes. Nenhuma TASK criada; implementação
  aguarda solicitação explícita futura.

### DEC-052 — Registrar `v1.0.2` como release corretiva de configuração/infraestrutura, antes da V1.2

- **Data:** 2026-08-10
- **Ideia:** a preparação de `docs/installation/linux-legacy-setup.md` para a `v1.0.1`
  encontrou dois ajustes corretivos válidos, nenhum bloqueador da
  `v1.0.1`: (1) `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` existem em
  `.env`/`.env.example`/`backend/.env.example` mas não são propagadas pelo
  `compose.yaml` atual, sem efeito real em produção; (2) só
  `collection_worker` e `telegram_notifier` têm `restart: unless-stopped`
  em `compose.yaml` — os outros cinco serviços não voltam sozinhos após
  reboot/queda de energia/crash. O usuário decidiu não implementar nenhum
  dos dois agora (prioridade é colocar a `v1.0.1` em produção primeiro) e
  registrar formalmente uma futura release corretiva **`v1.0.2`** —
  sem funcionalidade nova, só configuração/infraestrutura — que deve
  acontecer depois da `v1.0.1` estar em produção e observada, e **antes**
  da V1.2 funcional já listada em `docs/internal/v1.2-scope.md`.
- **Classificação:** Versão futura (`v1.0.2`, inicialmente registrada
  dentro de `docs/internal/v1.2-scope.md`; posteriormente movida para o documento próprio
  `docs/internal/v1.0.2-scope.md` para não misturar as duas versões no mesmo arquivo —
  nenhuma TASK criada, nenhuma implementação autorizada agora).
- **Justificativa técnica:** os dois achados são reais (confirmados por
  auditoria de `compose.yaml` durante a TASK-064/preparação do manual de
  produção) mas de baixo risco e não funcionais — remoção de configuração
  morta e ajuste de resiliência operacional, não mudança de comportamento
  de IA nem de arquitetura. Adiar para uma release corretiva dedicada
  (`v1.0.2`) evita misturar correção de infraestrutura com a primeira
  subida real em produção da `v1.0.1`, e evita competir com o escopo
  funcional já priorizado da V1.2. A cascata `Gemini Flash → Groq`
  (`DEC-050`) não é alterada por nenhum dos dois itens.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` (documento próprio, separado de
  `docs/internal/v1.2-scope.md`) registra os itens com ordem de execução (`v1.0.2` antes
  da V1.2). `docs/installation/linux-legacy-setup.md` atualizado para deixar explícito,
  no comportamento real da `v1.0.1`, que ambos os pontos estão planejados
  para correção na `v1.0.2`. Nenhuma TASK criada
  ainda; implementação aguarda solicitação explícita futura, depois da
  `v1.0.1` estar em produção.

### DEC-051 — Manter `v1.0.0` imutável; publicar `v1.0.1` como release corretiva atual

- **Data:** 2026-08-10
- **Ideia:** auditoria (a pedido do usuário) confirmou que a tag `v1.0.0`
  (`85b56c6`, criada pela TASK-054 em 2026-08-09) nunca foi movida e não
  contém as correções da TASK-063 (`DEC-048`, relevância/apresentação dos
  alertas) nem da TASK-064 (`DEC-049`/`DEC-050`, cascata Gemini Flash →
  Groq), ambas concluídas e aprovadas depois da tag existir. O
  `docs/releases/checklist.md` marcado "65/65" refletia o código corrente
  (`main`/branch da TASK-064), não o conteúdo efetivamente publicado sob
  `v1.0.0` — uma divergência real entre "release definitiva" e "estado
  aprovado atual". Decisão do usuário: `v1.0.0` **permanece intocada**,
  como marco histórico do estado da V1 em 2026-08-09 (não deve ser usada
  como referência de deploy); uma nova tag **`v1.0.1`** é publicada sobre
  o commit atual (que inclui TASK-063 e TASK-064) como a release corretiva
  e referência corrente.
- **Classificação:** Implementar agora (ação administrativa de
  versionamento sobre escopo já aprovado — TASK-054/TASK-063/TASK-064 —,
  sem nenhuma mudança de código ou arquitetura).
- **Justificativa técnica:** semver de correção (`v1.0.0` → `v1.0.1`) é
  apropriado porque TASK-063 e TASK-064 são correções sobre o mesmo escopo
  do MVP da V1 (`docs/internal/mvp.md`), não funcionalidades novas. Manter `v1.0.0`
  imutável preserva o histórico auditável e evita reescrever uma tag já
  publicada em `origin`; publicar uma tag nova em vez de mover a existente
  é a forma correta de corrigir a divergência sem apagar evidência do
  estado anterior.
- **Próxima ação:** `docs/releases/checklist.md`, `docs/tasks/TASK-054.md`
  e `docs/releases/changelog.md` atualizados nesta mesma revisão. Publicação da tag
  `v1.0.1` e atualização de `origin/main` aguardam autorização final
  explícita do usuário, em separado desta decisão.

### DEC-050 — Eliminar o nível Gemini Pro/preview da V1; USER/ADMIN/DEV usam só Flash

- **Data:** 2026-08-09
- **Ideia:** depois da auditoria da TASK-064 (`DEC-049`) mostrar que tanto o
  modelo premium configurado (`gemini-3.1-pro-preview`, preview) quanto um
  candidato GA "Pro" (`gemini-pro-latest`) falham com a chave atual
  (`quota_exceeded` imediato no candidato GA), o usuário rejeitou
  explicitamente continuar procurando um modelo Gemini Pro/premium
  alternativo. Decisão final: `USER`, `ADMIN` e `DEV` usam o mesmo modelo
  Gemini Flash (o gratuito já configurado, `Settings.gemini_model`) para
  operações automáticas de IA; a distinção de papel continua sendo só de
  permissão/autorização, nunca de modelo. Fallback só por disponibilidade:
  Gemini Flash → Groq → outros já aprovados, nunca dois modelos Gemini
  equivalentes em sequência.
- **Classificação:** Implementar agora (dentro do escopo já aprovado da
  TASK-064; não é ampliação — é simplificação de infraestrutura de IA já
  existente, TASK-059/`DEC-016`).
- **Justificativa técnica:** `AdminDevAIProviderManager` hoje monta 3
  camadas (premium, Groq opcional, gratuito), sendo as camadas 1 e 3 sobre
  a mesma chave `gemini_api_key_admin_dev`. Removendo a camada "Pro", elas
  ficam idênticas — a simplificação correta é colapsá-las numa cascata de
  2 camadas (`gemini_model` → Groq), eliminando
  `gemini_premium_model`/`AISHOPPING_GEMINI_PREMIUM_MODEL` do config e a
  tentativa redundante contra o mesmo modelo duas vezes. Afeta tanto as
  chamadas automáticas da TASK-063 (`collection_worker`) quanto as
  chamadas interativas de ADMIN/DEV via Telegram, que compartilham o mesmo
  `AdminDevAIProviderManager` — ambas se beneficiam de não gastar uma
  tentativa garantidamente perdida. `UserAIProviderManager` não muda (já
  usa só `gemini_model`, sem fallback). Memória de projeto registrada:
  `project_gemini_flash_only_v1.md`.
- **Próxima ação:** `docs/tasks/TASK-064.md` atualizado com o plano de
  implementação decorrente (cascata de 2 camadas) e os testes/validação
  necessários. Implementação aguarda autorização explícita do usuário.
  TASK-054/`v1.0.0` continua suspensa até a TASK-064 fechar.

### DEC-049 — Criar a TASK-064 para revisar disponibilidade/fallback dos provedores de IA

- **Data:** 2026-08-09
- **Ideia:** a validação real da TASK-063 (`DEC-048`) revelou um problema
  separado: `gemini-3.1-pro-preview` (camada premium do
  `AdminDevAIProviderManager`) teve 0 sucessos em 248 tentativas reais. O
  usuário pediu para tratar isso como TASK própria — auditar a cascata
  ADMIN/DEV, validar com chamadas mínimas quais modelos a chave atual
  realmente consegue usar, propor (sem implementar ainda) a melhor ordem
  de fallback, e só considerar batching depois, com evidência real.
- **Classificação:** Nova TASK do MVP (a cascata ADMIN/DEV já é
  infraestrutura aprovada da V1, TASK-059/DEC-016; esta TASK corrige sua
  disponibilidade prática, não amplia escopo — nenhum provedor novo, nenhum
  canal novo).
- **Justificativa técnica:** auditoria (`docs/tasks/TASK-064.md`) confirmou
  que `gemini-3.1-pro-preview` é oficialmente `preview` (`stable=False` na
  própria listagem da API). Um teste mínimo (uma chamada de
  `client.models.list()` mais duas chamadas reais de `generateContent`,
  sem carga adicional) mostrou que um candidato GA "Pro" (`gemini-pro-latest`)
  também falha com `quota_exceeded` de imediato, enquanto um modelo GA
  "Flash" (`gemini-3.5-flash`) responde normalmente — mais consistente com
  a chave não ter cota real de nível "Pro" do que com um problema
  específico do modelo preview escolhido. A taxonomia de erro
  (quota/indisponibilidade/timeout/autenticação/rejeição) já está separada
  corretamente no código (`_translate_api_error` idêntica em
  `gemini.py`/`groq.py`); o único gap de observabilidade encontrado é
  cosmético (falha de parsing de resposta não se correlaciona automaticamente
  com a tentativa de provedor bem-sucedida que a originou). Volume real por
  coleta é 2 operações lógicas de IA por oferta nova (nunca recorrente —
  cache permanente por `(mission_id, offer_id)`/`Product`); o pico de 248
  chamadas visto na validação veio de várias missões ficando due ao mesmo
  tempo após um restart do worker, não de uma única coleta.
- **Próxima ação:** `docs/tasks/TASK-064.md` criado com a auditoria e duas
  propostas de nova cascata (Opção A: dois modelos Flash estáveis, sem
  depender de "Pro"; Opção B: manter uma camada "Pro" GA, se o usuário
  confirmar faturamento habilitado na chave). Implementação aguarda
  autorização explícita, incluindo a resposta à pergunta sobre faturamento.
  TASK-054/`v1.0.0` permanece suspensa até esta TASK fechar.

### DEC-048 — Criar a TASK-063 para relevância de resultados e apresentação de alertas

- **Data:** 2026-08-09
- **Ideia:** antes de tratar a V1 como definitivamente pronta, o usuário
  identificou no Telegram real que alertas de preço podiam corresponder a
  itens irrelevantes (acessórios, modelos errados) e sempre mostravam o
  nome da missão em vez do nome real do anúncio, sem link direto visível.
  Pediu auditoria completa do fluxo `StoreProvider → Product/Offer →
  PriceObservation → evaluator → evento → telegram_notifier`, uso do
  `AIProviderManager` já existente para normalizar título e classificar
  correspondência (`MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`), revisão do
  template de alerta e testes cobrindo os casos críticos — sem implementar
  antes de autorização explícita.
- **Classificação:** Nova TASK do MVP (corrige rastreabilidade exigida pelo
  critério 4 de `docs/internal/mvp.md`: "uma condição de preço... produz evento e
  notificação rastreáveis" — hoje a notificação existe, mas não é
  rastreável ao anúncio real, e pode não corresponder ao produto pedido).
  Não é ampliação de escopo: usa o `AIProviderManager` já aprovado, sem
  novo provedor de IA, novo canal ou nova loja.
- **Justificativa técnica:** auditoria (`docs/tasks/TASK-063.md`) confirmou
  causa raiz concreta e não hipotética: `Offer.url` já é a URL real e
  correta; `Product.name` guarda o título bruto só na primeira coleta da
  oferta, nunca normalizado; `telegram/notifications.py::_render_alert` usa
  só `mission.title`, nunca busca `Offer`/`Product`/`Store` a partir do
  `offer_id` já presente nos payloads de evento; e
  `evaluate_price_alerts` roda para todo item devolvido pela busca do
  site, sem nenhum filtro de correspondência produto-missão. Um achado
  adicional (busca de `previous` observação sem filtrar por missão,
  compartilhando estado de "alvo já atingido" entre missões diferentes na
  mesma oferta) foi registrado na auditoria, mas fica fora do escopo desta
  TASK até decisão explícita do usuário.
- **Próxima ação:** `docs/tasks/TASK-063.md` criado com auditoria,
  diagnóstico e plano proposto; implementação aguarda autorização explícita
  do usuário, incluindo a regra para `POSSIBLE_MATCH`. A tag `v1.0.0`
  (TASK-054) permanece publicada sem alteração, mas deixa de ser tratada
  como estado final da V1 até a TASK-063 fechar (ver notas em
  `docs/tasks/TASK-054.md` e `docs/releases/checklist.md`).

### DEC-047 — Backoff persistente por `(mission_id, source)` em `MissionSource`, não na `MissionSchedule`

- **Data:** 2026-08-09
- **Ideia:** aprovar e implementar a modelagem mínima de backoff persistente
  proposta em `DEC-046`, corrigida por duas restrições explícitas do
  usuário: (1) o backoff não pode atrasar a missão inteira quando só uma
  das quatro lojas selecionadas está bloqueada — cada bloqueio confirmado
  deve afetar somente aquela fonte específica; (2) o gatilho não pode
  incluir 401 — só 403, 429 e challenge/CAPTCHA/proteção externa
  confirmada contam, porque 401 normalmente representa
  autenticação/credencial/configuração, não proteção anti-bot, e não deve
  crescer exponencialmente como se fosse rate limit (a chamada ainda falha
  normalmente; só o backoff persistente fica de fora). `MissionSource`
  (já a entidade `(mission_id, store_id)`) ganhou `next_eligible_at` e
  `consecutive_blocks` (migração `20260809_0003`); `claim_due_collections`
  passou a filtrar por fonte, sem tocar em `MissionSchedule.next_run_at`.
  O gatilho ficou restrito a bloqueio externo **confirmado** (status
  403/429 do próprio `ProviderBlockedError`) — não dispara para 401,
  timeout, erro de rede, erro de parsing, erro interno, nem para o mesmo
  `ProviderBlockedError` com status ambíguo (seletor ausente/oferta vazia,
  possível mudança de markup). A fórmula (`2**consecutive_blocks`,
  teto 6h) usa sempre o `interval_minutes` já configurado da missão, nunca
  um valor fixo, e o contador para de crescer assim que o teto é atingido.
  Sucesso reseta só a fonte que teve sucesso.
- **Classificação:** Implementar agora.
- **Justificativa técnica:** `MissionSource` já existia com a granularidade
  certa (PK composta `mission_id`/`store_id`), reutilizada pelo próprio
  `claim_due_collections` para decidir quais fontes reivindicar — bastou
  adicionar 2 colunas e um filtro a mais na mesma consulta, sem tabela nova
  nem mudança na cadência da missão. `advance_schedule`/`MissionSchedule`
  continuam exatamente como antes: o gate existente
  `if mission_claims: advance_schedule(...)` já garante que uma missão com
  todas as fontes em backoff permanece due (reexaminada no próximo poll,
  não no próximo intervalo inteiro) sem precisar de nenhuma mudança de
  arquitetura — confirmado com teste de integração real
  (`test_all_sources_in_backoff_creates_no_run_and_schedule_stays_due`).
  Distinguir bloqueio confirmado (403/429) de `ProviderBlockedError`
  ambíguo (mesma exceção, status diferente, já usada hoje também para
  seletor ausente/oferta vazia, e para 401) evita que um possível bug de
  mudança de markup na loja — ou uma falha de autenticação/configuração —
  seja tratado como se fosse proteção anti-bot confirmada. A chamada em si
  continua tratada como bloqueio pelo `app.collection.providers.base`
  existente (401/403/429 seguem interrompendo o fallback daquele ciclo);
  só o backoff persistente por fonte ficou mais restrito.
- **Próxima ação:** `docs/architecture/mission-schedules.md` atualizado com a modelagem
  final. Testes unitários (função pura de backoff, classificação de erro,
  wiring de `_record_failure`/`_persist_success`) e de integração real
  (ciclo completo de bloqueio → exclusão do claim → expiração → segundo
  bloqueio; todas as fontes bloqueadas não cria run nem erro) aprovados via
  `scripts/check.ps1` (723 testes rápidos, 90,43% de cobertura, 13
  integrações PostgreSQL, migração `20260809_0003`). TASK-053 continua sem
  fechamento até a validação E2E final ser refeita com este estado.

### DEC-046 — Intervalo de coleta configurável com stagger; 30 min é implantação temporária de 8 GB, alvo da V1 é 15 min

- **Data:** 2026-08-09
- **Ideia:** durante a auditoria de frequência de coleta pedida na retomada da
  TASK-053, ficou confirmado que a V1 já usa um intervalo fixo e global
  (`collection_schedule_interval_minutes`, `Settings`) para todas as
  missões, sem jitter/stagger e sem backoff por bloqueio externo no nível
  da agenda. O usuário decidiu, dado que o servidor de produção está
  temporariamente com 8 GB de RAM (upgrade para 16 GB previsto): (1) usar
  30 minutos como valor de implantação temporário, para reduzir o pico de
  Chromium/RAM; (2) usar `AISHOPPING_COLLECTION_MAX_CONCURRENCY=2` (em vez
  de 4) pelo mesmo motivo, sem remover a capacidade de voltar a 4; (3)
  manter 15 minutos como alvo pretendido da V1 assim que o servidor tiver
  16 GB; (4) adicionar stagger (deslocamento aleatório pequeno, só na
  criação/backfill/reativação da agenda, nunca recalculado em restart)
  para que missões com o mesmo intervalo não fiquem sincronizadas no
  mesmo instante; (5) **rejeitar** a primeira proposta de backoff após
  401/403/429 no nível da `MissionSchedule` inteira (uma loja bloqueada
  atrasaria a consulta das outras três da mesma missão) — o backoff
  persistente precisa ser por `(mission_id, source)`, com modelagem
  mínima a ser apresentada e aprovada antes de qualquer migration.
- **Classificação:** Implementar agora (intervalo temporário de 30 min,
  `max_concurrency=2` temporário, stagger na criação/backfill); Nova TASK
  do MVP ou complemento desta TASK, a definir (backoff persistente por
  fonte, modelagem ainda pendente de aprovação).
- **Justificativa técnica:** `MissionSchedule.interval_minutes` é uma
  coluna gravada por linha, lida por `advance_schedule` — nunca por
  leitura ao vivo de `Settings`. Trocar a env var de 30 para 15 no futuro
  **não migra agendas já persistidas**: só afeta missões criadas/
  recuperadas depois da troca. É preciso um `UPDATE` explícito (não uma
  migration Alembic — não há mudança de schema) no momento do upgrade
  para 16 GB, documentado como procedimento operacional em
  `docs/operations/linux-runbook.md`, para evitar coexistência silenciosa de missões em
  30 e 15 minutos. Também foi corrigida uma lacuna pré-existente: o
  serviço `api` (onde `/cadastro`/criação de missão via Telegram roda) não
  recebia `AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES` no
  `compose.yaml`, então missões novas criadas pelo Telegram usariam
  sempre o padrão de código (60 min) independentemente da env var
  configurada para o `collection_worker`; agora as duas fontes de criação
  de agenda (Telegram/`api` e backfill/`collection_worker`) leem a mesma
  env var. `collection_max_concurrency` é puramente configuração de
  runtime (não persistida): reduzir para 2 e voltar para 4 depois é
  seguro e reversível só reiniciando o `collection_worker`. O backoff por
  missão inteira foi rejeitado porque acopla a saúde de uma fonte à
  frequência de coleta das outras três, prejudicando diretamente a
  detecção rápida de promoções — objetivo central do produto.
- **Próxima ação:** `docs/architecture/mission-schedules.md` e `docs/operations/linux-runbook.md`
  atualizados com a distinção entre alvo da V1 (15 min) e implantação
  temporária de 8 GB (30 min), e o procedimento de upgrade. Modelagem
  mínima de backoff por `(mission_id, source)` a apresentar antes de
  qualquer migration; TASK-053 continua sem fechamento até essa peça e as
  demais pendências serem resolvidas.

### DEC-045 — Alertas da V1 monitoram `amount` (preço do produto), não `total_amount`; frete deixa de bloquear a TASK-053

- **Data:** 2026-08-09
- **Ideia:** a TASK-053 ficou `BLOCKED_EXTERNAL` porque as quatro lojas
  reais nunca revelam frete sem login no marketplace, e
  `evaluate_price_alerts` (TASK-027) rejeitava qualquer observação com
  `shipping_amount is None`. Decisão de produto do usuário: na V1,
  monitoramento/alertas de preço não exigem frete nem login em loja — só o
  preço do produto (`PriceObservation.amount`). Frete/parcelamento
  precisos ficam para depois: V1.2 (só ADMIN/DEV, com sessão autenticada
  nas lojas) e V2 (usuários comuns, com desenho de credenciais/sessões
  isoladas próprio).
- **Classificação:** Implementar agora (correção de escopo dos alertas e
  do critério de elegibilidade externa da TASK-053); Versão futura (itens
  de V1.2/V2 registrados abaixo).
- **Justificativa técnica:** a primeira proposta desta correção pretendia
  continuar comparando `total_amount` (que hoje é `amount +
  COALESCE(shipping_amount, 0)`). O usuário apontou que isso é errado:
  quando o frete muda de conhecido para desconhecido (ou vice-versa) entre
  duas observações da mesma oferta, `total_amount` mistura bases
  diferentes e pode gerar alerta falso. Exemplo concreto: observação
  anterior `amount=2000, shipping=100 → total=2100`; observação atual
  `amount=2050, shipping=None → total=2050`. Comparar `total_amount`
  diria "caiu" (2050 < 2100) quando o preço do produto na verdade **subiu**
  (2000 → 2050). Por isso a série de monitoramento da V1 compara sempre
  `amount` (produto vs. produto), nunca `total_amount` — em nenhuma das
  duas pontas da comparação (observação atual nem anterior), e a mesma
  base é usada tanto na decisão quanto no payload do evento publicado
  (`previous_total`/`current_total`/`target_total` recebem o valor de
  `amount`, não de `total_amount`, para os alertas da V1 — nomes de campo
  do catálogo não mudam, só a origem do valor). `total_amount` continua
  existindo, sendo persistido normalmente e sendo a base de custo final
  usada por `app.purchase` (recomendação/comparação/confirmação,
  TASK-038 a TASK-041), que **não muda** — frete desconhecido continua
  tornando uma oferta inelegível para afirmar custo total ali. A
  separação fica explícita: **alertas V1 = `amount`; custo
  final/compra = `amount + shipping` conhecido.** O critério de
  elegibilidade externa da TASK-053
  (`backend/scripts/validate_external_e2e.py`) deixa de exigir
  `shipping_amount is not None`, exigindo só disponibilidade válida e
  moeda compatível. Frete nunca é fabricado nem tratado como zero/grátis
  quando desconhecido — só deixou de ser exigido para o monitoramento de
  preço da V1.
- **V1.2 registrada** (`docs/internal/v1.2-scope.md`, item 4): "Consulta autenticada de
  frete e parcelamento para ADMIN/DEV" — só o proprietário do sistema
  inicialmente, usando sessão autenticada nas lojas (Pichau, Terabyte,
  Amazon, Kabum conforme suporte real) para obter frete/parcelamento reais,
  com carrinho apenas quando necessário e CEP configurado. Regras de
  segurança já registradas para quando a TASK for desenhada: credenciais
  nunca em prompt de IA/logs/traces/métricas/auditoria, sem senha em texto
  puro, sessão restrita ao provider, nenhuma conta compartilhada com
  `USER` comum, login/carrinho nunca autorizam compra sozinhos.
- **V2 registrada** (`docs/internal/backlog.md`): "Frete e parcelamento
  autenticados por usuário" — mesma capacidade da V1.2, aberta a usuários
  comuns, exigindo desenho próprio de credenciais/sessões isoladas por
  usuário, autorização e ciclo de vida de sessão.
- **Próxima ação:** `backend/app/alerts/evaluator.py` e
  `backend/scripts/validate_external_e2e.py` corrigidos; documentação
  sincronizada (`docs/architecture/price-alerts.md`, `docs/tasks/TASK-027.md`,
  `docs/architecture/mission-criteria.md`, `docs/development/e2e-tests.md`,
  `docs/tasks/TASK-053.md`, `docs/architecture/price-engine.md`); reexecutar a
  TASK-053 (pipeline, E2E reproduzível, E2E externo real) e atualizar seu
  resultado real, sem presumir sucesso antes de rodar. TASK-054 continua
  aguardando a conclusão real da TASK-053.

### DEC-042 — Corrigir onboarding descoberto pelo E2E

- **Data:** 2026-08-09
- **Ideia:** tornar a seleção de lojas do `/cadastro` numerada, emitir o link
  inicial de senha ao concluir o cadastro e reduzir o mínimo da senha para oito
  caracteres sem impor regras artificiais de composição.
- **Classificação:** Implementar agora.
- **Justificativa:** o E2E real da TASK-053 demonstrou que texto livre sem IA
  induzia o usuário a um formato não explicado e que separar `/senha` deixava o
  onboarding incompleto. O fluxo continua determinístico e não recebe senha no
  Telegram. O mínimo de oito é uma escolha de usabilidade da V1 protegida por
  Telegram privado, Argon2id, blocklist, limites e cooldown; não é apresentado
  como conformidade ou MFA formal, que continuam futuros. Verificação de
  e-mail permanece V2.
- **Próxima ação:** concluir as correções dentro da TASK-053 e repetir os dois
  modos E2E antes de fechar a tarefa.

**Atualização pontual (2026-08-15):** o mínimo permanece em oito caracteres,
mas a política final aprovada passou a exigir ao menos uma letra maiúscula, uma
letra minúscula, um número e um símbolo. Esta atualização substitui somente a
parte acima que dispensava composição; Argon2id, blocklist, limite máximo de
128 caracteres e demais proteções permanecem.

### DEC-043 — Confirmar operações de autenticação no chat

- **Data:** 2026-08-09
- **Ideia:** registrar no chat privado do Telegram as conclusões de criação,
  alteração e recuperação de senha e de login, além de avisar uma única vez
  antes da expiração e quando a sessão expirar.
- **Classificação:** Implementar agora.
- **Justificativa:** a senha continua restrita à página HTTPS, mas o retorno
  durável no mesmo chat em que a operação foi iniciada torna o estado de
  autenticação observável para a pessoa e cria um histórico operacional no
  Telegram. Eventos persistentes, consumo idempotente e marcadores atômicos
  por sessão evitam perda e duplicidade após restart, sem transformar
  preferências de alertas de preço em preferências de segurança.
- **Próxima ação:** implementar e validar dentro da TASK-053, incluindo
  PostgreSQL e Telegram reais.

### DEC-044 — Negociar orçamento ausente e sugerir referência na V1.2

- **Data:** 2026-08-09
- **Ideia:** quando o pedido de missão não trouxer valor, perguntar primeiro se
  a pessoa possui um orçamento; na ausência dele, consultar histórico e, se
  necessário, fontes externas para sugerir uma média de itens/marcas de menor
  preço antes da criação.
- **Classificação:** Versão futura.
- **Justificativa:** o fluxo exige contrato conversacional novo, critério de
  qualidade para amostra e marcas comparáveis, consulta externa adicional e
  regras para evidência insuficiente. O usuário reservou expressamente essa
  evolução à V1.2; introduzi-la durante o fechamento E2E da V1 ampliaria o MVP.
- **Próxima ação:** manter no backlog da V1.2 e criar especificação/TASK
  própria antes de implementar. A V1 não deve afirmar genericamente que usará
  “quatro lojas padrão” como substituto dessa conversa.

### DEC-040 — Isolar integração real por banco descartável

- **Data:** 2026-08-08
- **Ideia:** tornar os fluxos persistentes críticos uma suíte permanente contra
  PostgreSQL real sem permitir contato acidental com dados do operador.
- **Classificação:** Implementar agora.
- **Justificativa:** mocks e transações globais não exercitam migrations,
  constraints, commits ou corridas reais. Um PostgreSQL 18.4 fixado por digest,
  com recursos exclusivos por execução e banco clonado por teste, oferece
  isolamento determinístico. Guards de ambiente/banco, loopback e cleanup exato
  fazem a suíte falhar fechado sem usar prune ou infraestrutura real.
- **Próxima ação:** TASK-052 concluída; o preflight da TASK-053 originou a
  correção de ordem registrada posteriormente na DEC-041.

### DEC-041 — Orquestrar coletas antes dos testes E2E

- **Data:** 2026-08-09
- **Ideia:** criar a TASK-062 para ligar automaticamente agendas vencidas,
  fontes selecionadas, Store Providers, persistência de observações, avaliação
  e publicação no event log antes de executar a TASK-053.
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** o código possui todos esses componentes isolados, mas
  `create_mission_from_criteria` não cria agenda e nenhum processo consome
  `mission_schedules`. Um E2E que chamasse os componentes manualmente provaria
  apenas o test harness, não o funcionamento real da V1. A nova TASK fecha um
  requisito já existente nos critérios 3 e 4 do MVP sem adicionar produto,
  loja, IA ou infraestrutura distribuída.
- **Próxima ação:** TASK-062 concluída e documentada em
  `docs/tasks/TASK-062.md`; executar a TASK-053 sobre a cadeia real, sem início
  automático.

### DEC-039 — Operar de forma privada com recuperação manual comprovada

- **Data:** 2026-08-08
- **Ideia:** consolidar o runbook do Ubuntu Server, tornar binds administrativos
  privados por padrão e validar backup/restauração sem prometer disaster
  recovery ou rollback universal.
- **Classificação:** Implementar agora.
- **Justificativa:** documentação operacional precisa reproduzir início,
  diagnóstico e recuperação básica sem expor PostgreSQL/telemetria. Backup só
  tem valor depois de restauração comprovada, enquanto versões anteriores da
  aplicação podem ser incompatíveis com o schema atual. Loopback por padrão,
  restauração em banco limpo e bloqueio de downgrade automático reduzem perda e
  exposição sem criar infraestrutura de V2.
- **Próxima ação:** concluir a TASK-051; manter scheduler, testes permanentes,
  release, domínio/TLS e disaster recovery completo fora desta tarefa.

### DEC-038 — Desidentificar sem reescrever históricos

- **Data:** 2026-08-08
- **Ideia:** remover identificadores diretos e limitar retenção operacional sem
  transformar UUID e fatos correlacionáveis em falsa alegação de anonimização.
- **Classificação:** Implementar agora.
- **Justificativa:** conta, Telegram, textos livres e autenticação exigem
  minimização, mas preços, eventos, transições, auditoria e confirmações são
  fatos protegidos por imutabilidade/FKs. A operação transacional remove dados
  diretos e falha antes de mutar se detectar PII em histórico append-only.
  Telemetria recebe limites explícitos e não se afirma certificação LGPD.
- **Próxima ação:** TASK-050 concluída; executar a TASK-051. Anonimização
  irreversível de bases históricas exige avaliação futura específica.

### DEC-037 — Persistir replay/retry e manter resiliência externa local

- **Data:** 2026-08-08
- **Ideia:** impedir abuso, replay e cascatas de falha sem adicionar
  infraestrutura distribuída à V1.
- **Classificação:** Implementar agora.
- **Justificativa:** `update_id`, cota por usuário e histórico de consumo
  precisam sobreviver a restart e concorrência, portanto usam fatos append-only
  no PostgreSQL. Timeout, retry de operações seguras e circuit breaker podem
  permanecer locais por processo. `sendMessage` e outras operações
  potencialmente não idempotentes não recebem retry cego; eventos não ganham
  estado mutável. O desenho fecha a TASK-049 com segurança sem Redis ou broker.
- **Próxima ação:** TASK-049 concluída e seguida pela TASK-050. Coordenação
  distribuída de circuitos e infraestrutura de filas permanecem fora da V1.

### DEC-036 — Usar secret files com fonte única e menor privilégio

- **Data:** 2026-08-08
- **Ideia:** retirar credenciais do ambiente dos contêineres e conceder a cada
  serviço somente os arquivos de que necessita em `/run/secrets`.
- **Classificação:** Implementar agora.
- **Justificativa:** `.gitignore` evitava commit acidental dos `.env`, mas não
  evitava exposição por `docker inspect`, excesso de acesso do worker ou falta
  de detecção preventiva. `*_FILE` obrigatório em produção, conflito
  fail-closed, execução non-root e Gitleaks fixado fecham o risco imediato sem
  introduzir um cofre de V2. `POSTGRES_PASSWORD_FILE` inicializa volume novo,
  mas banco existente exige `ALTER ROLE` e reinício coordenado dos consumidores.
- **Próxima ação:** TASK-048 concluída; executar a TASK-049. Vault, cloud secret
  manager e rotação automática permanecem fora da V1.

### DEC-035 — Autenticar por senha sem expor segredo ao Telegram

- **Data:** 2026-08-08
- **Ideia:** usar links HTTPS descartáveis para criar, verificar, alterar e
  recuperar senha, mantendo sessões persistentes com TTL absoluto.
- **Classificação:** Implementar agora.
- **Justificativa:** o chat de bot não é um canal apropriado para receber senha.
  Um token aleatório, armazenado somente como hash e vinculado no servidor a
  usuário, Telegram e ação, permite abrir um formulário HTTPS sem confiar em
  identidade enviada pelo navegador. Argon2id, rate limiting persistente,
  transações atômicas e revogação fecham o escopo da V1 sem JWT/OAuth/MFA.
- **Próxima ação:** TASK-061 concluída; executar a TASK-048. Outro canal,
  recuperação por e-mail e autenticação multifator permanecem futuros.

### DEC-034 — Autorizar a V1 com papel único e ownership obrigatório

- **Data:** 2026-08-08
- **Ideia:** manter um único `users.role`, definir DEV como superusuário
  técnico por herança e aplicar autorização sem permitir bypass dos dados de
  outros usuários.
- **Classificação:** Implementar agora.
- **Justificativa:** `USER ⊂ ADMIN ⊂ DEV` atende às capacidades existentes sem
  introduzir múltiplos papéis, planos ou entitlements. A autenticação da
  TASK-046 precede a política fail-closed; ownership continua uma condição
  independente para todos os papéis. Recusas encerram o webhook sem efeito
  funcional e deixam somente auditoria sanitizada. A promoção do proprietário
  de ADMIN para DEV é one-shot, por UUID validado, e não vira regra de sistema.
- **Próxima ação:** TASK-047 concluída; executar a TASK-061 antes da TASK-048.
  Múltiplos papéis, planos e gestão de roles permanecem na V2.

### DEC-033 — Executar a TASK-061 depois da TASK-047

- **Data:** 2026-08-08
- **Ideia:** retirar a autenticação real por usuário e senha da fila da V1.2 e
  inseri-la no fluxo principal imediatamente depois da autorização.
- **Classificação:** Implementar agora.
- **Justificativa:** autorização por papel (TASK-047) fecha primeiro as ações
  permitidas; em seguida, a TASK-061 estabelece credenciais, verificação e
  recuperação antes das demais etapas de segurança e entrega. A ordem oficial
  passa a ser `TASK-047 → TASK-061 → TASK-048`, sem renumerar identificadores.
- **Próxima ação:** concluir a TASK-047; depois executar obrigatoriamente a
  TASK-061 antes de iniciar a TASK-048.

### DEC-032 — Autenticar a identidade mínima do canal Telegram

- **Data:** 2026-08-08
- **Ideia:** fechar a fronteira de confiança do Telegram sem antecipar a
  autenticação por senha da TASK-061 nem a autorização da TASK-047.
- **Classificação:** Implementar agora.
- **Justificativa:** o segredo autentica a entrega, mas uma identidade de
  pessoa só é aceita depois, em chat privado direto com
  `chat.id == message.from.id`. A conta interna precisa estar ativa antes de
  IA, domínio ou qualquer mutação. Recusas terminam em `204` e logam somente
  um motivo fechado, sem IDs ou payload.
- **Próxima ação:** TASK-046 concluída; a próxima tarefa executável é a
  TASK-047.

### DEC-031 — Separar métricas, traces e disponibilidade funcional

- **Data:** 2026-08-08
- **Ideia:** entregar observabilidade sem transformar o stack operacional em
  dependência da API nem duplicar métricas por OTLP.
- **Classificação:** Implementar agora.
- **Justificativa:** Prometheus faz scrape direto de API/worker; somente traces
  seguem pelo Collector ao Jaeger. Rotas e labels usam catálogos limitados,
  SQL omite valores, e `/health`/`ready` separam processo de dependência
  funcional. Regras Prometheus representam detecção de estado, não envio de
  notificação sem Alertmanager.
- **Próxima ação:** TASK-045 concluída; a próxima tarefa executável é a
  TASK-046.

### DEC-030 — Publicar branch da TASK e atualizar apenas a main local

- **Data:** 2026-08-08
- **Ideia:** eliminar a confirmação repetitiva para publicação da branch sem
  perder o controle explícito sobre a `main` remota.
- **Classificação:** Implementar agora.
- **Justificativa:** a branch da TASK é o artefato remoto de trabalho e pode ser
  publicada automaticamente após testes, revisão e commit. A integração na
  `main` local mantém o workspace pronto para a próxima tarefa. Já
  `origin/main` continua sendo o ponto de publicação controlado pelo usuário e
  nunca deve avançar sem pedido explícito.
- **Próxima ação:** ao concluir cada TASK, subir sua branch e atualizar a
  `main` local automaticamente; aguardar pedido somente para atualizar a
  `main` remota.

### DEC-029 — Separar solicitação imutável da trilha append-only de confirmação

- **Data:** 2026-08-08
- **Ideia:** persistir a confirmação da TASK-040 sem transformá-la em máquina
  de estados e resolver concorrência pelo PostgreSQL.
- **Classificação:** Implementar agora.
- **Justificativa:** `purchase_confirmations` preserva a evidência original e
  `purchase_trail_entries` registra `requested` e no máximo um terminal. O
  índice único parcial é a autoridade concorrente; o serviço usa SAVEPOINT e
  distingue pelo nome somente essa violação. Uma observação nova idêntica não
  invalida a proveniência, `cancel` independe de TTL e, em `confirm`, expiração
  precede revalidação. Isso garante recuperação e idempotência sem status
  mutável, compra, evento ou auditoria duplicada.
- **Próxima ação:** TASK-041 concluída e validada no PostgreSQL 18 real; a
  próxima tarefa executável é a TASK-045.

### DEC-028 — Vincular confirmação temporária à evidência original e fazê-la expirar

- **Data:** 2026-08-08
- **Ideia:** fechar a TASK-040 como uma confirmação explícita, temporária e
  somente em memória para qualquer oferta elegível escolhida pelo proprietário.
- **Classificação:** Implementar agora.
- **Justificativa:** vincular somente valores monetários permitiria confirmar
  uma evidência diferente que por acaso repetisse os mesmos números. A
  solicitação guarda missão, oferta, observação e proprietário, além do snapshot
  completo, e expira após 15 minutos em UTC. A resolução recalcula a comparação
  e exige os mesmos campos materiais relevantes. A TASK-041 refinou a regra:
  novo UUID de observação com conteúdo equivalente continua válido, enquanto a
  observação original permanece como proveniência; expiração ou divergência
  material produz `stale`.
- **Próxima ação:** TASK-040 concluída; persistência e concorrência foram
  integradas pela TASK-041 (`DEC-029`).

### DEC-027 — Compartilhar elegibilidade e ordenação entre recomendação e comparação

- **Data:** 2026-08-08
- **Ideia:** fazer a TASK-039 comparar todas as evidências da TASK-038 sem criar
  uma segunda interpretação de elegibilidade ou uma ordenação divergente.
- **Classificação:** Implementar agora.
- **Justificativa:** uma única função ordena as ofertas elegíveis por total,
  recência e UUID tanto para a recomendação quanto para a comparação. Assim, a
  posição 1 é invariavelmente a recomendação da TASK-038. Inelegíveis não têm
  posição, ficam depois das elegíveis e são estabilizadas sem usar preço. Frete
  desconhecido conserva o preço do produto, mas mantém o total indisponível e
  informa `shipping_unknown`; nenhuma comparação parcial é inventada.
- **Próxima ação:** TASK-039 concluída e validada no PostgreSQL real; a próxima
  tarefa executável é a TASK-040.

### DEC-026 — Restringir recomendação ao menor custo total determinável na moeda da missão

- **Data:** 2026-08-08
- **Ideia:** fechar o escopo genérico da TASK-038 como uma recomendação única e
  determinística por missão ativa, preservando a comparação completa para a
  TASK-039 e compra, confirmação e trilha para as TASKs 040 e 041.
- **Classificação:** Implementar agora.
- **Justificativa:** frete desconhecido não prova custo zero e, portanto, não
  pode vencer uma seleção por total. Moedas diferentes também não são
  comparáveis sem uma política de conversão, ausente do MVP. A recomendação usa
  somente coletas bem-sucedidas da própria missão, fontes selecionadas,
  disponibilidade atual, moeda exata do critério e frete conhecido. Vendedor é
  evidência opcional porque varejistas diretos não possuem `Seller`. Ofertas
  inelegíveis permanecem explicadas, sem formar a comparação ordenada da
  TASK-039. Ausência de candidata válida vira `insufficient_data`, nunca uma
  escolha parcial.
- **Próxima ação:** TASK-038 concluída e validada no PostgreSQL real; a próxima
  tarefa executável é a TASK-039.

### DEC-025 — Restringir a TASK-037 a preferências de notificações com skipped terminal

- **Data:** 2026-08-08
- **Ideia:** resolver a sobreposição aparente entre a TASK-037 genérica
  ("preferências de usuário") e os campos de lojas/categorias já entregues
  pela TASK-060, tornando a TASK-037 exclusivamente responsável por ativar ou
  desativar, de forma independente, notificações de queda de preço e de
  preço-alvo atingido pelo comando textual `/preferencias`.
- **Classificação:** Implementar agora.
- **Justificativa:** `docs/internal/project-context.md` já reservava explicitamente
  preferências de notificação à TASK-037, enquanto a TASK-060 possui escopo e
  validação próprios para cadastro. Ambas as notificações começam ativadas para
  preservar compatibilidade. Tratar opt-out como falha causaria retry infinito;
  ignorar sem histórico deixaria o consumo sem rastreabilidade. Por isso
  `skipped` é um resultado append-only, sem `failure_code`, e terminal como
  `succeeded`: não envia, não fica pendente e não reaparece ao reativar. O chat
  privado da TASK-036 continua sendo o único destino.
- **Próxima ação:** TASK-037 concluída e validada contra PostgreSQL e Telegram
  reais; TASK-060 permanece inalterada. A próxima executável é a TASK-038.

### DEC-024 — Restringir notificações Telegram a alertas e chats privados

- **Data:** 2026-08-08
- **Ideia:** implementar a TASK-036 como consumidor contínuo dos eventos
  `price.decreased.v1` e `price.target_reached.v1`, enviando somente para o
  chat privado confirmado do proprietário da missão.
- **Classificação:** Implementar agora.
- **Justificativa:** decisões anteriores reservam a TASK-036 às notificações
  proativas de alerta, enquanto preferências de notificação pertencem à TASK-037. Persistir
  chats de grupos/canais como destino automático poderia expor dados de uma
  missão a terceiros; por isso `telegram_chat_id` só é atualizado quando a Bot
  API identifica `chat.type=private` e o ID corresponde ao
  `telegram_user_id`. O consumidor usa a semântica at-least-once da TASK-044:
  rejeições da API e destinos ausentes/inativos ficam como falha rastreável e
  podem repetir; exatamente uma vez não é prometido.
- **Próxima ação:** TASK-036 concluída. A TASK-037 é a próxima executável e
  poderá definir preferências sem alterar a segurança do destino privado.

### DEC-023 — Adotar consumo at-least-once por consumidor com transação explícita

- **Data:** 2026-08-08
- **Ideia:** implementar a TASK-044 como uma fronteira genérica de consumo
  concorrente, registrando cada tentativa em histórico append-only e
  considerando concluído apenas o par evento/consumidor que possuir resultado
  `succeeded`.
- **Classificação:** Implementar agora.
- **Justificativa:** `FOR UPDATE SKIP LOCKED` distribui eventos entre
  transações concorrentes sem introduzir fila externa; manter reivindicação,
  processamento e registro sob a transação controlada pelo chamador preserva o
  lock até o desfecho. A semântica at-least-once permite retry após falha sem
  apagar evidência. Exactly-once, backoff, limite de tentativas, dead-letter
  queue, worker e integração Telegram aumentariam o escopo e pertencem a
  tarefas posteriores.
- **Próxima ação:** TASK-044 concluída e documentada em
  `docs/architecture/event-consumption.md`; a próxima tarefa executável volta a ser a
  TASK-036, que definirá o consumidor/notificação Telegram e o endereçamento
  por `chat_id` sem alterar este contrato genérico.

### DEC-022 — Reordenar TASK-036 atrás de TASK-043 e TASK-044, e restringir a TASK-043 à publicação genérica

- **Data:** 2026-08-08
- **Ideia:** ao iniciar o preflight da TASK-036 ("Criar notificações
  Telegram"), a próxima tarefa executável pela ordem do `docs/internal/roadmap.md`,
  descobri que ela depende de duas coisas inexistentes: um pipeline real de
  eventos persistidos/publicados (`docs/architecture/price-alerts.md` já atribuía
  "persistência e publicação" à TASK-043 e "consumo" à TASK-044) e um
  `chat_id` persistido para endereçar conversas do Telegram
  (`docs/architecture/users.md` já previa isso como responsabilidade de uma tarefa
  futura). O usuário confirmou implementar TASK-043 e TASK-044 antes de
  retomar a TASK-036. Durante a exploração para a TASK-043, ficou claro que
  nenhum dos seis tipos de evento do catálogo (TASK-042) tem hoje um
  produtor real com chamador em produção, exceto `evaluate_price_alerts`
  (TASK-027) — que também não tinha chamador, porque não existe ainda
  nenhum serviço que insira `PriceObservation` de verdade. Escopo da
  TASK-043 restrito a: tabela `events` (migração + modelo, conforme
  `docs/database/schema.md`) e um serviço genérico `publish_event`, validado
  contra PostgreSQL real usando candidatos reais de `evaluate_price_alerts`
  — sem detectar os outros cinco tipos de evento (nenhuma TASK atribui essa
  detecção ainda) e sem nenhum worker/consumidor (TASK-044).
- **Classificação:** Implementar agora.
- **Justificativa:** a numeração da TASK não substitui dependências
  explícitas (`docs/internal/roadmap.md`); implementar a TASK-036 sem um pipeline
  real de eventos e sem `chat_id` exigiria mockar exatamente o que
  `AGENTS.md` proíbe substituir por implementação incompleta. O mesmo
  padrão já foi aceito neste projeto para `AuditEntry` (TASK-016) e
  `MissionTransition` (TASK-021): tabelas append-only criadas e validadas
  contra PostgreSQL real antes de qualquer chamador de produção existir.
- **Próxima ação:** TASK-043 e TASK-044 implementadas e concluídas
  (`docs/tasks/TASK-043.md`, `docs/tasks/TASK-044.md`). A TASK-036 volta a
  ser a próxima executável, incluindo a definição do `chat_id`.

### DEC-021 — Criar a fase V1.2 com uma lista priorizada de evoluções entre a V1 e a V2

> **Atualização (DEC-033):** a TASK-061 foi retirada desta fase e inserida no
> fluxo principal, imediatamente após a TASK-047. Os demais itens da V1.2
> mantêm sua ordem relativa original.

- **Data:** 2026-08-08
- **Ideia:** o usuário pediu um documento próprio para uma fase "V1.2",
  que sai depois da V1 e antes da V2, com ordem de execução definida. A ordem
  original começava pela TASK-061 (posição depois supersedida pela DEC-033),
  seguida por: ajustar o cadastro para
  pedir e-mail visando notificações; enviar notificações por e-mail;
  pesquisa de cupons; e um painel administrativo web com acesso/edição
  direta ao banco, login de administrador, status/consumo da aplicação e
  controles operacionais (reiniciar aplicação/banco). Pediu para pensar em
  quantos itens fazem sentido para o painel, sem criar um arquivo de TASK
  por item agora — só listar dentro do próprio documento da V1.2.
- **Classificação:** Versão futura.
- **Justificativa:** nenhum destes itens está em `docs/internal/mvp.md` (a V1 só
  prevê notificações essenciais via Telegram, não e-mail nem cupons nem
  painel administrativo). Diferente do registro sem compromisso do
  `docs/internal/backlog.md`, o usuário quer prioridade e ordem definidas — por
  isso ganham um documento próprio (`docs/internal/v1.2-scope.md`) com a lista já
  ordenada, sem criar `docs/tasks/TASK-XXX.md` individuais ainda; cada
  item vira TASK de verdade (com preflight, validação e critério de
  aceite próprios) só quando for solicitado para execução.
- **Próxima ação:** criado `docs/internal/v1.2-scope.md` com a ordem de execução e a
  decomposição do painel administrativo; nenhuma implementação iniciada.

## Registros

### DEC-020 — Permitir armazenar e-mail em User, mantendo senha e token de fora

- **Data:** 2026-08-08
- **Ideia:** `docs/architecture/users.md` ("Limites") registrava "Não são armazenadas
  senhas, tokens, e-mails ou credenciais de autenticação real" como um
  invariante único. O cadastro inicial da TASK-060 pede e-mail como campo
  não sensível; senha/token continuam explicitamente fora (TASK-061,
  `DEC-019`). É preciso separar e-mail (dado pessoal comum) desse
  invariante, que na origem tratava tudo como "credencial de autenticação".
- **Classificação:** Implementar agora.
- **Justificativa:** e-mail não é, por si só, uma credencial de
  autenticação — é dado pessoal padrão em cadastros, e o usuário confirmou
  explicitamente querer incluí-lo na TASK-060 mesmo sabendo do invariante
  anterior. Senha e token continuam de fora, sem mudança nenhuma nessa
  parte. `docs/architecture/users.md` será atualizado para refletir a separação.
- **Próxima ação:** atualizar `docs/architecture/users.md` e `docs/database/schema.md`
  removendo "e-mails" da lista de dados não armazenados, mantendo
  senha/token/credenciais de autenticação real de fora.

### DEC-019 — Criar a TASK-061 para autenticação real por usuário e senha

> **Atualização (DEC-033):** a tarefa deixou de aguardar a retomada da V1.2 e
> passou a ser obrigatória depois da TASK-047 e antes da TASK-048.

- **Data:** 2026-08-08
- **Ideia:** ao detalhar os campos do cadastro inicial da TASK-060, o
  usuário pediu também uma senha para autenticar no bot (usuário + senha),
  "pra saber que é ele mesmo".
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** autenticação real por senha não é "dado não sensível"
  — exige hashing seguro (nunca texto puro), fluxo de verificação e
  provavelmente recuperação de conta; é uma peça de segurança com desenho
  próprio, não um campo a mais num cadastro. `docs/internal/project-context.md`
  ("O que não existe") já registra que não há autenticação real hoje, de
  propósito — a identidade via Telegram (`User.telegram_user_id`, TASK-056)
  já é confiável para o canal atual. O usuário concordou em tirar isso da
  TASK-060 e tratar como TASK própria quando pedir.
- **Próxima ação:** criada `docs/tasks/TASK-061.md` (stub, escopo a definir
  na validação); pela DEC-033, executá-la depois da TASK-047 e antes da
  TASK-048.

### DEC-018 — Criar a TASK-060 para seleção de perfil de IA por papel, cadastro inicial e placeholder de upgrade

- **Data:** 2026-08-08
- **Ideia:** três pedidos relacionados do usuário, feitos juntos ao pedir
  para executar a TASK-058: (1) o webhook do Telegram passar a escolher o
  perfil de IA (`USER` vs `ADMIN`/`DEV`) a partir de `User.role`, em vez de
  sempre usar `USER` fixo, e o usuário (dono do projeto) ter seu próprio
  `User` elevado para `ADMIN` manualmente; (2) um fluxo de cadastro inicial
  via Telegram, capturando dados não sensíveis a definir; (3) um comando ou
  opção de "mudar de usuário/perfil" visível ao usuário, mas inativo —
  reservado para uma futura oferta de upgrade, gratuita por enquanto.
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** mudar de qual perfil de IA uma interação real usa é
  uma alteração de comportamento de produção no despacho do webhook (TASKs
  033–035), afetando diretamente o invariante que separava `USER` (Gemini
  gratuito, sem fallback, reservado a usuários reais) de `ADMIN/DEV`
  (cascata premium/Groq, TASK-059) — precisa de análise própria de como o
  papel é determinado e protegido, não pode ser um ajuste dentro de outra
  TASK. O cadastro inicial introduz persistência nova (campos ainda a
  definir) e também exige TASK própria. O placeholder de "mudar de
  usuário/perfil" fica deliberadamente **inativo** — nenhuma lógica de
  cobrança, plano pago ou mudança real de papel por autoatendimento — para
  não violar `docs/internal/out-of-scope.md` ("Plano PLUS", "Usuário pago"), que
  reserva qualquer oferta paga para a V2; é só uma afordance visível,
  reservada para decisão futura.
- **Próxima ação:** criada `docs/tasks/TASK-060.md`, registrada no
  roadmap; a elevação manual do usuário do próprio dono do projeto para
  `ADMIN` é uma ação pontual e deliberada dentro desta TASK, não uma
  capacidade geral exposta a qualquer usuário.

### DEC-017 — Encerrar a TASK-057 com validação real parcial e adiar mais variedade de linguagem para a V2

- **Data:** 2026-08-08
- **Ideia:** encerrar a TASK-057 aceitando a cobertura real atual — 3 dos 4
  valores de `IntentKind` confirmados contra o `USER`/Gemini real
  (`create_mission`, `query_mission`, `mission_command`); `unknown` só foi
  confirmado via a cascata `ADMIN/DEV` (Gemini premium/Groq), não contra o
  Gemini gratuito real do `USER`, por esgotamento repetido da cota
  gratuita. Testar ainda mais tipos/estilos de linguagem informal fica para
  a V2, em vez de continuar tentando fechar 100% da cobertura agora.
- **Classificação:** Versão futura (para a ampliação adicional de
  variedade de linguagem); o encerramento da TASK-057 em si é uma decisão
  de aceite explícita do usuário, não uma nova funcionalidade.
- **Justificativa:** o usuário autorizou explicitamente encerrar a TASK-057
  nesse estado ("dá a task 57 como encerrada... qualquer coisa na v2 a
  gente testa mais tipos de linguagens"). O prompt do `IntentInterpreter`
  foi refinado e validado com sucesso para os quatro `IntentKind` via
  provedores reais (`ADMIN/DEV`: Gemini premium e Groq; `USER`: Gemini
  gratuito para 3 dos 4 tipos), a suíte automatizada está aprovada, e a
  ferramenta de validação (`--profile admin`, TASK-059) já reduziu bastante
  o risco de regressão futura sem depender da cota escassa do `USER`. Não
  há indício de defeito conhecido no `unknown` — a lacuna é só de cobertura
  de confirmação real, não de comportamento incorreto observado.
- **Próxima ação:** `docs/tasks/TASK-057.md` marcada como concluída;
  registrar em `docs/internal/backlog.md` a ampliação futura de variedade de
  linguagem/gírias do `IntentInterpreter` para a V2.

### DEC-016 — Criar a TASK-059 para avaliar o Groq como fallback de cota do AIProviderManager

- **Data:** 2026-08-08
- **Ideia:** durante o impedimento de cota da TASK-057, o usuário pediu para
  testar a chave `AISHOPPING_GROQ_API_KEY` já presente em `backend/.env`,
  fora do `AIProviderManager` e sem tocar em nenhum módulo do app (script
  descartável em `scratchpad`, nunca importado por `backend/app`). A chave
  respondeu `200` com conteúdo coerente (`openai/gpt-oss-120b`). O
  usuário pediu para registrar a possibilidade de usar o Groq como fallback
  para quando a cota gratuita do Gemini se esgotar.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** um `GroqProvider` dentro de `app.ai_provider` seguindo
  o mesmo contrato agnóstico (`AIProvider`) já usado pelo Gemini é uma
  mudança arquitetural real no `AIProviderManager`, não um ajuste mecânico
  — por isso não pode ser implementada dentro de outra TASK, mesmo
  padrão que justificou TASK própria para TASK-056/057/058. Há tensão
  explícita com o invariante já documentado em `docs/architecture/ai-provider-manager.md`
  ("OpenAI, Claude, usuário pago e comparação multi-IA ficam para a V2") e
  em `docs/internal/project-context.md` ("USER usa apenas Gemini gratuito... OpenAI,
  Claude e usuário pago ficam para a V2"): embora o Groq não esteja citado
  nominalmente nessas exclusões, o princípio por trás delas — a V1 usa
  somente o Gemini como provedor de IA — precisa ser revisto
  explicitamente antes de qualquer implementação, não assumido por
  conveniência de disponibilidade de uma chave. A motivação é real e
  relevante ao MVP (a cota gratuita do Gemini se mostrou frágil o bastante
  nesta própria sessão para bloquear validação real), mas a decisão de
  introduzir um segundo provedor no MVP exige análise própria de escopo,
  custo, confiabilidade e consistência de contrato entre respostas de
  provedores diferentes.
- **Próxima ação:** criada `docs/tasks/TASK-059.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada — incluindo, como parte
  da própria TASK, decidir se o invariante "só Gemini na V1" deve ser
  revisto.

### DEC-015 — Criar a TASK-058 para confirmação da intenção interpretada antes da execução

- **Data:** 2026-08-08
- **Ideia:** ao pedir a execução da TASK-057, o usuário pediu também que a IA
  devolva o texto interpretado da intenção e peça confirmação explícita de
  que é aquilo que a pessoa quer, antes de criar, consultar ou comandar
  qualquer missão.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** é uma mudança de comportamento no fluxo de despacho do
  webhook (resposta síncrona ao comando do usuário), explicitamente fora do
  escopo da TASK-057 (`docs/tasks/TASK-057.md`, seção "Fora de escopo": "Não
  altera `app.telegram`... TASKs 033 a 035") e não coberta pela TASK-036
  (notificações proativas de alerta) nem pela TASK-037 (preferências de notificação).
  Confirmação de intenção antes de agir é uma decisão de domínio nova e
  não-trivial — mesmo padrão que justificou TASK própria para a identidade
  do Telegram (`DEC-011`) — não um refinamento mecânico que caiba em outra
  TASK já registrada. O usuário optou explicitamente por executar apenas a
  TASK-057 agora e registrar esta ideia para decisão futura, sem
  implementá-la agora.
- **Próxima ação:** criada `docs/tasks/TASK-058.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada.

**Adendo de 2026-08-15 (correção pontual, sem nova TASK):** a entrada do
Telegram deixou de usar IA como roteador universal. Criação por linguagem
natural só ocorre depois de `/criar_missao`; cancelamento usa
`/cancelar_missao`; escolhas numéricas e confirmações usam vocabulário local
fechado. O antigo propósito `interpret_confirmation_reply` não é mais chamado.
O menu formal usa underscore porque `BotCommand.command` não aceita hífen;
aliases com hífen continuam aceitos como texto digitado.

### DEC-014 — Criar a TASK-057 para melhorar a robustez da interpretação de intenção

- **Data:** 2026-08-08
- **Ideia:** durante a validação manual real da TASK-035, uma mensagem real
  do usuário foi classificada como `unknown` quando, na avaliação do
  usuário, deveria ter sido reconhecida — o `IntentInterpreter` (TASK-032)
  precisa ficar mais robusto para diferentes formas de escrita.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** o usuário pediu explicitamente o registro como próxima
  tarefa, não a correção imediata. O `IntentInterpreter` já foi validado
  contra o Gemini real nas TASKs 032, 034 e 035 para as mensagens testadas;
  isso é um refinamento de qualidade de classificação, não um defeito
  estrutural, e não deve ser implementado sem uma TASK própria — alterar o
  prompt de sistema durante a validação da TASK-035 misturaria escopos e
  arriscaria regressão sem a validação dedicada que a mudança merece.
- **Próxima ação:** criada `docs/tasks/TASK-057.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada.

### DEC-013 — Distinguir erro conhecido de falha inesperada no despacho de missão do webhook

- **Data:** 2026-08-08
- **Ideia:** ao capturar exceções no despacho de `Intent` por comando de
  missão (TASK-035), a rota do webhook só deve tratar como resposta
  controlada (`204` + mensagem ao usuário) os erros **esperados e conhecidos**
  de domínio/validação. Qualquer falha inesperada — incluindo `IntegrityError`
  residual não tratada no serviço apropriado — não deve ser mascarada.
- **Classificação:** Implementar agora
- **Justificativa:** decisão do usuário ao revisar o plano da TASK-035: uma
  captura genérica de exceções esconderia bugs reais atrás de um `204`
  aparentemente saudável. A única corrida esperada com `IntegrityError`
  continua isolada dentro de `get_or_create_telegram_user` (TASK-056, via
  `SAVEPOINT`); tudo o mais que chegar até a rota como `IntegrityError` é
  inesperado e deve subir como `500`. A lista de exceções conhecidas é
  fechada e explícita: `MissionNotFoundError`, `MissionVersionConflictError`,
  `InvalidMissionTransitionError`, `MissionTransitionConditionError`,
  `MissionReferenceError` e `MissionIntentError`.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/mission-commands.md` e
  implementado em `backend/app/telegram/router.py`
  (`_KNOWN_DISPATCH_ERRORS`).

### DEC-012 — Fechar o escopo da TASK-035 (comandos de missão via Telegram)

- **Data:** 2026-08-08
- **Ideia:** `docs/tasks/TASK-035.md` só trazia uma frase de escopo real
  (seleção de fontes). Quatro decisões precisaram ser fechadas antes de
  implementar: (1) sem teclado interativo agora — as fontes vêm do que o
  `IntentInterpreter` já extrai do texto livre; (2) a TASK-035 envia uma
  resposta síncrona mínima ao Telegram, e não a TASK-036; (3) o seed das
  quatro lojas da V1 entra nesta TASK, por ser dado de referência fixo já
  definido em `docs/architecture/providers.md`; (4) quando o `Intent` de criação
  não especifica nenhuma fonte, a missão usa automaticamente as quatro
  fontes da V1 e sai `active` — nunca fica em `draft` por falta de fonte.
- **Classificação:** Implementar agora
- **Justificativa:** `docs/internal/mvp.md` exige que "um usuário autorizado consegue
  criar e consultar uma missão pelo canal Telegram" — sem resposta síncrona,
  "consultar" não tem como funcionar para o usuário. `TASK-036` continua
  reservada a notificações proativas orientadas a evento (alertas de preço,
  TASK-027/042-044), não a essa resposta ao próprio comando do usuário. O
  seed de lojas é dado de referência fixo, sem decisão de domínio nova,
  diferente do que justificou uma TASK própria para a identidade do Telegram
  (`DEC-011`). Toda `CREATE_MISSION` válida sair `active` evita o estado
  intermediário "criada mas inerte" que uma missão em `draft` sem fonte
  representaria.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/mission-commands.md` e
  `docs/tasks/TASK-035.md`.

### DEC-011 — Criar a TASK-056 para vincular identidade do usuário ao Telegram antes da TASK-035

- **Data:** 2026-08-07
- **Ideia:** ao preparar a TASK-035 ("Criar comandos de missão"), identifiquei
  que persistir uma missão via Telegram exige `Mission.user_id`, uma FK
  obrigatória para `User`. `docs/architecture/users.md` hoje declara explicitamente que
  nenhum identificador do Telegram é armazenado e que não existem serviços de
  CRUD de usuário. Sem resolver qual `User` corresponde a um chat do
  Telegram, a TASK-035 não tem como gravar o proprietário da missão.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** `docs/internal/mvp.md` exige, como critério objetivo de conclusão
  do MVP, que "um usuário autorizado consegue criar e consultar uma missão
  pelo canal Telegram" — isso pressupõe uma identidade resolvível, que ainda
  não existe. Não é autenticação real (reservada à TASK-046) nem autorização
  (TASK-047): é o vínculo mínimo necessário para o próximo passo do fluxo já
  iniciado nas TASKs 032 a 034. O usuário, ao ser consultado, optou por pausar
  a TASK-035 e criar esta tarefa prévia em vez de ampliar o escopo da 035 ou
  implementar apenas a camada de apresentação sem persistência.
- **Próxima ação:** criada `docs/tasks/TASK-056.md`, fora da faixa numérica
  original (mesmo padrão da TASK-042 e da TASK-055), posicionada no roadmap
  imediatamente antes da TASK-035, que permanece bloqueada até a TASK-056 ser
  executada. `docs/internal/roadmap.md`, `docs/tasks/README.md`, `AGENTS.md` e
  `docs/architecture/users.md` atualizados para refletir a pendência.

### DEC-010 — Nunca converter falha de interpretação em falha de transporte no webhook

- **Data:** 2026-08-07
- **Ideia:** depois que o webhook do Telegram autentica e aceita uma
  atualização, uma falha subsequente ao traduzi-la em `Intent` (cota de IA
  excedida, indisponibilidade do provedor, conteúdo inválido) não deve virar
  `500`. A entrega da atualização pelo Telegram e o processamento por IA são
  tratados como falhas independentes.
- **Classificação:** Implementar agora
- **Justificativa:** decisão do usuário durante a aprovação do plano da
  TASK-034: um `500` faria o Telegram reentregar a mesma atualização,
  potencialmente repetindo a chamada de IA sem necessidade. A falha já é
  registrada pela telemetria sanitizada da TASK-031; a rota apenas confirma o
  recebimento com `204`, sem executar ação de domínio, sem responder ao
  usuário e sem criar mecanismo de retry, fila ou execução de comando — isso
  permanece reservado às TASK-035 e TASK-036.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/telegram-adapter.md` e
  `docs/tasks/TASK-034.md`.

### DEC-009 — Restringir a TASK-033 à fronteira de entrada do Telegram

- **Data:** 2026-08-07
- **Ideia:** `docs/tasks/TASK-033.md` só continha o texto-modelo genérico
  ("Definir adaptação Telegram"), sem escopo detalhado. Definir o que essa
  tarefa cobre exclusivamente a partir do que já está documentado:
  representar a mensagem bruta do Telegram como contrato imutável e
  traduzi-la em um `Intent`, chamando somente o `IntentInterpreter` já
  existente (TASK-032).
- **Classificação:** Implementar agora
- **Justificativa:** `docs/architecture/overview.md` lista Telegram como módulo-alvo
  com fronteira própria; `docs/architecture/telegram.md` já definia que "o adaptador deve
  traduzir mensagens em comandos ou intenções sem conter lógica de
  domínio" e que autenticação, webhooks, comandos e notificações ficam para
  tarefas posteriores. `docs/internal/roadmap.md` já reserva a TASK-034 para o
  webhook real, a TASK-035 para comandos de missão, a TASK-036 para
  notificações e a TASK-037 para preferências de notificação. Incluir qualquer
  uma dessas responsabilidades na TASK-033 seria antecipar tarefas futuras,
  proibido por `AGENTS.md`.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/telegram-adapter.md` e
  `docs/tasks/TASK-033.md`. Webhook, comandos, notificações e preferências
  pertencem às TASKs 034 a 037.

### DEC-008 — Fechar o vocabulário de intenção da TASK-032 na documentação existente

- **Data:** 2026-08-07
- **Ideia:** definir o conjunto de `IntentKind` e parâmetros da interpretação
  de intenção estritamente a partir do que já estava documentado, sem
  adicionar nem omitir nada.
- **Classificação:** Implementar agora
- **Justificativa:** `docs/architecture/mission-system.md` já define os seis comandos
  fechados de `MissionCommand` (`activate`, `pause`, `resume`, `complete`,
  `cancel`, `expire`); `docs/internal/mvp.md` exige explicitamente que o usuário
  consiga "criar e consultar uma missão pelo canal Telegram"; e
  `docs/architecture/telegram.md` fixa as quatro fontes selecionáveis da V1. `IntentKind`
  reaproveita `MissionCommand` diretamente em vez de duplicar suas strings, e
  `IntentParameters` reaproveita os campos já existentes de
  `MissionCriteria` e `mission_sources`. Nenhum campo, comando ou fonte novos
  de domínio foram introduzidos.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/intent-interpretation.md` e
  `docs/tasks/TASK-032.md`. Decisões de execução de comando e de canal
  pertencem às TASKs 033 em diante.

### DEC-007 — Limitar a V1 ao Gemini por nível de acesso

- **Data:** 2026-08-02
- **Ideia:** usar Gemini gratuito para USER e permitir que ADMIN/DEV tentem o melhor modelo Gemini, com retorno automático ao gratuito quando o nível pago não estiver disponível.
- **Classificação:** Implementar agora
- **Justificativa:** Gemini 3.6 Flash possui nível gratuito e já foi validado, enquanto OpenAI e Anthropic não oferecem API geral gratuita nas contas configuradas. A política mantém uma integração real na V1, evita dependência de créditos e preserva um único comportamento compartilhado para ADMIN/DEV.
- **Próxima ação:** implementar na TASK-030 USER somente com `gemini-3.6-flash` e ADMIN/DEV tentando `gemini-3.1-pro-preview` antes do fallback gratuito; registrar usuário pago e OpenAI/Claude como evolução da V2.

### DEC-006 — Corrigir a TASK-055 para fontes selecionadas pelo usuário

- **Data:** 2026-08-02
- **Ideia:** substituir o escopo exclusivo da Kabum por coleta em todas as lojas e marketplaces explicitamente selecionados pelo usuário para a V1.
- **Classificação:** Implementar agora
- **Justificativa:** a TASK-055 registrada anteriormente não representa o requisito informado pelo usuário. A correção amplia arquitetura, testes e manutenção, pois cada fonte exige um Store Provider próprio, mas continua limitada à seleção explícita e não autoriza descoberta ou integração automática de qualquer marketplace.
- **Próxima ação:** implementar na TASK-055 providers para Pichau, Terabyte, Amazon e Kabum; na TASK-035, exibir essas opções como selecionáveis e apresentar abaixo, sob ***Futuro***, Mercado Livre, Shopee e AliExpress desabilitados.

### DEC-005 — Ordenar observações de preço após coletas persistidas

- **Data:** 2026-08-02
- **Ideia:** corrigir a ordem de execução da TASK-015 para respeitar sua FK obrigatória para `collection_runs`.
- **Classificação:** Implementar agora
- **Justificativa:** executar a TASK-015 antes da TASK-026 exigiria antecipar persistência de coletas ou violar o contrato relacional e a rastreabilidade histórica definidos na TASK-010.
- **Próxima ação:** executar a TASK-016 antes do bloco de missões, seguir da TASK-019 à TASK-026, executar então a TASK-015 e, na sequência, a TASK-017.

### DEC-001 — Instituir governança de novas funcionalidades

- **Data:** 2026-08-01
- **Ideia:** registrar decisões e classificar previamente toda nova funcionalidade sugerida.
- **Classificação:** Implementar agora
- **Justificativa:** a política protege o escopo definido em `docs/internal/mvp.md`, evita aumento de complexidade não planejado e cria rastreabilidade para decisões futuras.
- **Próxima ação:** aplicar a política no `AGENTS.md`; registrar ideias futuras em `docs/internal/backlog.md`, `docs/internal/out-of-scope.md` ou no roadmap conforme sua classificação.

### DEC-002 — Instituir workflow permanente de execução de TASKs

- **Data:** 2026-08-01
- **Ideia:** padronizar preparação, validação, implementação, testes, revisão, documentação, commit e push para toda TASK.
- **Classificação:** Implementar agora
- **Justificativa:** o workflow preserva o escopo do MVP, aumenta a rastreabilidade das entregas e garante que código, documentação e repositório permaneçam sincronizados.
- **Próxima ação:** aplicar automaticamente o workflow definido em `AGENTS.md`
  a toda TASK futura. A política de push foi posteriormente refinada pela
  `DEC-030`.

### DEC-003 — Inventariar dependências para novas máquinas

- **Data:** 2026-08-01
- **Ideia:** registrar dependências e verificar a compatibilidade do ambiente antes de iniciar TASKs em outra máquina.
- **Classificação:** Implementar agora
- **Justificativa:** evita instalações desnecessárias, mantém o ambiente reproduzível e preserva a autorização do usuário para qualquer download ou instalação.
- **Próxima ação:** manter `docs/development/dependencies.md` e `backend/requirements.txt` atualizados; comparar o ambiente antes de cada nova TASK.

### DEC-004 — Acompanhar a versão estável mais recente do Python

- **Data:** 2026-08-01
- **Ideia:** manter o projeto na versão estável mais recente do Python, em vez de fixá-lo permanentemente em uma série menor antiga.
- **Classificação:** Implementar agora
- **Justificativa:** a alteração afeta somente a política de ambiente, não amplia o escopo funcional do MVP e evita instalar uma versão antiga quando a versão estável atual é compatível. Cada atualização continua condicionada à validação das dependências e dos testes aplicáveis.
- **Próxima ação:** registrar em `docs/development/dependencies.md` a versão mais recente efetivamente validada e repetir a validação quando uma nova versão estável for adotada.
### DEC-061 — Compartilhar roteamento gratuito entre USER e DEV

- **Data:** 2026-08-15
- **Ideia:** manter toda IA atrás do `AIProviderManager`, ampliar o perfil
  `USER` exclusivamente com fallbacks gratuitos
  Gemini → Groq (`openai/gpt-oss-120b`) → OpenRouter (`openrouter/free`) e
  dar ao perfil `DEV` a mesma capacidade gratuita. Em requisições normais,
  ambos seguem Gemini → Groq (`openai/gpt-oss-120b`) → OpenRouter
  (`openrouter/free`). Pesquisa web é exclusiva de DEV e opt-in por
  `AIRequest.require_search_grounding`; nesse caso a requisição vai diretamente
  à Firecrawl Search API v2 direta e, após fontes válidas, à mesma cascata
  gratuita de LLM. `ADMIN`, papel histórico do domínio, compartilha a mesma cascata
  gratuita e não constitui uma terceira política de IA.
- **Classificação:** Implementar agora
- **Justificativa:** pedido explícito do usuário para corrigir a continuidade
  após timeout/HTTP 504 do Gemini, remover o modelo Groq antigo e impedir uso
  intencional de modelos pagos. A mudança reutiliza contratos,
  circuit breaker, telemetria e capability de grounding já existentes; não
  altera fluxos determinísticos do Telegram, coleta, preços ou missões.
- **Próxima ação:** implementar e validar somente o roteamento descrito, sem
  no máximo duas chamadas reais gratuitas e controladas, sem push, rebuild ou
  deploy. A TASK-086 do drift conhecido do `alembic check` permanece não iniciada.

## Decisão TASK-118F — integração AI DEV

Classificação: **Implementar agora**, por solicitação explícita após 118E.
Preservar roles exige extensão retrocompatível `prompt` OU `messages` no Core,
autorizada pelo usuário. Consumidor envia somente mensagens tipadas; flags
default false. Disaster opt-in apenas por falha de conexão, nunca mascarando
HTTP de auth/policy/quota ou repetindo inferência após timeout incerto.
Grounding/Search não migram. Credenciais DEV separadas por arquivo, sem PROD,
commit ou push. Estado e testes: `docs/tasks/TASK-118F.md`.
