# Roadmap

| Fase | Tarefas | Resultado |
| --- | --- | --- |
| Fundação | TASK-000 | Estrutura e memória do projeto |
| Base técnica | TASK-001 a TASK-009 | Ambiente, aplicação e qualidade mínima |
| Ciclo de vida de missões | TASK-018 | Estados e transições definidos antes do modelo persistente |
| Dados | TASK-010 a TASK-017 | Modelo PostgreSQL e persistência, após a definição do ciclo de vida |
| Missões e coleta | TASK-019 a TASK-026 | Missões, coleta e histórico |
| Store Providers selecionados | TASK-055 | Busca em Pichau, Terabyte, Amazon e Kabum |
| Catálogo de eventos | TASK-042 | Eventos definidos antes dos alertas de preço |
| Alertas de preço | TASK-027 | Alertas baseados no catálogo de eventos |
| IA e interação | TASK-028 a TASK-037 | Gerenciador de IA e Telegram |
| Identidade do usuário no Telegram | TASK-056 | Vincula um `User` interno a uma pessoa do Telegram (`telegram_user_id`), pré-requisito da TASK-035 |
| Robustez da interpretação de intenção | TASK-057 | Refina o `IntentInterpreter` (TASK-032) para diferentes formas de escrita, sem alterar seu vocabulário |
| Confirmação da intenção interpretada | TASK-058 | Devolve a intenção interpretada e pede confirmação antes de executar comandos de missão (`DEC-015`) |
| Fallback de cota do AIProviderManager | TASK-059 | Avalia e, se aprovado, integra o Groq como fallback quando a cota do Gemini se esgotar (`DEC-016`) |
| Perfil de IA por papel, cadastro e placeholder de upgrade | TASK-060 | Webhook escolhe o perfil de IA a partir de `User.role`, cadastro inicial não sensível e opção de upgrade visível porém inativa (`DEC-018`) |
| Compra e eventos | TASK-038 a TASK-041 e TASK-043 a TASK-045 | Fluxos de compra, publicação, consumo e monitoramento |
| Segurança — canal e autorização | TASK-046 e TASK-047 | Autenticação mínima do Telegram e autorização por papel |
| Autenticação real por usuário e senha | TASK-061 | Executada depois da TASK-047: hashing seguro, verificação e recuperação de conta (`DEC-019`, `DEC-033`) |
| Segurança e entrega — preparação | TASK-048 a TASK-052 | Segredos, resiliência, privacidade, documentação e integração |
| Orquestração automática das coletas | TASK-062 | Liga agendas, fontes, providers, histórico, avaliação e event log antes dos E2E (`DEC-041`) |
| Testes E2E e lançamento | TASK-053 e TASK-054 | Validação ponta a ponta do fluxo real e preparação da release |
| Relevância e apresentação de alertas | TASK-063 | Corrige rastreabilidade do alerta ao anúncio real e filtro de correspondência produto-missão, antes da release ser definitiva (`DEC-048`) |
| Disponibilidade e fallback dos provedores de IA | TASK-064 | Revisa a cascata ADMIN/DEV do `AIProviderManager` (modelos, ordem, taxonomia de erro) achada degradada durante a validação da TASK-063, antes da release ser definitiva (`DEC-049`) |
| Drift de constraints no Alembic | TASK-086 | Concluída; metadata corrigida e runner aprovado em PostgreSQL 18.4 descartável |
| Padronização de textos visíveis | TASK-087 | Concluída; catálogo aplicado sem mudança funcional e suíte não-integração aprovada |
| Listagem de missões e menu Telegram | TASK-088 | Concluída; comando determinístico numerado para ativas, pausadas e canceladas; menu oficial sincronizado no deploy |
| Mídia e entrega individual de ofertas | TASK-084 | Concluída; imagem persistente, link curto próprio e checkpoint por evento (`DEC-066`) |
| Classificação de vendedor/entrega | TASK-077 | Concluída; classificação histórica Amazon/KaBuM! com detalhe limitado e fail-soft (`DEC-067`) |
| Preço à vista e parcelado | TASK-089 | Implementada e testada; modelo 1:N `OfferInstallmentOption` por observação, corrigido pela investigação real (`DEC-069`, substitui o desenho de 3 campos da `DEC-068`); alertas/pré-lista já mostram `💰 À vista`/`💳 Parcelado`; interpretação de "quero em Nx" pelo usuário adiada para V2. Terabyte temporariamente desativada e simplificada para só-card (`DEC-070`, bloqueio Cloudflare) |
| Pausar/retomar manual e cancelamento em massa | TASK-090 | Implementada e testada; `/pausar` e `/retomar` novos, `/cancelar_missao` aceita seleção múltipla, todos reaproveitando a infraestrutura genérica da TASK-085 sem IA; `/editar_missao` encadeia direto no menu após pausar para editar, sem retomar sozinho. Alerta de preço-alvo repetitivo e busca "iphone 16 512" seguem pendentes, fora de escopo |
| Fundação da aplicação web | TASK-091 | Concluída e publicada em `origin/main` (`65b1d4b`); sessão própria, CSRF acoplado a `require_web_session` (não a router), whitelist de rotas da SPA e empacotamento Docker multi-stage (`DEC-074`) |
| Área USER — gerenciamento de missões pela web | TASK-092 | Concluída e publicada em `origin/main` (`4b1e677`); reaproveita `app.missions` sem regra nova, sessão assíncrona própria (`DEC-075`) |
| Reduzir `PriceObservation` redundante | TASK-093 | Concluída e publicada em `origin/main` (`cf666fa`, correção `650b067`); redundância avaliada pelo estado comercial completo (preço, disponibilidade, vendedor/fulfillment, condição, parcelamento), nunca só preço igual isoladamente; sem arquivo formal de TASK, gap registrado sem reconstrução retroativa (`DEC-073`/`DEC-076`) |
| Pré-lista com múltiplas ofertas relevantes por loja | TASK-094 | Concluída e publicada em `origin/main` (`b915106`); até 5 ofertas por loja, ranking por relevância/condição/vendedor/disponibilidade/preço, eventos `mission.prelist_ready.v2` (`DEC-076`) |
| Página rica de oferta USER | TASK-095 | Concluída, aprovada e publicada em `origin/main` (`0924f42`); detalhe centrado em Offer com ownership por missão/relevância |
| Avaliações por oferta/loja | TASK-096 | Concluída, aprovada e publicada em `origin/main` (`fd5a6f9`); snapshot explícito na Offer, página USER e Telegram (`DEC-078`) |
| Identidade global de produto/variante | TASK-097 | Concluída, aprovada e publicada em `origin/main` (`eeb2f4a`); equivalência fail-closed e escolha determinística de variantes (`DEC-079`) |
| Pesquisa de produtos pela Web | TASK-099 | Concluída, aprovada e publicada em `origin/main` (`e9610c3`); pesquisa read-only e missão somente após “Monitorar” (`DEC-083`) |
| Área USER — ofertas | TASK-100 | Concluída, aprovada e publicada em `origin/main` (`b4e61c5`); listagem paginada, fail-closed e sem duplicação (`DEC-084`) |
| Área USER — minha conta | TASK-101 | Concluída, aprovada e publicada em `origin/main` (`c57f5ab`); perfil, preferências e vínculo Telegram opcional (`DEC-085`) |
| Área DEV/ADMIN | TASK-102 | Concluída, aprovada e publicada em `origin/main` (`b93bcfa`); dashboard, dados e operações runtime-neutral (`DEC-086`) |
| Comparação entre lojas | TASK-103 | Concluída e publicada `610a997`; mesma identidade específica e ownership por Offer (`DEC-087`) |
| Store Provider Magalu | TASK-104A | Concluída e publicada em `origin/main` (`7384de0`); Edge/CDP supervisionado é o único transporte operacional, parser SSR comum e falha rápida isolada (`DEC-090`) |
| Store Provider Mercado Livre | TASK-104B | Concluída e publicada em `origin/main` (`22a9062`); Playwright normal primário e Edge/CDP como último recurso (`DEC-091`) |
| Store Provider Shopee | TASK-104C | Adiada; bloqueio anti-bot confirmado mesmo autenticado (Edge/CDP, login real, sessão persistente) — nenhum código de provider escrito (`DEC-092`) |
| Reativação da Terabyte via Edge/CDP | TASK-105 | Concluída e publicada em `origin/main` (`31df942`); Cloudflare bloqueava só o Chromium gerenciado pelo Playwright — mesma infra CDP da Magalu reaproveitada como transporte primário/único, sem fallback Playwright; `stores.is_active` volta a `true` por migration (`20260822_0009`) |
| Pesquisa de cupons | TASK-106 | **Em pausa por decisão do usuário (2026-08-22)** — arquitetura de subsistema independente já reorientada e preservada intocada (`DEC-093`), auditoria real (Amazon/Kabum/Magalu/ML) concluída; retomada fica para depois de TASK-107/108. Nenhum código escrito |
| Histórico e gráficos por produto/variante | TASK-098 | **Concluída no DEV (2026-08-27)** — último item da V1.2 (`DEC-081`/`DEC-082`); backend, frontend e suíte de testes completa (unitários, integração PostgreSQL real, `EXPLAIN ANALYZE`); publicação em `origin/main` pendente, sem deploy |
| Cotas e capacidade por usuário | TASK-107 | Concluída e publicada em `origin/main` (`ac34725`/`faa939c`/`83a9572`/`d61951a`); `max_active_missions=5`, `max_store_slots=18`, `max_daily_searches=30`, nunca pausa/cancela automaticamente, UX obrigatória com ações contextuais, override por ADMIN (`DEC-094`) |
| Fila justa e controle de carga | TASK-108 | **Concluída (2026-08-24) e publicada em `origin/main`** — fila justa por usuário (cooldown 1–3 min, `DEC-095`) reaproveitada do WIP sobre a arquitetura final da TASK-109; nova camada de pacing GLOBAL por loja (`StoreThrottleState`, auditoria confirmou que não existia antes) e config persistida editável pelo ADMIN (`CollectionQueueConfig`, sem depender só de `.env`); os 2 testes de integração legados que falhavam por um bug pré-existente não relacionado (dedupe da TASK-093 x contrato de alertas) voltaram a passar após a correção separada `DEC-097` (`742dcf2`, também publicada) |
| Migrar collection_worker para Windows nativo com Edge | TASK-109 | **Concluída, aprovada e publicada em `origin/main`** — worker nativo Windows (Task Scheduler + Windows Ops Agent), Edge via CDP loopback direto (sem bridge) com lifecycle sob demanda (lease/idle-timeout), `collection_worker` removido do `compose.yaml`, `ops_controller` integrado ao Windows Ops Agent (`WindowsOpsAgentAdapter`); zero `.launch()` de qualquer tipo em todo o projeto — `BrowserSession` (só teste) conecta via `connect_over_cdp()` a um Edge dedicado da suíte (`tests/conftest.py`), mesma arquitetura de produção. Deploy em produção ainda não feito (`DEC-096`) |
| Atualizar `docs/architecture/providers.md` | TASK-110 | **Concluída, commitada localmente (`36351bd`)** — tabela de fontes corrigida para as 6 reais (Pichau/Terabyte/Amazon/Kabum/Magalu/Mercado Livre), transporte de navegador corrigido para Edge/CDP nativo Windows (TASK-109), Chromium/Xvfb/Docker removido da descrição atual. Publicação em `origin/main` ainda pendente |
| Corrigir asserção desatualizada em `test_product_identity.py` | TASK-111 | Concluída e publicada em `origin/main` (`471e898`, junto da TASK-112 fase 3A — achado recorrente na regressão dessa fase). `test_same_variant_from_all_stores_reuses_one_global_product` agora espera as 6 lojas reais |
| Vincular missões que monitoram o mesmo item, sem duplicar coleta | TASK-112 | **Funcionalmente concluída.** **Fase 1 publicada em `origin/main` (`1dca734`, `DEC-098`)** — Product Identity Engine genérico (registry de categorias/atributos, 23 categorias, extractors de CPU/GPU/smartphone). **Fase 2 publicada em `origin/main` (`5d05767`, `DEC-099`)** — `MonitoringItem`/`MissionMonitoringItem`/`MonitoringItemStore`, `monitoring_key` v2 com `scope` explícito (`SPECIFIC`/`FAMILY`/`GENERIC`). **Fase 3A publicada em `origin/main` (`471e898`, `DEC-100`)** — coleta compartilhada durável: provider 1x, persistência comercial 1x, fan-out individual com máquina de estados completa (retryable nunca vira terminal sem prova, elegibilidade revalidada antes do fan-out, recuperação automática de tarefa presa, notificação via outbox idempotente da TASK-080). **Fase 3B concluída localmente, ainda não publicada (`9f95351`, `DEC-101`)** — `CollectionOrchestrator` de produção passa a chamar `claim_due_work`, scheduler unificado (fairness por lock real, cadência NORMAL/PROMO_CALENDAR/HIGH_ACTIVITY, agendamento legado por `MissionSource` em vez de `MissionSchedule` — correção estrutural pós-auditoria), single-flight compartilhado, fan-out durável. Suíte de integração 166/166 |
| Estado operacional no Windows Server | manutenção 2026-08-16 | HEAD `0e90cf0` implantado; 7 serviços saudáveis; WSL2 limitado a 4 GB; somente schedules de missões ativas habilitados |
| Expansão de fontes na V1.2 | TASK-104A/B/C | Magalu e Mercado Livre implementadas; Shopee adiada por bloqueio anti-bot; AliExpress permanece futuro (`DEC-080`/`DEC-089`/`DEC-092`) |
| Avaliação inteligente de preço, pesquisa de mercado e qualidade dos alertas — **absorve e implementa o item 17 da V1.2** (menor preço histórico externo) | TASK-113 | **Concluída no DEV, commitada localmente (2026-08-27)** — commit principal `03b7370` (implementação: schema, migration `20260827_0001`, evaluator A/B/C, `MarketPriceAssessment`/`MissionProductAlertState`, wiring legado + shared/fan-out, testes unitários e de integração) e commit documental posterior `8ad5709` (validação real do `/v2/search` da Firecrawl contra a API de produção). `docs/tasks/TASK-113.md` §33/§39-42 registra o desenho e o estado real da implementação. Chave por `product_id` (Product Identity Engine, TASK-097), checkpoint `MissionProductAlertState` por `(mission_id, product_id)`, `MarketPriceAssessment` com single-flight crash-safe (lease), TTL/re-alert/material improvement determinísticos, Firecrawl + AI Provider Manager. `/v2/search` validado ponta a ponta com chamada real; `/v2/scrape` segue validado só contra documentação oficial e testes mockados (não bloqueia a TASK). Item 17 da V1.2 absorvido e implementado, não é mais item sem TASK nem item pendente. **Publicação em `origin/main` ainda pendente — nenhum push/deploy feito, PROD intocada** |

As TASKs 000 a 053 e as TASKs 055 a 062 estão
concluídas. O preflight
da TASK-036 revelou dependências reais não satisfeitas (pipeline de eventos
persistidos/publicados e `chat_id` do Telegram, nenhum dos dois existente
antes desta sessão — `DEC-022`); TASK-043 (persistência e publicação de
eventos) e TASK-044 (consumo at-least-once por consumidor, `DEC-023`) foram
implementadas primeiro. A TASK-036 (`DEC-024`) persistiu somente o chat
privado, implementou o consumidor dos alertas de preço e o validou contra
Telegram, PostgreSQL e Docker reais. A TASK-037 (`DEC-025`) adicionou
preferências independentes para quedas e preço-alvo, com `skipped` terminal,
e foi validada contra PostgreSQL e Telegram reais. A TASK-038 (`DEC-026`)
implementou a recomendação determinística por menor custo total conhecido na
moeda da missão, com evidências históricas e vendedor opcional, validada no
PostgreSQL real. A TASK-039 (`DEC-027`) implementou a comparação completa das
mesmas evidências, com posição 1 invariável em relação à recomendação e frete
desconhecido sem total, também validada no PostgreSQL real. A TASK-040
(`DEC-028`) implementou confirmação temporária com TTL, proprietário e
observação original, revalidada no PostgreSQL real. A TASK-041 (`DEC-029`)
persistiu a solicitação imutável e sua trilha append-only, com recuperação,
idempotência e concorrência reais. A TASK-045 (`DEC-031`) adicionou métricas
Prometheus, traces OTLP/Jaeger, correlação segura e health/readiness. A
TASK-046 (`DEC-032`) passou a aceitar operações do Telegram somente após
autenticar o transporte, validar o chat privado direto e resolver um usuário
ativo. A TASK-047 (`DEC-034`) aplicou autorização fail-closed com papel único,
herança `USER ⊂ ADMIN ⊂ DEV` e ownership obrigatório. A TASK-061
(`DEC-035`) acrescentou Argon2id, tokens de 10 minutos, sessões absolutas de
12 horas e recuperação pelo Telegram vinculado. A TASK-048 (`DEC-036`) moveu
os secrets para arquivos por serviço, adicionou Gitleaks reproduzível e validou
rotação manual do PostgreSQL. A TASK-049 (`DEC-037`) adicionou limite HTTP,
replay/rate limit persistentes, retry apenas seguro, circuit breakers locais
por integração e dead letter append-only. A TASK-050 (`DEC-038`) removeu PII
de logs, limitou a retenção de telemetria e implementou desidentificação
fail-closed preservando UUID/históricos. A TASK-051 (`DEC-039`) consolidou o
runbook do Ubuntu Server, tornou as portas administrativas privadas por padrão
e validou backup/restauração manual sem alegar disaster recovery. A TASK-052
(`DEC-040`) criou a suíte permanente e fail-closed em PostgreSQL 18.4
descartável, agora obrigatória no pipeline. O preflight da TASK-053 confirmou
que não existia processo ligando agendas, providers, observações e eventos; a
TASK-062 foi criada como requisito funcional do MVP (`DEC-041`) e concluiu a
orquestração automática. A TASK-053 refez a suíte reproduzível e o E2E
externo depois da disponibilidade por card, do DEC-045, do DEC-046 e do
DEC-047, obteve `PASS` nos dois modos em 2026-08-09 e foi encerrada com
aprovação explícita do usuário; a falha isolada da Pichau no E2E externo é
uma condição externa observada, não um bug interno pendente. A TASK-054
fechou o checklist de release e publicou o tag `v1.0.0` em `origin`, só como
marco revisado (sem deploy real, sem CI/CD, sem GitHub Release pública, por
decisão explícita do usuário). A TASK-063 (`DEC-048`) foi registrada em
seguida, ainda em 2026-08-09, depois de o usuário identificar no Telegram
real que alertas podiam ser irrelevantes ao produto pedido e usavam o nome
da missão em vez do anúncio real, sem link direto. A TASK-063 está
**concluída**: relevância `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`, correção do
bug de `previous` compartilhado entre missões, título/loja/link reais no
alerta e formatação revisada das mensagens principais, tudo validado
(pipeline, E2E reproduzível, missão real) e aprovado explicitamente. A
validação real revelou que a camada premium da cascata ADMIN/DEV
(`gemini-3.1-pro-preview`) teve 0% de sucesso sob carga — desmembrado para
a TASK-064 (`DEC-049`/`DEC-050`). A TASK-064 teve escopo final decidido
pelo usuário (Flash único para USER/ADMIN/DEV, sem nível Pro/preview,
fallback só por disponibilidade) e está **concluída**, aprovada
explicitamente pelo usuário em 2026-08-10: `AdminDevAIProviderManager`
colapsado para 2 camadas (Flash→Groq), validado com pipeline oficial, E2E
reproduzível e chamadas reais (fallback Flash→Groq real confirmado; coleta
representativa com 15/20 sucesso em classificação e em normalização,
melhora real sobre a maioria de falhas da TASK-063; falhas restantes do
Flash por cota registradas como condição operacional externa). Com a
TASK-064 fechada, a condição que suspendia `v1.0.0` como release final
está resolvida (`docs/releases/checklist.md`, 65/65); o tag continua
publicado sem alteração e deploy real segue fora do escopo até decisão
explícita futura. A
TASK-057 (`DEC-017`): validação real contra o
`USER`/Gemini cobre 3 dos 4 `IntentKind`, e o usuário aceitou explicitamente
encerrar nesse estado, adiando mais variedade de linguagem para a V2
(`docs/tasks/TASK-057.md`, `docs/internal/backlog.md`). A TASK-058 (`DEC-015`):
`create_mission`/`mission_command` ficam encenados e só executam após
confirmação via classificador de IA dedicado, validado de ponta a ponta
contra o Telegram real. A TASK-059 (`DEC-016`): Groq como fallback opcional
do `ADMIN/DEV` e perfil configurável de validação do `IntentInterpreter`. A
TASK-060 (`DEC-018`): webhook escolhe o perfil de IA a partir de
`User.role`, `/cadastro` e `/upgrade` (inativo), validados de ponta a ponta
contra o Telegram real. A TASK-043 (`DEC-022`): publicação durável de
eventos — tabela `events` append-only e serviço genérico `publish_event`,
validado contra PostgreSQL real com candidatos reais de
`evaluate_price_alerts` (TASK-027); não inclui detecção dos demais tipos de
evento nem qualquer worker/consumidor. A TASK-044 (`DEC-023`): histórico
append-only de tentativas e reivindicação concorrente com
`FOR UPDATE SKIP LOCKED`, validada em PostgreSQL real; não inclui worker,
backoff, dead-letter queue ou notificação em seu próprio escopo — o consumidor
e a notificação foram adicionados depois pela TASK-036. A TASK-061 (`DEC-019`,
autenticação real por usuário e senha) foi retirada da V1.2 e integrada ao
fluxo principal depois da TASK-047 (`DEC-033`).
As demais continuam pendentes e só podem ser iniciadas por solicitação
explícita. A
V1 pesquisa Pichau, Terabyte, Amazon e Kabum; Mercado Livre, Shopee e
AliExpress permanecem futuras.

A ordem de execução é a ordem apresentada nesta tabela; a numeração da TASK é um identificador estável e não substitui dependências explícitas.

Toda TASK do roadmap segue o workflow oficial e permanente definido em
`AGENTS.md`, incluindo validação prévia, testes, revisão técnica, sincronização
documental, commit convencional, publicação automática da branch da TASK e
atualização da `main` local. A `main` remota só é atualizada após solicitação
explícita do usuário.

O escopo obrigatório da V1 está em `docs/internal/mvp.md`. Evoluções futuras devem ser registradas em `docs/internal/backlog.md`, e exclusões explícitas da V1 estão em `docs/internal/out-of-scope.md`. Uma lista priorizada de evoluções para depois da V1 e antes da V2 está em `docs/internal/v1.2-scope.md` (`DEC-021`); a TASK-061 não faz mais parte dessa lista (`DEC-033`).

Ordem de versões registrada: `v1.0.1` (release atual, **já implantada em
produção real** — 7 serviços, migrations no head, Telegram ativo,
proprietário promovido a `DEV`) → `v1.0.2` (release corretiva, documento
próprio `docs/internal/v1.0.2-scope.md` — não confundir com V1.2 —, escopo original de
cinco itens: dois de configuração/infraestrutura — `DEC-052` — e três
adicionados depois por pedido explícito do usuário apesar de fugirem
desse escopo original, sinalizado no próprio doc — editar missão
existente (`DEC-057`), categorias numeradas no `/cadastro` (`DEC-055`) e
pré-lista de preços sem IA, um preço por loja (`DEC-058`); e dois itens
adicionados após a TASK-069 concluir (`DEC-060`) — perguntar as lojas
por lista numerada quando uma missão for criada sem nenhuma informada, e
impedir `/cadastro` para usuário já autenticado; **status 2026-08-11:
os 7 itens estão implementados e validados** — TASK-065 (item 1),
TASK-066 (item 2, restart policy), TASK-067 (item 4, categorias
numeradas), TASK-068 (item 5, pré-lista — escopo final revisado para
top-2 mais baratas + correção única, ver `docs/tasks/TASK-068.md`),
TASK-069 (item 3, editar missão existente — só `PAUSED` é editável,
`ACTIVE` oferece pausar primeiro, ver `docs/tasks/TASK-069.md`),
TASK-070 (item 7, lista numerada própria — `1 Pichau/2 Terabyte/3
Amazon/4 Kabum/5 Todas`, distinta da ordem do `/cadastro`, ver
`docs/tasks/TASK-070.md`) e TASK-072 (item 6, `/cadastro` bloqueado com
sessão ativa + checagem antecipada de username duplicado, ver
`docs/tasks/TASK-072.md`) **estão concluídas — todo o escopo registrado
da `v1.0.2` está concluído**; tag `v1.0.2` (`ea653b8`) criada, publicada
e implantada em produção em 2026-08-11) → `v1.0.3` (release corretiva,
item único TASK-073 — `/cadastro` bloqueado também para cadastro já
concluído sem sessão ativa, complementando a TASK-072, ver
`docs/tasks/TASK-073.md` — encontrada durante a validação real do
deploy da `v1.0.2`; concluída, validada e implantada em produção,
tag `v1.0.3` (`6fa5e13`), em 2026-08-11) → `v1.0.4` (release corretiva,
item único TASK-074 — `search_query` do `IntentInterpreter` passa a
corrigir digitação óbvia e completar marca/modelo reconhecível, sem
inventar especificação nova, ver `docs/tasks/TASK-074.md` —
encontrada durante a mesma validação real; concluída e validada com
pipeline oficial e chamadas reais ao perfil `ADMIN`) → `v1.0.5`
(release corretiva, TASK-075 — canonicalização completa + campo
estruturado `model` no `IntentInterpreter`, filtros determinísticos de
modelo/bundle e regra exclusiva de menor preço da Amazon antes de
persistir/chamar IA, ver `docs/tasks/TASK-075.md` — encontrada durante
a mesma validação real, estouro de cota de IA por volume de candidatos
irrelevantes; mais a correção de readiness da Pichau encontrada na
validação real do teste controlado em produção — `domcontentloaded`
(22-38s, às vezes >45s) trocado por `wait_until="commit"` + espera
pelo card real ou pelo estado legítimo de "zero resultados" (9-12s),
com o timeout de navegação do browser desacoplado do timeout de
chamadas de API/IA (`AISHOPPING_BROWSER_NAVIGATION_TIMEOUT_SECONDS=45`
vs `AISHOPPING_EXTERNAL_HTTP_TIMEOUT_SECONDS=10`), documentada em
`docs/tasks/TASK-075.md`; **concluída e validada** com pipeline
oficial, migration real, chamadas reais ao perfil `ADMIN` e missão
real em produção (`5800X3D`, 4 lojas `succeeded`, pré-lista correta
Amazon+Kabum por serem as mais baratas)) → `v1.0.6` (release corretiva,
**em andamento — TASK-076, TASK-079, TASK-086 e TASK-087 concluídas e
validadas; permanecem pendentes TASK-077 e TASK-084** — dois itens originais: TASK-076
(`docs/tasks/TASK-076.md`, observabilidade — enriquecer os logs de
falha dos providers com tipo/status/traceback da exceção original, hoje
descartados no ponto em que `_process` já os tem em escopo; achada
durante o próprio diagnóstico da correção da Pichau na `v1.0.5`, que só
foi possível reproduzindo a falha isolada por falta dessa informação no
log) e TASK-077 (`docs/tasks/TASK-077.md`, distinguir "vendido pela
Amazon" de "loja parceira Amazon" nos cards de busca, sem catalogar
vendedores terceiros nem mudar a regra de menor preço da TASK-075) —
nenhuma das duas altera comportamento de retry, classificação de falha,
filtros, IA, Telegram ou a regra de menor preço da Amazon; **as
decisões arquiteturais das duas já foram aprovadas explicitamente pelo
usuário** (TASK-076: captura de traceback só local, sem tocar o
`JsonFormatter` compartilhado; TASK-077: nova coluna `seller_kind` em
`PriceObservation`, investigação ao vivo obrigatória antes de
codificar) — nenhuma das duas implementada ainda. Um terceiro item foi
adicionado depois: **TASK-078** (`docs/tasks/TASK-078.md`, redesenho da
UX de texto do Telegram — saudação de primeiro contato, `/ajuda`
reorganizado por contexto, novo comando `/missao`, fallback menos seco
para pedido não reconhecido, e fusão de `/senha` em `/recuperar` depois
de confirmado que `/recuperar` sozinho recusaria criar a primeira senha
se `/senha` fosse só apagado — `_validate_action_state` exige
credencial existente para `RECOVER_PASSWORD`). Decisões já aprovadas;
não implementada. **Um quarto item, TASK-079
(`docs/tasks/TASK-079.md`), foi adicionado depois e é a PRIMEIRA
PRIORIDADE de implementação da `v1.0.6`, executada antes de
TASK-076/077/078** — achada durante a validação real da missão
"cadeira gamer": o `collection_worker` inteiro travou (não só essa
missão) com 15 processos Chromium/Xvfb zumbis, sem exceção registrada;
hipótese forte (ainda não comprovada) de falta de init real como PID 1
do container para recolher subprocessos órfãos do Playwright/Chromium.
Diagnóstico completo, reprodução controlada e comparação objetiva
sem/com `init: true` antes de declarar causa raiz ou implementar
qualquer correção — autorizado a trabalhar diretamente em produção para
isso, preservando banco/dados/secrets/rollback.

**TASK-079 concluída e validada** (commit `4728857`, `collection_worker`;
commit `0afac04`, extensão do webhook Telegram — mesma classe estrutural
de autodeadlock, ver `docs/tasks/TASK-079.md`) — validada ao vivo em
produção depois do redeploy completo do zero em servidor novo (Windows
Server 2025 + Docker Desktop): stack saudável, webhook Telegram
funcional, `/start` real processado sem erro.

**Rodada de auditoria/planejamento pós-deploy (2026-08-13)**, sem
nenhuma implementação — só auditoria, TASKs e documentação — acrescentou
seis itens à `v1.0.6`, nesta ordem de prioridade recomendada de
implementação (`docs/tasks/TASK-079.md`, seção "Auditoria sistemática
adicional", é o item 1 desta lista, já coberto acima):

2. **TASK-080** (`docs/tasks/TASK-080.md`) — `telegram_notifier` mantém
   transação síncrona aberta durante `await send_message` (mesma classe
   estrutural da TASK-079, sem evidência de reprodução do autodeadlock
   real — processamento sequencial, query com `SKIP LOCKED`); risco
   residual real é a ausência de timeouts defensivos de Postgres nessa
   conexão. Três opções de tratamento registradas, nenhuma escolhida.
3. **TASK-081** (`docs/tasks/TASK-081.md`) — zumbis Chromium/Playwright
   no `collection_worker`, problema separado do autodeadlock (já
   descartado como causa pela própria TASK-079). Reproduzido de novo,
   ao vivo, no servidor novo (15 zumbis depois de um único ciclo
   abrir/fechar Chromium); auditoria de código não encontrou bug de
   cleanup no projeto (`BrowserSession.close()` já é exception-safe) —
   reforça, sem provar, a hipótese de reaping do PID 1. Plano de
   investigação completo (comparação objetiva sem/com `init: true`)
   registrado, nenhuma correção implementada.
4. **TASK-082** (`docs/tasks/TASK-082.md`) — buscas genéricas (ex.:
   "cadeira gamer") não têm hoje nenhuma redução de candidatos antes da
   persistência/IA, porque os filtros da TASK-075 (modelo + menor preço
   da Amazon) só ativam com `criteria.model` preenchido. Nenhum número
   de limite escolhido — decisão explícita pendente do usuário.
5. **TASK-083** (`docs/tasks/TASK-083.md`) — canonicalização de produto
   pela IA não é confiável de forma determinística (caso real: `9800X3D`
   recebeu `Ryzen 7` corretamente na validação da TASK-075 e `Ryzen 9`
   incorretamente numa interação posterior, mesma entrada). Quatro
   opções levantadas (confiança auto-relatada, grounding do Gemini,
   validação determinística por padrão de nomenclatura, combinação),
   nenhuma escolhida; caso de regressão obrigatório documentado.
6. **TASK-084 concluída** (`docs/tasks/TASK-084.md`) — imagem persistente por
   Offer, uma oferta por mensagem, fallback textual de mídia, link curto próprio
   fail-closed e checkpoint por evento/oferta/parte.
7. **TASK-085** (`docs/tasks/TASK-085.md`) — seleção numérica única e
   múltipla para cancelar/pausar missões ambíguas; nenhuma TASK anterior
   encontrada sobre o assunto (verificado nesta rodada); reaproveita
   componentes já existentes das TASK-070/071
   (`parse_numbered_store_selection`, `parse_single_numbered_choice`). A
   infraestrutura genérica só foi conectada ao caminho ambíguo da IA na
   época; a TASK-090 (`docs/tasks/TASK-090.md`) conectou o mesmo código a
   três comandos manuais dedicados (`/cancelar_missao`, `/pausar`,
   `/retomar`), sem IA em nenhum deles.

**TASK-083 concluída e validada (2026-08-14)**, commit local `d86587e` —
verificação em camadas implementada (gatilho determinístico +
`model_confidence`, grounding dedicado via `AIProviderManager`/
`gemini-2.5-flash`, fallback seguro nunca mantendo canonicalização
suspeita, `ProductIdentityResolver` Kabum→Amazon rodando só no
`collection_worker`, fora do webhook e fora de transação, uma vez por
`mission_id`); ver `docs/tasks/TASK-083.md` para arquitetura completa e
resultado da validação real (grounding bloqueado nesta chave/projeto:
`gemini-2.5-flash` e `gemini-2.5-flash-lite` aparecem no catálogo mas
`generate_content` recusa os dois com `404` — causa exata não comprovada
—, fallback via `ProductIdentityResolver` real confirmado: Kabum resolveu
`9800X3D` → `"Processador AMD Ryzen 7 9800X3D"` na primeira tentativa).

**TASK-082 concluída (2026-08-14)** — limite de 3 candidatos por loja em
busca genérica (`criteria.model is None`), derivado por analogia de
`availability_fallback_max_candidates` (mesmo pipeline, TASK-075) já que
nenhum número havia sido fixado na auditoria original; reaproveita o
princípio de menor preço + desempate determinístico já comprovado pela
regra da Amazon. Busca específica preservada sem nenhuma alteração; ver
`docs/tasks/TASK-082.md`. Só validação não-integração (suíte + ruff) —
sem chamada real, missão ou rebuild Docker, por instrução explícita.

**TASK-085 concluída (2026-08-14)** — seleção numérica única/múltipla
(`"1"`/`"1,3"`) para `mission_command` ambíguo (`cancel`/`pause`/etc.),
determinística sem IA, mapeamento gravado em `pending_intent` no
momento da listagem, processamento item a item nunca tudo-ou-nada
(✅/⚠️/❌ por missão); resposta numérica já é a confirmação, sem
intercalar o par confirmar/cancelar da TASK-058. Ver
`docs/tasks/TASK-085.md`. Só validação não-integração (suíte + ruff).

**TASK-078 concluída (2026-08-14)** — UX do Telegram simplificada:
boas-vindas diferentes para primeiro contato (`created_now`, sem
consulta nova) vs. retorno sem sessão; `/senha` deixou de existir como
comando público, fundido em `/recuperar` (escolhe `SET_PASSWORD`/
`RECOVER_PASSWORD` pela presença de `UserCredential`, nunca
`CHANGE_PASSWORD`, TTL/rate limit/política inalterados); `/ajuda`
reorganizado por grupos; `/missao` novo com exemplos concretos; menu
final atualizado. Ver `docs/tasks/TASK-078.md`. Só validação
não-integração (suíte + ruff).

Nenhum push/tag feito para nenhuma das quatro na época deste registro.
**Atualização posterior**: TASK-080 (`docs/tasks/TASK-080.md`, commit
`68d8528`) e TASK-081 (`docs/tasks/TASK-081.md`, commit `ac53902`) estão
**concluídas e validadas em runtime**, ambas já publicadas em
`origin/main` (confirmado por `git merge-base --is-ancestor`). TASK-084
também concluída (ver linha própria da tabela acima).)
→ V1.2
(evolução funcional, documento `docs/internal/v1.2-scope.md`; reorganizada em
2026-08-21, ampliada e reordenada até a `DEC-082` para 18 itens
ordenados com **objetivo central de
transformar o AIShoppingAgent numa aplicação web completa** de monitoramento
e comparação de preços — duas áreas na mesma aplicação/backend/banco,
`/app` para USER e `/admin` para DEV/ADMIN, autorização real no backend —
mantendo o Telegram principalmente como canal de alertas. Itens já
aprovados anteriormente foram preservados e renumerados (Magalu, Mercado Livre
e Shopee como novas lojas, redução de `PriceObservation` redundante,
comparação de menor preço histórico externo/interno estilo Steam Inventory
Helper e pesquisa de cupons — `DEC-053`/`DEC-054`/`DEC-056`/`DEC-080`);
frete/parcelamento autenticado e ofertas em lives foram movidos para a V2;
os itens de e-mail
(opt-in no cadastro e notificações por e-mail) saíram da V1.2 e foram
movidos para a V2 (`docs/internal/backlog.md`). **Estado em 2026-08-27**
(ver `docs/internal/v1.2-scope.md` e a tabela acima para o detalhe por
TASK): itens 1-9 (TASK-091 a TASK-100), TASK-101 (minha conta), TASK-102
(dashboard DEV/ADMIN) e TASK-103/item 14 (comparação entre lojas) —
todos concluídos e publicados em `origin/main`. TASK-104A/B (novas
lojas) publicadas; TASK-104C adiada. TASK-105/107/108/109/111 publicadas;
TASK-112 fases 1/2/3A publicadas, fase 3B concluída localmente ainda não
publicada. TASK-110/113 concluídas e commitadas localmente, publicação
pendente (item 17 absorvido e implementado pela TASK-113). TASK-106 em
pausa; **TASK-098 (último item ativo da V1.2, item 18) concluída no
DEV**, publicação em `origin/main` pendente. Nenhuma TASK ativa de
implementação da V1.2 permanece em aberto — só a publicação acumulada.)
→ V2.
