# Changelog

## 2026-08-10 (3) — TASK-066 concluída: restart policy uniforme nos 7 serviços

- **TASK-066** (`docs/tasks/TASK-066.md`, item 2 da `v1.0.2`) concluída:
  auditoria serviço por serviço encontrou que a política parcial de
  restart já era contraditória — `collection_worker`/`telegram_notifier`
  tinham `restart: unless-stopped` mas dependem de `database`, que não
  tinha, tornando o auto-restart deles parcialmente inútil após um reboot
  real. O usuário aprovou explicitamente aplicar `restart: unless-stopped`
  aos 5 serviços restantes (`database`, `api`, `otel-collector`,
  `prometheus`, `jaeger`), deixando os 7 serviços de `compose.yaml`
  consistentes.
- **Validação real**: além do pipeline oficial completo, subi
  `database`/`jaeger` localmente (ambiente isolado, sem tocar produção),
  simulei um crash real do processo interno (`docker exec ... kill -9 1`,
  distinto de um `stop` manual, que `unless-stopped` intencionalmente não
  reinicia) e confirmei recuperação automática em segundos.
- `docs/PRODUCTION_SETUP.md` atualizado (seções "Serviços" e "Inicialização
  após reboot"). Produção da `v1.0.1` já implantada não foi alterada;
  aplicar a mudança lá fica para uma atualização operacional futura.
  Nenhuma tag `v1.0.2` criada; TASK-067 não iniciada.

## 2026-08-10 (2) — TASK-065 concluída: remoção de variáveis de modelo obsoletas

- **`v1.0.2` entrou em planejamento ativo** e os 5 itens de
  `docs/V1_0_2.md` foram convertidos em propostas de TASK (TASK-065 a
  TASK-069), aprovadas para execução uma de cada vez.
- **TASK-065** (`docs/tasks/TASK-065.md`, item 1) concluída: auditoria
  reconfirmou que `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` não
  são propagadas por nenhum dos 7 serviços de `compose.yaml` — o nome do
  modelo em produção sempre veio do default hardcoded em
  `Settings.gemini_model`/`Settings.groq_model`. Removidas de
  `.env.example` (raiz), `backend/.env.example` e do `backend/.env` local
  (limpeza não versionada, sem expor valores). `docs/DEPENDENCIES.md` e
  `docs/PRODUCTION_SETUP.md` atualizados para não anunciar essas duas
  variáveis como configuráveis via `.env`/Compose. `manager.py`, a
  cascata Gemini Flash → Groq (`DEC-050`), `compose.yaml` e a produção da
  `v1.0.1` já implantada **não foram alterados**. Nenhuma tag `v1.0.2`
  criada; TASK-066 não iniciada.

## 2026-08-10 — `v1.0.1` implantada em produção real; planejamento de `v1.0.2`/V1.2 ampliado

- **`v1.0.1` implantada num servidor de produção real**: build das imagens,
  PostgreSQL subido, 26 migrations aplicadas até `20260809_0004` (head), os
  7 serviços do `compose.yaml` saudáveis. Webhook do Telegram registrado
  sobre uma URL HTTPS pública real; cadastro, criação de senha, login e
  criação de missão por texto livre validados ao vivo, com IA (Gemini
  Flash e fallback Groq) respondendo corretamente e sem carga alta. O
  proprietário foi promovido a `DEV` pelo procedimento manual documentado
  em `docs/PRODUCTION_SETUP.md` (seção 9).
- **Achado real de implantação, corrigido**: os containers da aplicação
  rodam como usuário não-root (UID 999 dentro da imagem); os arquivos de
  `.secrets/` inicialmente ficaram com dono do usuário do host e ficaram
  ilegíveis para os containers. Corrigido só com permissão de arquivo no
  servidor (`chown` para o UID do container) — nenhum código,
  `compose.yaml` ou `Dockerfile` alterado. Não aparecia em desenvolvimento
  porque o Docker Desktop no Windows não aplica permissão POSIX real em
  bind mounts como um host Linux real aplica; vale registrar como nota
  operacional futura em `docs/PRODUCTION_SETUP.md`.
- **`docs/V1_0_2.md` criado**, separado de `docs/V1_2.md` — a `v1.0.2`
  (release *patch* corretiva) e a V1.2 (fase funcional maior) são versões
  distintas e agora vivem em documentos próprios, para não confundir uma
  com a outra (`DEC-059`). Nenhuma TASK criada, nenhum código alterado em
  nenhum dos seis itens novos desta sessão:
  - **`docs/V1_0_2.md`** ganhou três itens além dos dois originais do
    `DEC-052`: editar missão existente sem precisar recriá-la — lojas
    e/ou preço-alvo (`DEC-057`); trocar o texto livre de categorias do
    `/cadastro` por lista numerada, no mesmo padrão já usado pelas lojas
    favoritas (`DEC-055`); e uma pré-lista de preços encontrados **sem
    IA**, mostrando só 1 preço por loja selecionada, puramente
    informativa (`DEC-058`). Todos os três sinalizados explicitamente no
    documento como fora do escopo original "configuração/infraestrutura,
    sem funcionalidade nova" — registrados mesmo assim por pedido
    explícito do usuário.
  - **`docs/V1_2.md`** (evolução funcional) ganhou três itens: reduzir
    `PriceObservation` redundante gravando só quando o estado observado da
    oferta mudar de verdade (preço, disponibilidade, frete, moeda,
    modalidade de envio), nunca apagando histórico já gravado
    (`DEC-053`); Magalu como quinta loja pesquisável, mesma arquitetura de
    Store Provider já aprovada (`DEC-054`); e uma capacidade de comparação
    de menor preço histórico — externo (pesquisa fora do que o app já
    coleta, com fonte/URL/data verificáveis) e interno
    (`price_observations`) — com regra fundamental de que a IA nunca
    inventa preço/data/loja/fonte/URL, só interpreta fatos encontrados;
    essa é a "fase com IA" por cima da pré-lista sem IA da `v1.0.2`; mais
    pesquisa de ofertas em lives, por enquanto limitada a YouTube e Shopee
    Live (`DEC-056`).
- `v1.0.0` permanece intocada; `v1.0.1` continua sendo a release corrente.

## 2026-08-10 — Release `v1.0.1` preparada — corrige a divergência da `v1.0.0`

- Auditoria identificou que a tag `v1.0.0` (`85b56c6`) permaneceu apontando
  para o commit de fechamento da TASK-054 (2026-08-09) e **nunca foi
  atualizada** — não contém as correções da TASK-063 (relevância/
  apresentação dos alertas) nem da TASK-064 (cascata Gemini Flash → Groq),
  concluídas depois. O `docs/RELEASE_CHECKLIST.md` marcado como "65/65"
  descrevia o código corrente (branch/`main` local), não o conteúdo
  efetivamente publicado sob a tag — inconsistência corrigida por esta
  entrada.
- Preparado o commit de documentação para a tag **`v1.0.1`**, release
  corretiva sobre a `v1.0.0`, cobrindo TASK-063 e TASK-064. `v1.0.0`
  permanece **intocada** — não foi movida, apagada nem recriada — como
  marco histórico anterior às duas correções.
- Documentos sincronizados: `docs/RELEASE_CHECKLIST.md` (65/65 associado à
  `v1.0.1`, `v1.0.0` marcada como histórica), `docs/tasks/TASK-054.md`
  (nota posterior sobre a supersessão), `docs/DECISION_LOG.md` (decisão de
  versionamento registrada).
- Publicação da tag e atualização de `origin/main` **aguardam autorização
  final explícita do usuário** — nada foi publicado ainda nesta entrada.

## 2026-08-10 — TASK-064 concluída — release deixa de estar suspensa

- **TASK-064 concluída**, aprovada explicitamente pelo usuário. Registro do
  fechamento: `USER`, `ADMIN` e `DEV` usam Gemini Flash
  (`Settings.gemini_model`) nas operações automáticas de IA; cascata
  oficial da V1 é Gemini Flash → Groq; os modelos Pro/Preview
  (`gemini-3.1-pro-preview`, `gemini-pro-latest`) foram removidos dessa
  função; o fallback real Flash→Groq foi validado com chamadas reais; a
  validação representativa obteve 15/20 classificações
  (`classify_offer_relevance`) e 15/20 normalizações
  (`normalize_offer_title`) com sucesso; as falhas restantes do Flash por
  cota ficam registradas como condição operacional externa, não como falha
  desta TASK. Papel continua sendo só permissão/autorização. Batching não
  implementado. Semântica `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH` da TASK-063
  intocada.
- `docs/RELEASE_CHECKLIST.md` atualizado: critério 6 do MVP volta a
  `✅ Atendido`, checklist 65/65, 0 pendentes.
- Com a TASK-063 e a TASK-064 concluídas, **a condição que suspendia
  `v1.0.0` como release final está resolvida**. O tag `v1.0.0` não foi
  alterado nem recriado; `main`/`origin/main` não foram tocados; deploy
  real num Ubuntu Server continua fora do escopo até decisão explícita
  futura.

## 2026-08-09/2026-08-10 — TASK-064 implementada e validada com chamadas reais — aguardando fechamento

- `AdminDevAIProviderManager` (`backend/app/ai_provider/manager.py`)
  colapsado de 3 para 2 camadas: `gemini-3.6-flash` (mesmo modelo do
  perfil `USER`, `Settings.gemini_model`) e, se configurado, Groq como
  fallback de disponibilidade. `Settings.gemini_premium_model`/
  `AISHOPPING_GEMINI_PREMIUM_MODEL` removidos do config e dos
  `.env.example`. Nenhum modelo Gemini Pro/preview participa da cascata
  (DEC-050); `USER`/`ADMIN`/`DEV` usam o mesmo Gemini Flash, distinção
  continua sendo só permissão/autorização.
- Pipeline oficial aprovado (752 testes, 90,63% cobertura, 14 integrações
  PostgreSQL reais, migration head inalterada) e E2E reproduzível 2/2.
- Validação real (stack Docker reconstruído): fallback Flash→Groq
  confirmado com chamadas reais (Groq respondendo quando o Flash falha por
  `quota_exceeded`/`unavailable`, circuit breaker da TASK-049 funcionando
  como esperado); uma coleta pequena representativa (missão descartável,
  uma única fonte, 20 ofertas novas) confirmou melhora real na taxa de
  classificação — 75% de sucesso (`classify_offer_relevance` e
  `normalize_offer_title`), contra a maioria de falhas documentada na
  validação original da TASK-063 sob a cascata de 3 camadas. Nenhuma
  referência a `gemini-3.1-pro-preview`/`gemini-pro-latest` em nenhum log.
  Semântica `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH` da TASK-063 intocada;
  batching não implementado (fora do escopo desta TASK).
- **Aguardando aprovação explícita do usuário para fechar a TASK-064.**
  `v1.0.0` (TASK-054) não foi tocada; `main`/`origin/main` não foram
  tocados. A release continua sem ser tratada como definitiva até o
  fechamento formal.

## 2026-08-09 — TASK-063 concluída; TASK-064 registrada — release segue suspensa

- TASK-063 (relevância dos alertas e formatação do Telegram) **concluída**
  com aprovação explícita do usuário: classificador
  `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH` por `(mission_id, offer_id)` (só
  `MATCH` alerta), correção do bug de `previous` compartilhado entre
  missões, `products.display_name` normalizado por IA, título/loja/link
  reais no alerta (nunca o nome da missão) e formatação revisada das
  mensagens principais do Telegram (`app/telegram/formatting.py`,
  `MissionStatus` deixou de vazar em inglês). Migration `20260809_0004`.
  Pipeline (753 testes, 90,64% cobertura, 14 integrações reais), E2E
  reproduzível (2/2) e missão real no Telegram validados.
- A validação real revelou a camada premium da cascata ADMIN/DEV
  (`gemini-3.1-pro-preview`) com 0% de sucesso em 248 tentativas reais.
  TASK-064 criada (`DEC-049`) para revisar disponibilidade/fallback dos
  provedores de IA. Auditoria concluída: o modelo é oficialmente
  `preview`; teste mínimo mostrou um candidato GA "Pro"
  (`gemini-pro-latest`) também falhando com `quota_exceeded`, enquanto um
  GA "Flash" (`gemini-3.5-flash`) respondeu normalmente — mais consistente
  com falta de cota "Pro" na chave do que com um problema pontual do
  modelo. Duas propostas de cascata registradas; nenhum código alterado.
- `v1.0.0` (TASK-054) não foi tocada. A release continua sem ser tratada
  como definitiva até a TASK-064 fechar.

## 2026-08-09 — TASK-063: registrada e auditada — release deixa de ser definitiva

- Usuário identificou no Telegram real que alertas de preço podiam ser
  irrelevantes ao produto pedido, mostravam o nome da missão em vez do
  anúncio real e não exibiam link direto. TASK-063 criada (`DEC-048`).
- Auditoria completa do fluxo `StoreProvider → Product/Offer →
  PriceObservation → evaluator → evento → telegram_notifier`
  (`docs/tasks/TASK-063.md`) confirmou causa raiz em código: `Offer.url` já
  é correta; `Product.name` guarda título bruto não normalizado só na
  primeira coleta da oferta; `telegram/notifications.py` nunca busca
  `Offer`/`Product`/`Store` a partir do `offer_id` já presente no evento,
  usando só `mission.title`; e não existe nenhum filtro de correspondência
  entre o resultado da busca do site e o produto pedido pela missão.
- Nenhum código alterado. Implementação aguarda autorização explícita do
  usuário, incluindo a regra para classificação `POSSIBLE_MATCH`.
- Tag `v1.0.0` (TASK-054) **não foi alterada nem recriada**; `main`/
  `origin/main` não foram tocados. A release deixa de ser tratada como
  estado final da V1 até a TASK-063 fechar.

## 2026-08-09 — TASK-054: release `v1.0.0` — MVP da V1 completo

- `docs/RELEASE_CHECKLIST.md` fechado como retrato real do repositório:
  63/63 tarefas planejadas concluídas, 8/8 critérios objetivos do MVP
  (`docs/MVP.md`) atendidos.
- Tag Git anotado `v1.0.0` criado sobre o commit revisado e publicado em
  `origin`. Por decisão explícita do usuário: só o tag como marco revisado
  (permite `docs/OPERATIONS.md` fazer checkout por tag no futuro) — sem
  deploy real num Ubuntu Server, sem CI/CD e sem GitHub Release pública.
  Deploy real fica para quando o servidor estiver provisionado.
- `AGENTS.md`, `docs/ROADMAP.md`, `docs/PROJECT_CONTEXT.md` e
  `docs/tasks/README.md` sincronizados: MVP da V1 completo, sem próxima TASK
  do roadmap pendente. Evoluções (V1.2/V2) exigem decisão explícita futura.

## 2026-08-09 — TASK-053: E2E reproduzível e externo refeitos, `PASS`

- Correção do gatilho de backoff persistente (`DEC-047`): `401` removido do
  conjunto que aciona backoff por fonte; tratamento da chamada em si continua
  cobrindo `401/403/429` normalmente.
- E2E reproduzível refeito com o código atual (disponibilidade por card,
  `DEC-046`, `DEC-047`). Achado real: `staggered_next_run_at` (`DEC-046`)
  desloca `next_run_at` em até 5 min já na criação da missão; o cenário E2E
  não fixava esse valor e passou a falhar de forma não determinística.
  Corrigido só no teste (`tests/e2e/test_critical_flow.py`), sem mudança de
  produto. 2/2 cenários aprovados.
- E2E externo refeito com missão real criada pelo usuário via Telegram real,
  quatro fontes, `collection_worker` real sem chamada manual. Resultado:
  `PASS` — Amazon e Terabyte 100% `AVAILABLE`; Kabum resolveu 3 ofertas via
  fallback seletivo (top K=3) e manteve 17 `UNKNOWN` dentro da regra; Pichau
  falhou por instabilidade externa isolada (`provider_unavailable` →
  `circuit_open`), sem bloqueio `403/429` confirmado, corretamente sem
  acionar o backoff persistente do `DEC-047`. 58 observações, 41 elegíveis,
  11 eventos de alvo, 11 notificações Telegram reais entregues sem
  duplicação.
- TASK-053 **concluída**, com fechamento aprovado explicitamente pelo
  usuário; a falha isolada da Pichau fica registrada como condição externa
  observada, não como bug interno pendente. TASK-054 é a próxima TASK do
  roadmap, mas não foi iniciada.

## 2026-08-09 — TASK-053: E2E implementado, validação externa bloqueada

- Criada suíte E2E reproduzível separada do pipeline rápido, usando webhook,
  API, PostgreSQL 18.4, workers e consumo durável reais, com fronteiras
  externas controladas. Dois cenários foram aprovados.
- O onboarding passou a oferecer lojas numeradas, aceitar `5` para todas,
  emitir automaticamente o link de senha e usar mínimo de oito caracteres sem
  regra artificial de composição.
- Criação/alteração/recuperação de senha e login agora geram confirmações no
  chat privado. Sessões avisam uma vez antes do vencimento e uma vez ao expirar;
  a revisão `20260809_0002` evita eventos retroativos/duplicados.
- Telegram real confirmou exatamente dois envios de conclusão, um aviso prévio
  e um de expiração; replay do worker manteve um único terminal por evento.
- Quatro Store Providers reais produziram 40 observações. Como o frete depende
  de login nos marketplaces, nenhuma evidência completa ficou elegível: o
  resultado externo é `BLOCKED_EXTERNAL/no_eligible_external_evidence`, não
  aprovação nem falha interna.
- O classificador externo e o registrador de webhook foram corrigidos para
  falhar de modo observável, sem fabricar observações ou eventos.
- Pipeline oficial aprovado com Gitleaks, Ruff, 665 testes rápidos, 90,01% de
  cobertura, 11 integrações PostgreSQL, Alembic head `20260809_0002` e Compose.
- A conversa de orçamento ausente e sugestão de referência de mercado ficou
  reservada à V1.2 (`DEC-044`). A TASK-054 não é liberada enquanto a validação
  externa da TASK-053 permanecer bloqueada.

## 2026-08-09 — TASK-062: orquestração automática de coletas

- Novas missões recebem agenda atômica e missões ativas antigas recebem
  backfill idempotente, sem reativar agenda desabilitada.
- `collection_worker` reivindica agendas em transação curta, executa as quatro
  fontes fora do lock e persiste observações/eventos por fonte de forma
  independente.
- Migration `20260809_0001` impede run concorrente duplicado por missão/loja e
  observação duplicada por run/oferta; upgrade, downgrade e novo upgrade foram
  validados no PostgreSQL 18.4 real.
- Runs abandonados são terminalizados como `stale_execution`; resultados
  tardios não persistem. Falhas usam catálogo sanitizado e não revertem fontes
  bem-sucedidas.
- Frete desconhecido não produz alertas dependentes de custo total.
- Docker Linux/Xvfb validou Pichau, Terabyte, Amazon e Kabum reais. O worker
  automático processou quatro fontes, persistiu 20 observações e quatro eventos
  em banco isolado; seu container montou somente o secret do PostgreSQL.
- A próxima tarefa executável passou a ser a TASK-053; a main remota permanece
  fora do fluxo automático.
- Pipeline oficial aprovado no Python 3.14.6 com 638 testes rápidos, 90,04% de
  cobertura, 11 integrações PostgreSQL reais, Ruff, Alembic, Compose e Gitleaks.

## 2026-08-09 — Planejamento da TASK-062: orquestração automática de coletas

- O preflight da TASK-053 confirmou que missões ativas não criam agenda nem são
  consumidas por qualquer worker de coleta.
- A TASK-062 foi classificada como nova TASK obrigatória do MVP e inserida antes
  da TASK-053, sem iniciar implementação funcional.
- O plano audita e reutiliza scheduler, Store Providers, normalização,
  persistência, alertas, event log, notifier, resiliência e observabilidade já
  existentes.
- Concorrência foi desenhada com claim PostgreSQL curto, runs duráveis e nenhum
  lock durante Playwright/HTTP; migration fica limitada a unicidades necessárias
  para runs e observações.
- TASK-053 permanece pendente e deverá exercitar a cadeia real entregue pela
  TASK-062, nunca montar os componentes manualmente.

## 2026-08-08 — TASK-052: suíte permanente de integração PostgreSQL

- Criado runner multiplataforma fail-closed com PostgreSQL 18.4 fixado por
  digest, porta loopback aleatória e recursos sintéticos exclusivos.
- Alembic aplica `upgrade head` dinamicamente, exige head único, confere a
  revisão persistida e executa `alembic check`.
- Cada teste clona um banco limpo do template migrado; não há rollback global,
  dependência de ordem, `.env`, credencial ou banco do operador.
- Oito integrações reais cobrem schema/seeds, missão/preço/evento, concorrência,
  recomendação/compra, autenticação, autorização, resiliência e privacidade.
- Suíte completa repetida, teste individual, falha controlada e guard direto
  foram aprovados; nenhum container ou volume residual permaneceu.
- A integração tornou-se etapa obrigatória do pipeline completo; a TASK-053
  continua exclusivamente responsável pelos testes E2E externos.
- Pipeline aprovado em Python 3.14.6 com 607 testes rápidos, 90,61% de
  cobertura e 8 integrações PostgreSQL reais.

## 2026-08-08 — TASK-051: runbook operacional e recuperação básica

- Criado `docs/OPERATIONS.md` para preparar e operar um único Ubuntu Server,
  incluindo secrets, migrations, Telegram, health/readiness, observabilidade,
  manutenção e diagnóstico.
- API, PostgreSQL, Prometheus e Jaeger agora bindam em `127.0.0.1` por padrão;
  ferramentas administrativas não são orientadas à exposição pública.
- Backup manual foi separado de disaster recovery completo e rollback de
  código foi separado de downgrade de schema destrutivo.
- PostgreSQL 18 real confirmou backup `0600`, restauração em banco limpo,
  migration `20260808_0009`, contagem e dado sintético; o stack isolado também
  validou endpoints e restart.
- Cloudflared ficou explicitamente restrito a desenvolvimento/validação;
  domínio, TLS, reverse proxy e HTTPS permanente continuam pendências da
  implantação real.
- Pipeline aprovado em Python 3.14.6 com 602 testes, 90,61% de cobertura,
  Ruff, Alembic, Gitleaks e Docker Compose.

## 2026-08-08 — TASK-050: privacidade técnica e desidentificação

- Criado inventário de dados, finalidades, terceiros, retenções e limitações em
  `docs/PRIVACY.md`, sem alegar anonimização irreversível ou certificação LGPD.
- `/privacidade` responde em português sem IA nem sessão e foi registrado e
  entregue pela Bot API real sem conteúdo pessoal.
- Telegram IDs saíram dos logs; campos pessoais são filtrados e exceções expõem
  somente a classe segura, sem mensagem ou traceback bruto.
- Logs Docker ficaram limitados a `10m × 5`, Prometheus a 15 dias/2 GB e Jaeger
  a 10.000 traces voláteis em container de 512 MB.
- Action tokens e sessões receberam limpeza manual após 24h/30d, sem scheduler.
- `app.privacy` remove identificadores diretos, autenticação, preferências,
  intenção pendente e textos mutáveis em transação única, preservando UUID e
  fatos append-only. PII em histórico imutável bloqueia toda a operação.
- PostgreSQL real validou limpeza, desidentificação, idempotência e conflito com
  rollback; a conta do proprietário permaneceu intacta.
- Docker real confirmou limites e consumo; canário ficou ausente de logs,
  métricas e traces.
- Pipeline aprovado em Python 3.14.6 com 601 testes, 90,61% de cobertura,
  Ruff, Alembic, Gitleaks e Docker Compose.

## 2026-08-08 — TASK-049: limites, replay e resiliência limitada

- Adicionado limite HTTP de 64 KiB e recibos append-only únicos por `update_id`,
  atômicos com os efeitos aceitos do webhook.
- Implementado rate limit persistente de 20 updates autenticados/minuto por
  usuário; replay, excesso e descarte retornam `204` sem repetir domínio/IA.
- Toda integração ganhou timeout explícito; somente operações seguras recebem
  retry com jitter. Timeout ambíguo de `sendMessage` nunca é repetido cegamente.
- Circuit breakers locais foram separados por Telegram, provider/modelo de IA
  e Store Provider, com uma única sonda half-open.
- A migration `20260808_0009` acrescentou `next_retry_at`,
  `dead_lettered` terminal e `telegram_update_receipts`, preservando histórico
  append-only e unicidade concorrente no PostgreSQL.
- Worker passou a fazer rollback e backoff fora da transação após falha
  inesperada; Prometheus recebeu métricas allowlisted e regras de detecção para
  dead letters/circuitos abertos, sem notificação externa.
- PostgreSQL 18, Docker Linux, API, worker, Prometheus, Jaeger, Telegram real e
  Store Providers reais validaram migração reversível, restart, replay,
  concorrência, falha/recuperação e privacidade.
- Pipeline completo aprovado em Python 3.14.6: 590 testes, 91,38% de cobertura,
  Ruff, Alembic, Gitleaks e Docker Compose válidos.

## 2026-08-08 — TASK-048: secrets por arquivo e varredura reproduzível

- Produção agora aceita credenciais somente por `*_FILE`; conflito com valor
  direto, vazio, arquivo ausente ou multilinha falha fechado.
- Compose monta seis secrets em `/run/secrets` com menor privilégio: API recebe
  seis, worker dois e PostgreSQL um; valores não entram no ambiente.
- API e worker executam como UID não-root, preservando Chromium headless/headed
  e Xvfb no Linux.
- Gitleaks 8.29.1 foi fixado com SHA-256 publicado para Windows/Linux x64 e
  integrado ao pipeline para working tree, versão, histórico e canário.
- Criado provisionamento interativo/migração local sem imprimir valores e
  runbook de rotação segura, distinguindo volume PostgreSQL novo de existente.
- Docker isolado confirmou canários ausentes de inspect, imagem, rootfs,
  filesystem, logs, métricas e Jaeger; rotação real rejeitou a senha antiga e
  aceitou a nova após recriar consumidores.
- Pipeline aprovado com 573 testes, 92,53% de cobertura, Ruff, Alembic,
  Gitleaks e Compose.

## 2026-08-08 — TASK-061: autenticação real por senha

- Criadas credenciais Argon2id, sessões persistentes de 12 horas e tokens
  descartáveis de 10 minutos na migration `20260808_0008`.
- `/senha`, `/entrar`, `/sair` e `/recuperar` usam formulário HTTPS; identidade,
  ação e papel nunca vêm do navegador.
- Troca e recuperação revogam sessões; login, tokens e emissão de recuperação
  possuem limites persistentes sem bloqueio permanente.
- Comandos funcionais agora exigem sessão, preservando onboarding e recuperação
  depois das fronteiras de Telegram e RBAC.
- PostgreSQL 18 confirmou migration reversível, replay e concorrência real;
  API/worker, HTTPS, navegador e Bot API reais foram validados.
- Pipeline aprovado com 553 testes e 92,51% de cobertura; canários não
  apareceram em logs, métricas, traces nem auditoria.

## 2026-08-08 — TASK-047: autorização por papel único e ownership

- Criada política RBAC fail-closed em `app.authorization`, com herança estrita
  `USER ⊂ ADMIN ⊂ DEV`; papéis desconhecidos não recebem permissões.
- O webhook autoriza depois da autenticação da TASK-046 e antes de IA, leitura
  ou mutação. Recusa retorna `204`, não envia resposta nem altera estado
  funcional e acrescenta somente `authorization.denied` sanitizado.
- Novos usuários do Telegram continuam exclusivamente USER; nenhum payload,
  texto, comando ou cadastro pode atribuir ADMIN/DEV ou promover a conta.
- Ownership permanece obrigatório inclusive para DEV. Recomendação e
  comparação passaram a filtrar também `Mission.user_id`; intenção pendente
  adulterada não é executada nem apagada após recusa.
- O único proprietário ADMIN ativo, com vínculo privado do Telegram, foi
  promovido uma vez para DEV por UUID explicitamente validado. A mesma
  transação acrescentou `user.role_changed`; não existe migration, script
  persistente, regra geral ou lógica de startup para essa alteração.
- PostgreSQL 18, API/worker Docker e Telegram Bot API reais aprovaram o fluxo;
  a validação temporária do banco sofreu rollback sem resíduos.
- A validação cruzada detectou o banco persistente ainda em `20260808_0006`;
  aplicado `alembic upgrade head` para `20260808_0007`, corrigindo a ausência
  das tabelas da TASK-041 sem perda de dados, e o fluxo completo de
  recomendação/comparação/confirmação voltou a passar no PostgreSQL real.
- Pipeline completo aprovado em Python 3.14.6: 508 testes, 92,47% de cobertura,
  Ruff, Alembic com head único e Docker Compose válidos.
- O Docker CLI oficial da instalação por usuário foi incluído de forma
  idempotente no PATH persistente do usuário.

## 2026-08-08 — Workflow: TASK-061 depois da TASK-047

- A TASK-061 deixou de pertencer à fila da V1.2 e passou a integrar o fluxo
  principal de segurança.
- A sequência obrigatória agora é `TASK-047 → TASK-061 → TASK-048`, sem
  renumerar nenhuma tarefa.
- Roadmap, checklist de release, contexto, índice de tarefas e V1.2 foram
  sincronizados pela `DEC-033`.

## 2026-08-08 — TASK-046: autenticação mínima do canal Telegram

- Separada a autenticação do transporte, por segredo em tempo constante, da
  aceitação da identidade da pessoa no Telegram.
- Operações agora exigem chat privado direto (`chat.id == message.from.id`) e
  `User.is_active=true` antes de IA, domínio, cadastro, preferências, resposta
  ou atualização do destino.
- Grupo, supergrupo, canal, identidade divergente e conta inativa encerram em
  `204`; logs usam apenas motivo fechado, sem IDs, texto ou payload.
- Usuário/senha, sessão e recuperação continuam na TASK-061; autorização por
  papel continua na TASK-047.
- PostgreSQL 18, API Docker e Telegram reais aprovados. Pipeline completo:
  495 testes, 92,27% de cobertura, Ruff e Compose válidos.

## 2026-08-08 — TASK-045: observabilidade sanitizada e independente

- Adicionados logs JSON correlacionados, UUID seguro de requisição, métricas
  Prometheus de cardinalidade limitada e tracing sanitizado para API, worker e
  PostgreSQL.
- `/health` permanece liveness sem banco; o novo `/ready` executa `SELECT 1`
  com timeout curto e retorna `503` quando o PostgreSQL está indisponível.
- `/metrics` saiu do OpenAPI; `/metrics` e `/health` não geram spans, enquanto
  `/ready` e rotas funcionais continuam observáveis.
- Compose passou a incluir OpenTelemetry Collector 0.157.0, Prometheus 3.12.0
  e Jaeger 2.20.0, com scrape direto, OTLP para traces e regras sem Alertmanager.
- PostgreSQL, API, worker e stack de observabilidade reais aprovaram
  falha/recuperação, scrape, traces, correlação, canários e alerta
  `AIShoppingWorkerUnavailable` em `firing` e depois `inactive`.
- Pipeline completo aprovado em Python 3.14.6: 477 testes, 92,23% de cobertura,
  Ruff e Docker Compose válidos.

## 2026-08-08 — Workflow Git: branch automática e main remota controlada

- Atualizado o workflow permanente (`DEC-030`): após concluir uma TASK, sua
  branch é publicada automaticamente e a `main` local é atualizada.
- Removida a pergunta recorrente para subir a branch da TASK.
- A `main` remota permanece protegida e só recebe push quando o usuário pedir
  explicitamente.

## 2026-08-08 — TASK-041: confirmação persistente e trilha append-only

- Adicionadas `purchase_confirmations` para a solicitação imutável e
  `purchase_trail_entries` para o histórico append-only, sem duplicar status
  mutável e sem executar compra ou ação financeira.
- A revisão `20260808_0007` cria FKs `RESTRICT`, identidade composta,
  constraints monetárias e da matriz de resolução, índices únicos parciais
  para no máximo uma `requested` e um terminal, além de triggers contra
  `UPDATE` e `DELETE`.
- A criação pública grava confirmação e `requested` na mesma transação. A
  recuperação após reinício reconstrói o snapshot original, e a consulta da
  trilha mantém ordenação determinística.
- Corrigida a semântica da TASK-040: a observação original permanece como
  proveniência, mas uma observação mais nova materialmente equivalente não
  causa `stale`; `cancel` independe de TTL e da oferta corrente.
- `confirm` verifica primeiro proprietário/terminal, depois expiração e somente
  dentro do TTL recalcula a evidência. Expiração vence mudança de evidência.
- A inserção terminal usa SAVEPOINT e trata exclusivamente a violação do índice
  terminal: decisão equivalente retorna o vencedor; decisão diferente gera
  conflito. Outros `IntegrityError` continuam propagando.
- PostgreSQL 18 isolado aprovou `upgrade → downgrade → upgrade`, metadata,
  atomicidade, imutabilidade, FKs, recuperação, expiração, equivalência,
  alteração material e concorrência real idêntica/conflitante.
- Pipeline completo aprovado em Python 3.14.6: 461 testes, 92,78% de cobertura,
  Ruff e Alembic com head único. Nenhuma dependência nova foi necessária.

## 2026-08-08 — TASK-040: confirmação temporária vinculada à evidência exata

- Criado `app.purchase.confirmation` com solicitações imutáveis para qualquer
  oferta elegível escolhida pelo proprietário da missão.
- Cada solicitação vincula `mission_id`, `offer_id`, `price_observation_id` e
  `owner_user_id` ao snapshot completo e possui TTL fixo de 15 minutos em UTC.
- A resolução aceita somente `confirm` e `cancel`. Solicitação expirada retorna
  `stale`; outro usuário não pode solicitar nem resolver a confirmação.
- Antes de produzir `confirmed`, a comparação da TASK-039 é recalculada. A
  TASK-041 refinou essa regra para equivalência material, preservando a
  observação original como proveniência sem invalidar uma nova observação
  idêntica.
- A TASK-041 também integrou persistência e definiu `cancel` como independente
  do TTL. Nenhuma confirmação compra, reserva, abre checkout, publica evento ou
  cria auditoria.
- Validação real no PostgreSQL 18 confirmou confirmação, cancelamento,
  expiração, mudança de evidência, bloqueio de proprietário e oferta inelegível,
  com rollback sem resíduos.
- Pipeline completo aprovado em Python 3.14.6: 461 testes, 94,45% de cobertura,
  Ruff, Alembic com head único e Docker Compose válidos. Nenhuma dependência ou
  migration nova foi necessária.

## 2026-08-08 — TASK-039: comparação completa e ordenada de ofertas

- Criado `compare_offers_for_mission`, com posições `1..N` apenas para ofertas
  elegíveis e evidências inelegíveis sem posição, sempre depois do ranking.
- Extraída `rank_eligible_evidence` como regra única para TASK-038 e TASK-039;
  por invariância, a posição 1 da comparação é exatamente a recomendação para
  o mesmo conjunto de dados.
- Elegíveis usam menor custo total, observação mais recente e UUID. Inelegíveis
  são estabilizadas por loja, produto e UUID e nunca são ordenadas por preço.
- Frete desconhecido mantém `amount`, expõe `total_amount=None` e o motivo
  `shipping_unknown`; moedas incompatíveis, indisponibilidade e ausência da
  moeda da missão também permanecem inelegíveis e explicadas.
- Ausência de oferta elegível retorna `insufficient_data`, sem persistência,
  API, Telegram, IA, compra ou ação financeira.
- Validação real no PostgreSQL 18 confirmou a invariância TASK-038/TASK-039,
  ranking consecutivo, histórico, total indisponível e rollback sem resíduos.
- Pipeline completo aprovado em Python 3.14.6: 444 testes, 94,81% de cobertura,
  Ruff, Alembic com head único e Docker Compose válidos. Nenhuma dependência ou
  migration nova foi necessária.

## 2026-08-08 — TASK-038: recomendação determinística por missão

- Corrigido o escopo genérico da TASK-038 (`DEC-026`) e preservadas as
  fronteiras: TASK-039 compara ofertas; TASK-040 confirma compra; TASK-041
  registra a trilha.
- Criado `app.purchase` com contratos imutáveis e
  `recommend_for_mission`: somente missão ativa, coletas bem-sucedidas da
  própria missão e fontes selecionadas entram no recorte.
- A observação mais recente define o estado da oferta. Apenas disponibilidade
  `available`, moeda idêntica à do critério e frete conhecido tornam a oferta
  elegível; frete nulo nunca é zero/grátis e não existe conversão monetária.
- O menor `total_amount` vence deterministicamente, com desempate por recência
  e UUID. O resultado mantém vendedor opcional, evidências das ofertas
  inelegíveis e identificadores do histórico anterior/mínimo utilizado.
- Ausência de moeda, observações, disponibilidade, moeda compatível ou total
  determinável retorna `insufficient_data` com razão estável.
- Validação real no PostgreSQL 18 confirmou menor total elegível, exclusão de
  frete desconhecido, USD e fonte não selecionada, vendedor opcional, histórico
  identificável, retorno insuficiente e rollback sem resíduos.
- Pipeline completo aprovado em Python 3.14.6: 440 testes, 95,09% de cobertura,
  Ruff, Alembic com head único e Docker Compose válidos. Nenhuma dependência ou
  migração nova foi necessária.

## 2026-08-08 — TASK-037: preferências de notificações Telegram

- Corrigido o escopo genérico da TASK-037 (`DEC-025`): ela controla somente
  notificações de queda de preço e preço-alvo; lojas, categorias, e-mail,
  autenticação e todo o cadastro da TASK-060 permaneceram inalterados.
- Adicionado `/preferencias`, sem IA e sem botões, para consultar e executar
  `quedas ativar|desativar` e `alvo ativar|desativar`; o menu real do bot foi
  atualizado.
- A revisão `20260808_0006` adiciona `notify_price_decreases` e
  `notify_target_reached` a `users`, ambos obrigatórios e `true` por padrão,
  inclusive para registros existentes.
- `ConsumptionOutcome` ganhou `skipped`: sem `failure_code`, append-only e
  terminal como `succeeded`. O índice e `claim_unconsumed_events` consideram
  ambos terminais, evitando retry, pendência e reenvio retroativo após
  reativação.
- PostgreSQL 18 isolado aprovou defaults, constraints, índice e ciclo
  `upgrade → downgrade → upgrade`. Validação real com Telegram registrou dois
  `skipped`, entregou somente um evento novo após reativação e terminou sem
  pendências; dados de teste foram revertidos.
- Pipeline completo aprovado em Python 3.14.6: 429 testes, 94,97% de cobertura,
  Ruff, grafo Alembic com head único e Docker Compose válidos; imagem Linux
  reconstruída e smoke test do novo contrato aprovado.

## 2026-08-08 — TASK-036: notificações proativas Telegram

- Adicionado `users.telegram_chat_id` pela migração `20260808_0005`, com
  unicidade e constraint que aceita somente o chat privado correspondente ao
  `telegram_user_id`; o webhook nunca salva grupos, supergrupos ou canais como
  destino automático (`DEC-024`).
- Criado `app.telegram.notifications`: o consumidor
  `telegram_price_alerts_v1` reivindica apenas `price.decreased.v1` e
  `price.target_reached.v1`, resolve o proprietário da missão, formata a
  mensagem em português e registra sucesso/falha append-only pela TASK-044.
- `claim_unconsumed_events` ganhou filtro opcional por tipos, preservando o
  contrato genérico e impedindo que o consumidor Telegram reivindique eventos
  fora do seu escopo.
- `send_message` agora rejeita respostas `ok=false` com erro sanitizado; a
  falha vira `telegram_delivery_failed` e permanece elegível para retry, em
  vez de produzir falso sucesso.
- Criados o processo contínuo `app.telegram.worker` e o serviço Compose
  `telegram_notifier`, com lote e intervalo configuráveis, executáveis em
  Docker/Linux headless para o futuro Ubuntu Server.
- 411 testes automatizados aprovados e cobertura de 94,89%. PostgreSQL real
  confirmou upgrade/downgrade/upgrade; Telegram real aceitou um alerta
  temporário e a tentativa durável foi `succeeded`; transação de teste
  revertida sem resíduos. Imagem Linux construída e worker `--once` aprovado
  no Docker.

## 2026-08-08 — TASK-044: consumo durável de eventos

- Criados `ConsumptionOutcome` e `EventConsumptionAttempt`, com FK `RESTRICT`
  para `events`, resultados `succeeded`/`failed`, código estável de falha e
  histórico protegido contra `UPDATE`/`DELETE`.
- A revisão Alembic `20260808_0004` adiciona a tabela
  `event_consumption_attempts`, o enum `consumption_outcome`, índice parcial de
  sucessos e trigger append-only; downgrade remove toda a estrutura e o enum.
- `claim_unconsumed_events` reivindica em ordem determinística somente eventos
  sem sucesso daquele consumidor, usando `FOR UPDATE SKIP LOCKED` e lote entre
  1 e 1000. `record_consumption_attempt` valida e registra o desfecho sem fazer
  commit, mantendo o controle transacional com o chamador.
- Decidida semântica at-least-once por consumidor (`DEC-023`): falhas permitem
  retry ilimitado; sucesso de um consumidor não afeta outro. Worker, backoff,
  dead-letter queue, exactly-once e Telegram permanecem fora do escopo.
- 394 testes automatizados aprovados, cobertura total de 95,46%. PostgreSQL
  real descartável confirmou concorrência sem dupla reivindicação, retry após
  falha, independência entre consumidores, append-only e ciclo
  upgrade/downgrade/upgrade; nenhum resíduo temporário permaneceu.

## 2026-08-08 — Correção da identidade agregada de eventos

- `publish_event` agora rejeita `aggregate_id` diferente do identificador do
  agregado presente no payload tipado (`mission_id`, `collection_run_id` ou
  `offer_id`), impedindo eventos duráveis associados ao recurso errado.
- Testes cobrem os três tipos de agregado e confirmam que nenhuma linha é
  adicionada nem ocorre `flush` quando as identidades divergem.
- Validação real no PostgreSQL confirmou a rejeição antes do `INSERT`, aceitou
  o evento coerente e deixou o banco sem resíduos após rollback. Pipeline local
  completo aprovado: 379 testes e 95,34% de cobertura.

## 2026-08-08 — TASK-043: publicação durável de eventos

- `backend/migrations/versions/20260808_0003_create_events.py` (novo): cria
  `events` conforme `docs/DATABASE.md` — `event_type`/`aggregate_type` em
  texto validado pela aplicação, `mission_id` FK opcional `RESTRICT`,
  `payload` JSONB, `occurred_at` informado pelo produtor, `recorded_at`
  gerado só pelo PostgreSQL (`server_default=now()`); dois índices
  compostos; trigger `trg_events_append_only` rejeitando `UPDATE`/`DELETE`,
  mesmo padrão de `mission_transitions`/`audit_entries`.
- `backend/app/events/models.py` (novo): modelo `Event`, registrado em
  `backend/app/database/model_registry.py`.
- `backend/app/events/service.py` (novo): `publish_event` — valida
  `occurred_at` consciente de fuso, reaproveita `validate_event_payload`
  do catálogo (TASK-042) para checar tipo/agregado, serializa o payload em
  JSON seguro sem perda de precisão (`UUID`/`Decimal` → `str`, `Enum` →
  `.value`) e persiste. Deliberadamente sem importar `app.alerts` — quem
  tiver um `PriceAlertCandidate` desempacota os campos na chamada.
- Escopo restrito à publicação genérica: nenhuma detecção nova para
  `mission.status_changed`/`collection.completed`/`collection.failed`/
  `offer.availability_changed` (nenhuma TASK atribui essa detecção ainda) e
  nenhum worker/consumidor (TASK-044) — ver `DEC-022`.
- Testes novos em `tests/test_event_publication.py` (forma da tabela,
  serviço, validação tz-aware/naive, incompatibilidade de agregado,
  propagação de `EventCatalogError`); `tests/test_database.py` atualizado
  para incluir `events` na lista fechada de tabelas implementadas.
- Validação real contra PostgreSQL (dentro de uma transação revertida, sem
  resíduo no banco local): `evaluate_price_alerts` (TASK-027) real produziu
  2 candidatos a partir de uma queda de preço cruzando o alvo da missão;
  `publish_event` persistiu ambos com `recorded_at` populado pelo servidor
  via `RETURNING`; `UPDATE` e `DELETE` reais em `events` foram rejeitados
  pelo trigger. `scripts\check.cmd` completo aprovado: 376 testes, 95,33%
  de cobertura, grafo de migrações com único head (`20260808_0003`).

## 2026-08-08 — TASK-058: confirmação da intenção interpretada antes de executar

- `backend/migrations/versions/20260808_0002...`: `users` ganha
  `pending_intent` (JSONB opcional) para guardar a ação encenada aguardando
  confirmação.
- `backend/app/telegram/confirmation.py` (novo): classifica
  confirmar/cancelar via `AIProviderManager` com propósito e prompt
  dedicados (`interpret_confirmation_reply`), fora do vocabulário fechado
  do `IntentInterpreter` — reconhece linguagem informal e erros de
  português, não só palavra exata (decisão revista em campo depois da
  primeira validação real, que usava só palavra exata).
- `backend/app/telegram/router.py`: `create_mission` e `mission_command`
  passam a ser "encenados" (`user.pending_intent`) em vez de executados
  direto; a mensagem seguinte do usuário é checada contra a confirmação
  pendente antes de qualquer outro processamento. `query_mission` e
  `unknown` continuam imediatos. Texto de `unknown` agora deixa explícito
  que o bot não conversa sobre outros assuntos.
- `backend/app/intent/interpreter.py` e `backend/app/telegram/adapter.py`
  ganham uma propriedade `manager`, para o classificador de confirmação
  reaproveitar o `AIProviderManager` já resolvido por perfil (TASK-060).
- **Duas correções reais de infraestrutura**, achadas durante a validação
  real contra o Telegram:
  - `AISHOPPING_GEMINI_API_KEY` virou duas chaves —
    `AISHOPPING_GEMINI_API_KEY_USER` e `AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`
    — para que a cota gratuita do perfil `USER` nunca compita com a do
    `ADMIN`/`DEV`, nem no nível da credencial.
  - `validate_provider_response` estava fora do `try/except` em
    `UserAIProviderManager`/`AdminDevAIProviderManager`: uma resposta
    reprovada por essa checagem (ex.: `finished_at` antes de
    `requested_at`) escapava sem telemetria nem fallback. Causa raiz real:
    `TelegramIntentAdapter` repassava `message.received_at` (relógio do
    Telegram) como `requested_at`, comparado contra `finished_at` do nosso
    próprio relógio (sem sincronia NTP na máquina local) — removida essa
    comparação entre relógios de fontes diferentes.
- Testes novos/ajustados em `tests/test_telegram_confirmation.py`,
  `tests/test_telegram_router.py`, `tests/test_telegram_adapter.py`,
  `tests/test_gemini_user_profile.py` (regressão do bug de telemetria) e
  `tests/test_users.py`. `scripts\check.cmd` completo aprovado: 368
  testes, 95,29% de cobertura.
- **Validação real completa contra o Telegram**: criar missão sem citar
  loja (quatro fontes-padrão, confirmação descrita corretamente), cancelar
  com frase informal ("cancela essa aí, quero mais não" → reconhecido como
  cancelamento), e confirmar um comando de missão — todos via a cascata
  real `ADMIN/DEV` (premium → Groq), sem falhas silenciosas depois das
  correções.

## 2026-08-08 — TASK-060: perfil de IA por papel, cadastro inicial e placeholder de upgrade

- Registradas `DEC-018` (TASK-060), `DEC-019` (TASK-061, autenticação real
  por usuário e senha, retirada do cadastro por exigir desenho de segurança
  próprio) e `DEC-020` (e-mail deixa de ser tratado como credencial em
  `User`, mantendo senha/token de fora).
- `backend/app/telegram/router.py`: o webhook agora resolve o `User`
  (TASK-056) **antes** de interpretar a intenção (antes era depois), e
  escolhe entre dois `IntentInterpreter` — um ligado ao
  `UserAIProviderManager` (`USER`), outro ao `AdminDevAIProviderManager`
  (`ADMIN`/`DEV`, TASK-059) — a partir do `User.role` resolvido.
- Migração `20260808_0001`: `users` ganha `username` (único), `email`,
  `favorite_stores`, `preferred_categories` e `registration_step`, todos
  opcionais ou com padrão vazio; nenhum dado de autenticação real.
- `backend/app/users/registration.py` (novo): fluxo de cadastro inicial
  dirigido pelo comando `/cadastro`, com passos sequenciais
  (username → email → lojas favoritas → categorias), interceptando a
  mensagem seguinte do usuário sem passar pelo `IntentInterpreter`.
- Comando `/upgrade` registrado no menu do bot
  (`backend/scripts/register_telegram_commands.py`, novo), responde apenas
  "em breve" — nenhuma lógica real, placeholder deliberado
  (`docs/OUT_OF_SCOPE.md`: "Plano PLUS", "Usuário pago" continuam na V2).
- Testes novos (`tests/test_user_registration.py`) e ajustados
  (`tests/test_telegram_router.py`, `tests/test_telegram_adapter.py`,
  `tests/test_users.py`, `tests/test_gemini_user_profile.py`).
  `scripts\check.cmd` completo aprovado: 345 testes, 94,98% de cobertura.
- **Validação real completa contra o Telegram**: mensagem de um usuário
  `USER` interpretada via Gemini; usuário elevado manualmente a `ADMIN`
  (dono do projeto); mensagem seguinte do mesmo usuário como `ADMIN`
  acionou o Gemini premium, que retornou `429`, caindo para o Groq real com
  sucesso — a cascata da TASK-059 acionada por uma interação real, não só
  por ferramentas de validação. `/cadastro` completo validado de ponta a
  ponta (username, e-mail, lojas, categorias, `registration_step` limpo ao
  final). `/upgrade` validado (resposta estática, sem chamada de IA).

## 2026-08-08 — TASK-057 encerrada (`DEC-017`)

- O usuário autorizou explicitamente encerrar a TASK-057 com a cobertura
  real atual: 3 dos 4 `IntentKind` confirmados contra o `USER`/Gemini real
  (`create_mission`, `query_mission`, `mission_command`); `unknown` só tem
  confirmação via a cascata `ADMIN/DEV` (Gemini premium/Groq), não contra o
  Gemini gratuito real do `USER`, por nova exaustão de cota. Sem indício de
  comportamento incorreto — só lacuna de confirmação.
- Ampliar ainda mais a variedade de linguagem testada pelo
  `IntentInterpreter` registrada em `docs/BACKLOG.md` para a V2.
- `docs/tasks/TASK-057.md` marcada como concluída.

## 2026-08-08 — TASK-059: Groq como fallback do ADMIN/DEV + perfil configurável de validação

- Registrada `DEC-016` e criada `docs/tasks/TASK-059.md` depois que a chave
  `AISHOPPING_GROQ_API_KEY` (já presente em `backend/.env`) foi testada
  isoladamente com sucesso, fora do `AIProviderManager`.
- Criado `GroqProvider` (`backend/app/ai_provider/groq.py`) via `httpx`
  contra a API compatível com OpenAI do Groq, seguindo o mesmo contrato
  sanitizado (`AIProviderQuotaExceeded` com `quota_reset_at` do cabeçalho
  `retry-after`, `AIProviderUnavailable`, erros de autenticação/rejeição)
  já usado pelo `GeminiProvider`. `httpx` declarado explicitamente em
  `backend/requirements.txt` (já presente de forma transitiva).
- `AdminDevAIProviderManager` (`backend/app/ai_provider/manager.py`) agora
  tenta até três níveis — Gemini premium → Groq → Gemini gratuito — com o
  Groq opcional: sem `AISHOPPING_GROQ_API_KEY` configurada,
  `build_admin_dev_ai_provider_manager` mantém o comportamento de dois
  níveis já validado nas TASKs 029–031. `USER` continua sem fallback,
  inalterado.
- `IntentInterpreter.interpret` (`backend/app/intent/interpreter.py`) ganhou
  um parâmetro nomeado opcional `profile` (default `USER`, inalterado para
  o webhook de produção — TASKs 033–035), para que ferramentas de validação
  manual rodem via `ADMIN`/`DEV` sem consumir a cota gratuita compartilhada
  do `USER`.
- `backend/scripts/validate_intent_interpreter.py` ganhou a opção
  `--profile` (`admin` como padrão, `dev`, `user`), com pacing mais curto
  para `ADMIN/DEV` (5s) e mantendo o pacing conservador para `user` (60s).
- Testes novos (`tests/test_groq_provider.py`) e ajustados
  (`tests/test_gemini_user_profile.py`, `tests/test_intent_interpreter.py`).
  `scripts\check.cmd` completo aprovado: 316 testes, 94,96% de cobertura.
- **Validação real**: chamada direta ao Groq real via `AIProviderManager`
  (resposta coerente do `llama-3.3-70b-versatile`); cascata de 3 níveis
  validada de ponta a ponta com um premium real forçado a falhar por cota,
  confirmando que o Groq real é alcançado e responde. As 19 mensagens
  diversas da TASK-057 foram classificadas corretamente via `--profile
  admin`, sem tocar na cota do `USER`.
- **TASK-057 avançou**: com o perfil `ADMIN/DEV` validado, a confirmação
  final contra o `USER`/Gemini real passou de 3/19 para cobrir 3 dos 4
  `IntentKind` (`create_mission`, `query_mission`, `mission_command`); só
  `unknown` segue pendente por nova exaustão da cota gratuita do `USER`
  (`docs/tasks/TASK-057.md`).

## 2026-08-08 — TASK-057 (parcial) e TASK-058 (registro)

- Registrada `DEC-015` e criada `docs/tasks/TASK-058.md`: confirmar a
  intenção interpretada e pedir aprovação explícita do usuário antes de
  executar qualquer comando de missão. Pedido do usuário durante a execução
  da TASK-057, fora do escopo dela (altera o despacho do webhook das TASKs
  033–035); registrada como nova TASK do MVP, não implementada.
- Refinado o prompt de sistema de `backend/app/intent/interpreter.py`
  (TASK-032/057): orientação explícita de robustez a erros de digitação,
  gírias regionais, falta de acentuação/pontuação e ordem livre das
  informações na frase, mais exemplos few-shot cobrindo os quatro
  `IntentKind`, sem alterar o vocabulário fechado do contrato.
- Ampliado `backend/scripts/validate_intent_interpreter.py` para rodar um
  conjunto padrão de 19 mensagens diversas cobrindo os quatro `IntentKind`,
  com pacing entre chamadas e retry com backoff respeitando
  `quota_reset_at` quando informado pelo provedor.
- Atualizado `tests/test_intent_interpreter.py` com verificação de que o
  prompt de sistema contém a orientação de robustez. `scripts\check.cmd`
  completo aprovado em Python 3.14.6: 299 testes, 94,73% de cobertura.
- **Impedimento registrado** (`docs/tasks/TASK-057.md`): a validação manual
  real contra o Gemini ficou incompleta — apenas 3 das 19 mensagens
  (`create_mission`) foram validadas antes de a cota gratuita do perfil
  `USER` se esgotar, sem `quota_reset_at` informado. Rotear a validação pelo
  Gemini premium ou por outro provedor (ex.: a chave Anthropic presente em
  `backend/.env`) foi descartado por violar o guardrail de acesso exclusivo
  via `AIProviderManager` no perfil `USER`. TASK-057 permanece em execução
  até a validação real cobrir `query_mission`, `mission_command` e
  `unknown`.

## 2026-08-08 — TASK-035

- Semeada a migração `20260807_0002` com as quatro lojas selecionáveis da
  V1 (`pichau`, `terabyte`, `amazon`, `kabum`), necessárias para
  `MissionSource.store_id`.
- Criada `backend/app/database/dependency.py` (`get_session`), a primeira
  dependência FastAPI de sessão de banco por requisição do projeto: comita
  no sucesso, desfaz em qualquer exceção, sempre fecha a sessão.
- Criadas `list_missions_for_user`, `find_missions_by_reference` e
  `resolve_mission_for_command` (`backend/app/missions/query.py`) para
  resolver uma missão a partir de texto livre, já que a V1 não expõe
  identificador de missão ao usuário.
- Criado `create_mission_from_criteria` (`backend/app/missions/service.py`):
  cria `Mission` + `MissionCriteria` + `MissionSource` e ativa
  imediatamente. Quando o `Intent` não especifica fonte nenhuma, usa
  automaticamente as quatro fontes da V1 — toda `CREATE_MISSION` válida sai
  `active`, nunca `draft`.
- Criado `backend/app/telegram/bot_api.py` (`call_bot_api`, `send_message`),
  reaproveitado pelo script de registro do webhook para eliminar duplicação.
- `POST /telegram/webhook` agora resolve a identidade do usuário (TASK-056),
  despacha por `IntentKind` (criar, consultar, comandar, desconhecido) e
  responde ao Telegram. Definido o limite explícito entre erro conhecido de
  domínio/validação (`204` com explicação) e falha inesperada, incluindo
  `IntegrityError` residual não tratada no serviço apropriado (`500`, nunca
  mascarada) — `DEC-012`, `DEC-013`.
- Aprovado `scripts\check.cmd` completo em Python 3.14.6: 298 testes, 94,73%
  de cobertura.
- Validado de ponta a ponta contra PostgreSQL 18, o Gemini e o Telegram
  reais: criação com fonte explícita e sem fonte nenhuma (fonte-padrão
  confirmada), consulta, comando (pausar) e mensagem não reconhecida — todos
  com resposta real recebida no Telegram e o estado correspondente
  confirmado em `missions`, `mission_criteria`, `mission_sources` e
  `mission_transitions`.
- Ajustada a resposta de intenção não reconhecida para lista com marcadores
  em vez de um parágrafo único, a partir de feedback real de legibilidade
  durante a validação manual.

## 2026-08-07 — TASK-056

- Descoberto, ao preparar a TASK-035, que persistir uma missão via Telegram
  exigia `Mission.user_id` sem existir nenhuma forma de resolver qual `User`
  corresponde a uma pessoa no Telegram; TASK-035 pausada e TASK-056 criada
  como pré-requisito (`DEC-011`).
- Adicionado `User.telegram_user_id` (opcional, único, `BigInteger`) —
  exclusivamente o `user.id` do Telegram (a pessoa), nunca o `chat.id` (a
  conversa); `chat_id` não é persistido nesta tarefa.
- Adicionada a migração reversível `20260807_0001`.
- Criado `get_or_create_telegram_user` em `backend/app/users/service.py`:
  resolução determinística e idempotente, com `SAVEPOINT` protegendo contra
  a corrida de duas mensagens simultâneas do mesmo usuário novo. Sessão
  controlada pelo chamador (`session.flush()`, nunca `commit()`), mesmo
  padrão de `transition_mission`. Não conectado ao webhook da TASK-034 —
  essa decisão de sessão por requisição pertence à TASK-035.
- Aprovado `scripts\check.cmd` completo em Python 3.14.6: 272 testes, 94,50%
  de cobertura, `users/service.py` e `users/models.py` a 100%.
- Validado em PostgreSQL 18 real via Docker Compose: `get_or_create_telegram_user`
  chamado duas vezes devolveu o mesmo `User.id`; uma inserção direta
  duplicada, contornando o serviço, foi rejeitada pela constraint `UNIQUE`
  do banco; confirmada a cadeia `upgrade` → `downgrade -1` → `upgrade`
  novamente até `20260807_0001`.
- TASK-035 segue pausada e só será retomada por solicitação explícita; seu
  bloqueio de pré-requisito foi removido.

## 2026-08-07 — TASK-034

- Criada a rota `POST /telegram/webhook` (fora de `/api/v1`), que autentica
  cada atualização real do Telegram por segredo compartilhado
  (`X-Telegram-Bot-Api-Secret-Token` contra
  `AISHOPPING_TELEGRAM_WEBHOOK_SECRET`, comparação de tempo constante) e usa o
  envelope de erro padrão no `401`.
- Atualizações sem mensagem de texto (foto, callback, mensagem editada) são
  reconhecidas e ignoradas com `204`.
- Definido que, depois de uma entrega autenticada, falha de interpretação
  (cota, indisponibilidade, conteúdo inválido) nunca vira `500`: é registrada
  e a rota ainda responde `204`, para não transformar falha de IA em falha de
  transporte e evitar reentrega pelo Telegram. Nenhum mecanismo de retry,
  fila, resposta ao usuário ou execução de comando foi criado.
- Adicionado `backend/scripts/register_telegram_webhook.py` (`set`/`delete`/
  `info`), sem SDK, usando `urllib` da biblioteca padrão contra a Bot API real.
- Adicionados `AISHOPPING_TELEGRAM_BOT_TOKEN` e
  `AISHOPPING_TELEGRAM_WEBHOOK_SECRET` a `Settings`; instalado `cloudflared`
  como ferramenta de desenvolvimento para expor o webhook local durante a
  validação real.
- Aprovado `scripts\check.cmd` completo em Python 3.14.6: 265 testes, 94,41%
  de cobertura.
- Validado de ponta a ponta contra o Telegram e o Gemini reais, usando um
  túnel `cloudflared`: mensagem real recebida, autenticada, traduzida e
  processada com sucesso (`ai_provider_attempt` com `outcome=succeeded`),
  resposta `204`; requisições sem segredo válido rejeitadas com `401` sem
  chamar a IA. Webhook removido e túnel encerrado ao final.

## 2026-08-07 — Correção: integrar a branch órfã da TASK-031

- Descoberto, ao auditar o código antes de iniciar a TASK-034, que o código
  real da TASK-031 (`backend/app/ai_provider/telemetry.py` e sua fiação em
  `contracts.py`, `gemini.py` e `manager.py`) nunca tinha sido mesclado ao
  `main`: existia só na branch `task-031-ai-telemetry` (commit `b8a6f3d`),
  enviada ao GitHub mas nunca integrada, enquanto o `main` avançou por uma
  linha irmã sem esse commit.
- A documentação (`CHANGELOG.md`, `PROJECT_CONTEXT.md`, `ROADMAP.md`,
  `AGENTS.md`, `docs/tasks/TASK-031.md`) já descrevia a TASK-031 como
  concluída antes do código estar de fato no `main`, incluindo uma correção
  anterior desta mesma sessão que confiou nessa documentação sem verificar o
  código-fonte.
- Corrigido com `git cherry-pick` do commit `b8a6f3d` sobre o `main` atual,
  resolvido a favor da documentação já sincronizada com as TASKs 032 e 033
  nos cinco arquivos de texto que conflitaram; o código de telemetria foi
  aplicado sem conflito.
- Revalidado `scripts\check.cmd` completo em Python 3.14.6: 256 testes
  aprovados (incluindo `tests/test_ai_telemetry.py`, ausente até então),
  94,33% de cobertura, `telemetry.py` com 100%.

## 2026-08-07 — TASK-033

- Criado `app.telegram`, a fronteira de entrada do canal Telegram, sem
  lógica de domínio e sem SDK ou webhook do Telegram.
- Definido o contrato imutável `TelegramMessage` (`chat_id`, `user_id`,
  `text`, `received_at`), sem resolução para o `User` interno.
- Criado `TelegramIntentAdapter`, que encaminha `text` e `received_at` ao
  `IntentInterpreter` já existente (TASK-032) e devolve o `Intent`
  resultante sem inspecionar `kind` ou `command`.
- Adicionados testes unitários cobrindo o contrato e a adaptação para os
  quatro valores de `IntentKind`, aprovados junto com `scripts\check.cmd`
  completo em Python 3.14.6 (248 testes, 94,89% de cobertura); `app.telegram`
  ficou com 100% de cobertura.
- Sem integração externa nova nesta tarefa: nenhuma chamada de rede própria
  foi adicionada, então nenhum script de validação manual foi necessário.
- Documentado em `docs/TELEGRAM_ADAPTER.md`, com referência cruzada em
  `docs/TELEGRAM.md`, e registrada a decisão de escopo em
  `docs/DECISION_LOG.md` (DEC-009).

## 2026-08-07 — Validação real da TASK-032

- Executado `scripts\check.cmd` completo em Python 3.14.6 com Docker Desktop
  disponível: `pip check`, `ruff check`, `ruff format --check`, 236 testes
  aprovados com 94,79% de cobertura, grafo de migrações Alembic com uma única
  head e configuração do Docker Compose válida.
- Executada a validação manual real contra o Gemini do perfil `USER` com
  `backend/scripts/validate_intent_interpreter.py`, cobrindo os quatro
  valores de `IntentKind`: `create_mission` ("Quero um notebook gamer até R$
  5000 na Pichau ou Kabum"), `mission_command` ("pausa minha missão do
  notebook" → comando `pause`), `query_mission` ("Como está minha missão do
  notebook?") e `unknown` ("Qual é a previsão do tempo em São Paulo
  amanhã?").
- Confirmado por auditoria que o vocabulário de `IntentKind` e
  `IntentParameters` reaproveita exatamente `MissionCommand`
  (`docs/MISSION_SYSTEM.md`), os campos de `MissionCriteria`
  (`docs/MISSION_CRITERIA.md`) e as quatro fontes selecionáveis
  (`docs/TELEGRAM.md`), sem nenhum campo novo de domínio.
- Confirmado que a extração da tupla de exceções de parsing para a constante
  de módulo `_PARSING_ERRORS` não é uma correção de um bug do Ruff: com
  `target-version = "py314"`, o Ruff aplica a PEP 758 (Python 3.14) e remove
  os parênteses de `except (A, B):`, produzindo `except A, B:`, sintaxe
  válida somente a partir do Python 3.14. O ambiente de implementação
  original só tinha Python 3.10, que rejeita essa sintaxe; no Python 3.14.6
  real, ambas as formas compilam e passam por lint e testes. A constante
  nomeada foi mantida por clareza, sem necessidade de reversão.
- Nenhuma falha real foi encontrada; nenhuma correção de código foi
  necessária.

## 2026-08-07 — TASK-032

- Criado `IntentInterpreter`, agnóstico de canal, que traduz mensagens livres
  em `Intent` estruturado usando exclusivamente `AIProviderManager` no perfil
  `USER`.
- Definido vocabulário fechado de intenção (`create_mission`, `query_mission`,
  `mission_command`, `unknown`), reaproveitando diretamente `MissionCommand`
  para os seis comandos já existentes do ciclo de vida da missão e os campos
  já existentes de `MissionCriteria` para os parâmetros extraídos.
- Implementado parsing estrito da resposta do provedor, com fallback seguro
  para `unknown` em qualquer JSON inválido, campo desconhecido, valor fora do
  vocabulário fechado ou combinação inconsistente entre `kind` e `command`;
  falhas do provedor (cota, indisponibilidade) continuam propagadas, sem
  virar `unknown`.
- Adicionado script manual `backend/scripts/validate_intent_interpreter.py`
  para validação real contra o Gemini, seguindo o padrão das TASKs 029 a 031.
- Aprovados lint e formatação (`ruff check`, `ruff format --check`) e os
  novos testes unitários por leitura e revisão de código. A execução real do
  `pytest` e do script de validação com Gemini não foi possível neste
  ambiente: o sandbox de execução só tem Python 3.10 disponível (o projeto
  exige 3.14) e o download de uma versão compatível do Python via `uv` foi
  bloqueado pela política de rede do ambiente; adicionalmente, um diretório
  `.pytest_cache` órfão e sem permissão de leitura impede qualquer execução
  do `pytest` neste sandbox, independentemente da versão do Python. A
  validação real completa fica pendente da máquina do usuário, com Python
  3.14.6 já validado conforme `docs/DEPENDENCIES.md`.

## 2026-08-02 — TASK-031

- Adicionada telemetria JSON sanitizada por tentativa de IA, com correlação,
  perfil, finalidade, provedor, modelo, resultado e indicação de fallback.
- Preservado de erros `429` somente o reset recomendado por
  `google.rpc.RetryInfo`; detalhes brutos, prompts, respostas, tokens e chaves
  continuam fora dos contratos e dos eventos.
- Criado aviso seguro e agnóstico de canal para cota esgotada, informando o
  horário UTC quando conhecido ou declarando o prazo desconhecido.
- Validado o fluxo real ADMIN/DEV: o premium retornou `429` com reset informado,
  a telemetria registrou a falha e o fallback gratuito respondeu com sucesso.

## 2026-08-02 — TASK-030

- Implementada política única para ADMIN/DEV, sem perfis de IA duplicados.
- Definida tentativa em `gemini-3.1-pro-preview` com fallback para o gratuito
  `gemini-3.6-flash` em quota ou indisponibilidade.
- Mantido USER exclusivamente no nível gratuito e movidos usuário pago,
  OpenAI e Claude para a V2 pela ausência de API geral gratuita.
- Validado o fallback real com chave Free Tier: recusa `429` no premium e
  resposta não vazia com conteúdo esperado no Flash gratuito.

## 2026-08-02 — Preflight obrigatório de TASKs

- Tornada obrigatória, antes de qualquer implementação, a identificação de
  credenciais, contas, permissões, serviços, infraestrutura e ferramentas
  necessárias ao desenvolvimento e à validação real.
- Definido que recursos ausentes que dependam do usuário devem ser solicitados
  antecipadamente, com orientação de configuração segura fora do Git e do chat.
- Proibido substituir uma integração real previsível por implementação genérica
  ou validação exclusivamente mockada por falta de preparação antecipada.

## 2026-08-02 — TASK-029

- Implementado perfil USER com `google-genai` 2.16.0 e modelo padrão
  `gemini-3.6-flash`.
- Adicionadas configuração segura, tradução de mensagens e falhas sanitizadas.
- Validada chamada autenticada real pelo `AIProviderManager`, com resposta não
  vazia do modelo `gemini-3.6-flash` e conteúdo esperado.
- O endpoint real rejeitou uma chave descartável inválida e a falha foi
  sanitizada corretamente, sem exposição da credencial ou resposta bruta.

## 2026-08-02 — TASK-028

- Definidos contratos imutáveis de mensagens, requisição e resposta de IA.
- Criadas portas assíncronas para manager e providers internos e taxonomia segura
  de erros, sem integrar SDKs ou provedores reais.

## 2026-08-02 — TASK-027

- Criado avaliador determinístico de queda de preço e entrada no total-alvo para
  missões ativas.
- Adicionados candidatos tipados do catálogo, prevenção de repetição contínua e
  proteção contra disponibilidade ou moeda não comparável.

## 2026-08-02 — TASK-042

- Definido catálogo fechado e versionado com seis eventos de missão, coleta,
  preço e disponibilidade.
- Criados payloads tipados e validações executáveis sem antecipar persistência,
  publicação, consumo, alertas ou notificações.

## 2026-08-02 — TASK-017

- Criadas consultas somente leitura para listar e obter a observação mais recente
  do histórico de preços de uma oferta.
- Adicionados filtros temporais e de disponibilidade, paginação validada, total
  filtrado e ordenação determinística.

## 2026-08-02 — TASK-015

- Criadas observações append-only com preço, moeda, frete, total, fulfillment,
  disponibilidade, horários e evidência bruta.
- Adicionada revisão reversível `20260802_0012` com constraints monetárias e
  vínculos restritivos a oferta e coleta.

## 2026-08-02 — TASK-026

- Criados `CollectionRun`, estados `running`, `succeeded` e `failed` e revisão
  reversível `20260802_0011`.
- Implementados início e encerramento atômicos com bloqueio de linha.

## 2026-08-02 — TASK-025

- Implementada normalização monetária exata com `Decimal`, sem ponto flutuante.
- Separados preço do item, frete conhecido e total; frete grátis é zero e frete
  desconhecido permanece nulo.
- Validados códigos ISO 4217, símbolos monetários, consistência do frete,
  precisão e limite de `numeric(19,4)`.
- Preservados oferta bruta, vendedor, fulfillment e evidência; disponibilidade
  normalizada em três estados sem inferir estoque ausente.
- Corrigido o entrypoint Linux para LF permanente após falha real causada por
  CRLF no shebang.
- Validadas três ofertas reais de Amazon, Kabum, Pichau e Terabyte em container
  Linux; Pichau e Terabyte executaram headed via Xvfb.
- Ampliada a suíte para 134 testes aprovados e 97,68% de cobertura.

## 2026-08-02 — Complemento Linux da TASK-055

- Reaberta e concluída novamente a TASK-055 após validação Linux real.
- Adicionado entrypoint com Xvfb à imagem da API para Chromium headed sem
  interface gráfica no host.
- Incluído o validador real na imagem; Pichau e Terabyte usam headed por padrão,
  enquanto Amazon e Kabum permanecem headless.
- Localizado o Docker Desktop instalado fora do `PATH`; construída a imagem
  Linux com Python 3.14, Chromium 151 e Xvfb.
- Validadas três ofertas reais por origem: Amazon e Kabum em headless, Pichau e
  Terabyte em headed pelo display virtual, sem interface gráfica.
- Corrigida após falha real a invocação do validador para execução como módulo
  Python dentro do container.

## 2026-08-02 — TASK-055

- Implementados providers Playwright independentes para Pichau, Terabyte,
  Amazon e Kabum, preservando os campos brutos disponíveis em cada card.
- Adicionada coleta paralela de exatamente uma ou mais fontes selecionadas, com
  rejeição de duplicidade e sem providers para ML, Shopee ou AliExpress.
- Validadas Amazon, Kabum e Pichau por automação real e Terabyte por navegador
  real; documentada a proteção que pode exigir modo headed/display virtual.
- Mantida a política de não usar stealth, CAPTCHA solver ou evasão de proteção.
- Ampliada a suíte para 108 testes aprovados e 96,36% de cobertura.

## 2026-08-02 — TASK-024

- Adicionados Playwright 1.62.0 e Chromium à aplicação e à imagem Docker.
- Criada sessão assíncrona de navegador com contexto isolado, execução headless, timeouts configuráveis e downloads desabilitados.
- Implementado encerramento determinístico de página, contexto, navegador e processo Playwright, inclusive após inicialização parcial.
- Validado Chromium real com renderização e consulta de conteúdo local, sem antecipar Store Providers ou acessar marketplaces.
- Construída a imagem Docker e executado nela o mesmo smoke test com Chromium e dependências Linux reais.
- Ampliada a suíte para 101 testes aprovados e 99,80% de cobertura.
- Documentados instalação, operação, limites e sequência correta: TASK-055 antes da TASK-025.

## 2026-08-02 — Segurança do ambiente Python

- Restringido o ambiente do projeto à instalação oficial do Python da máquina, com assinatura digital válida.
- Proibidos instalação de dependências e testes em runtimes internos do Codex, plugins, caches ou ferramentas hospedeiras.
- Definido que alertas do antivírus interrompem a execução e não autorizam restauração ou exceções automáticas.
- Registrado em `docs/SECURITY_INCIDENT_LOG.md` o evento `INC-2026-08-02-001`, sua resposta, evidências sanitizadas e prevenção.
- Adicionada ao README uma observação visível sobre o ambiente Python permitido e a conduta diante de alertas.
- Corrigido o estado do `AGENTS.md`: TASK-023 concluída e TASK-024 como próxima tarefa executável.

## 2026-08-02 — TASK-023

- Criada a fronteira assíncrona entre orquestração e Store Providers, sem antecipar navegador ou providers concretos.
- Definidos contratos imutáveis para solicitação, oferta bruta e resultado de coleta, com validação de fonte e linha do tempo.
- Preservados nos resultados brutos vendedor, frete, disponibilidade e fulfillment necessários a varejistas e marketplaces.
- Implementado registro e despacho por fonte, com erros explícitos para duplicidade, ausência e violação contratual.
- Documentado o limite com as TASKs 024, 055, 025 e 026 e atualizada a referência de ambiente para Python 3.14.6.
- Validada a suíte no Python 3.14.6 com 97 testes aprovados, 99,78% de cobertura, Ruff, dependências, grafo Alembic e Docker Compose.

## 2026-08-02 — TASK-022

- Criados `MissionSchedule` e a revisão reversível `20260802_0010`, com uma agenda editável por missão.
- Implementados intervalo fixo positivo, próxima execução, última execução, ativação lógica e índice parcial de agendas habilitadas.
- Adicionada consulta determinística de missões ativas e não expiradas com `FOR UPDATE SKIP LOCKED`.
- Implementado avanço para o primeiro intervalo futuro, sem acumular backlog retroativo.
- Validada em PostgreSQL 18 a filtragem real, concorrência entre workers, constraints, downgrade e novo upgrade.
- Ampliada a suíte para 89 testes aprovados e 99,73% de cobertura.

## 2026-08-02 — Correção retroativa para marketplaces

- Corrigidas as TASKs 010, 014, 020 e 021 para suportar seleção de múltiplas fontes e vendedores terceiros da Amazon.
- Adicionados `Store.source_type`, `Seller`, `Offer.seller_id` e `MissionSource`, com integridade referencial e identidades separadas para varejo e marketplace.
- Ativação e retomada agora exigem critérios e ao menos uma fonte selecionada.
- Atualizado o contrato futuro de observações para preservar preço, frete, total, vendedor e fulfillment.
- Adicionada a revisão reversível `20260802_0009` e validada em PostgreSQL 18 com cenários válidos, constraints, downgrade e novo upgrade.
- Ampliada a suíte para 75 testes aprovados e 99,69% de cobertura.

## 2026-08-02 — Correção de escopo da TASK-055

- Corrigido o requisito que restringia a TASK-055 exclusivamente à Kabum.
- Definidas Pichau, Terabyte, Amazon e Kabum como fontes selecionáveis da V1, cada uma com Store Provider próprio e validação real.
- Mercado Livre, Shopee e AliExpress serão apresentados pelo bot sob ***Futuro***, sem seleção ou coleta na V1.
- Outras fontes e descoberta automática permanecem fora do escopo.
- Registrada a decisão `DEC-006` e sincronizados contexto, MVP, roadmap, backlog e limites de escopo.

## 2026-08-02 — TASK-021

- Criados `MissionTransition`, o vocabulário tipado `MissionCommand` e a revisão reversível `20260802_0008` com histórico append-only.
- Implementados todos os comandos e estados definidos na TASK-018, com validação de critérios, prazo e estados terminais.
- Tornadas atômicas a alteração de estado, a progressão de `state_version` e a inclusão do histórico, usando bloqueio da linha e versão esperada para concorrência.
- Validada em PostgreSQL 18 a cadeia de migration, o ciclo real completo, constraints, imutabilidade, conflito de versão, concorrência com uma única vencedora, downgrade e novo upgrade.
- Ampliada a suíte para 69 testes aprovados e 99,67% de cobertura.

## 2026-08-02 — Processo de validação

- Registrada autorização permanente para baixar e instalar dependências necessárias à execução e à validação real do projeto.
- Tornados obrigatórios testes reais e isolados de integração quando a mudança envolver banco de dados, contêiner, API ou outra infraestrutura; validação estática, mocks e testes unitários permanecem complementares.

## 2026-08-02 — TASK-020

- Criado o modelo SQLAlchemy `MissionCriteria`, limitado a um registro editável por missão.
- Adicionados busca obrigatória e preço-alvo opcional em `numeric(19,4)` pareado com moeda ISO 4217.
- Protegidos busca não vazia, valor não negativo, presença conjunta de valor e moeda e formato monetário por constraints.
- Mantidas recorrência e frequência fora dos critérios e reservadas à agenda da TASK-022, sem JSONB especulativo.
- Adicionada a revisão reversível `20260802_0007` e atualizado o registro central de modelos.
- Validada em PostgreSQL 18 a cadeia completa até a revisão `20260802_0007`, incluindo inserção válida, constraints, unicidade, chave estrangeira, downgrade, novo upgrade e sincronização da metadata pelo Alembic.
- Ampliada a suíte para 52 testes aprovados e 99,57% de cobertura.

## 2026-08-02 — TASK-019

- Criados o modelo SQLAlchemy `Mission` e o enum PostgreSQL `mission_status` com os seis estados da TASK-018.
- Adicionados proprietário obrigatório com `RESTRICT`, estado inicial `draft`, prazo opcional, versão concorrente e timestamps UTC.
- Protegidos título não vazio, prazo posterior à criação e versão não negativa por restrições no banco.
- Criados índices para listagem recente por proprietário e para expirações pendentes de estados não terminais.
- Adicionada a revisão reversível `20260802_0006` e atualizado o registro central de modelos.
- Detectadas e corrigidas durante a revisão a criação duplicada do enum e a divergência de ordenação do índice ORM.
- Validada a cadeia linear e a geração SQL offline de upgrade e downgrade; PostgreSQL real não pôde ser usado porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 46 testes aprovados e 99,55% de cobertura.

## 2026-08-02 — TASK-016

- Criado o modelo SQLAlchemy `AuditEntry` para fatos auditáveis separados de logs operacionais.
- Adicionados ator opcional, ação e recurso tipados, metadata JSONB com padrão seguro e timestamp UTC.
- Protegida a referência a usuários com `RESTRICT` e criados índices históricos por recurso e ator.
- Adicionado trigger PostgreSQL que rejeita `UPDATE` e `DELETE`, tornando a tabela efetivamente append-only.
- Adicionada a revisão reversível `20260802_0005` e atualizado o registro central de modelos.
- Corrigida a ordem documental da TASK-015 para depois da TASK-026, preservando a FK obrigatória para coletas.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 39 testes aprovados e 99,49% de cobertura.

## 2026-08-02 — TASK-014

- Criados os modelos SQLAlchemy `Store` e `Offer`, separando a origem nacional normalizada do anúncio estável de um produto.
- Adicionadas relações obrigatórias para produto e loja com `RESTRICT`, sem exclusão em cascata.
- Protegida a identidade por `(store_id, external_id)` quando informado e por `(store_id, url)`, com índices relacionais adicionais.
- Mantidos preço e disponibilidade fora da oferta para preservar o futuro histórico de observações.
- Adicionada a revisão reversível `20260802_0004` e atualizado o registro central de modelos.
- Detectada e corrigida durante a revisão uma expressão regex que o SQLAlchemy compilava incorretamente no DDL.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 33 testes aprovados e 99,43% de cobertura.

## 2026-08-02 — TASK-013

- Criado o modelo SQLAlchemy `Product` para a identidade canônica independente de loja, oferta e preço.
- Adicionados UUID gerado pela aplicação, nome, marca e modelo opcionais, timestamps UTC e restrições contra textos em branco.
- Centralizado o relógio UTC compartilhado pelos modelos de usuário e produto.
- Adicionada a revisão reversível `20260802_0003` e atualizado o registro central de modelos.
- Definida deduplicação conservadora, sem unicidade por nome nem mesclagem automática sem evidência suficiente.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 25 testes aprovados e 99,28% de cobertura.

## 2026-08-02 — TASK-012

- Criados o modelo SQLAlchemy `User` e o vocabulário tipado `UserRole` com `USER`, `ADMIN` e `DEV`.
- Adicionadas restrições para nome não vazio e papel válido, UUID gerado pela aplicação, ativação lógica e timestamps UTC.
- Criado registro central de modelos para manter a metadata do Alembic sincronizada.
- Adicionada a revisão reversível `20260802_0002` para a tabela `users`, sem credenciais, canais, API ou autorização.
- Validada em PostgreSQL 18 a inserção válida, a rejeição de `PLUS` e nome em branco, o downgrade e o novo upgrade.
- Ampliada a suíte para 20 testes aprovados e 99,17% de cobertura.

## 2026-08-02 — TASK-011

- Instalados e declarados SQLAlchemy 2.0.51, Alembic 1.18.5 e Psycopg 3.3.4 com suporte a Python 3.14 e PostgreSQL 18.
- Criadas configuração tipada da conexão, metadata declarativa única, construção de engine e fábrica de sessões sem conexão global antecipada.
- Configurado o ambiente Alembic e adicionada a baseline vazia `20260802_0001`, sem tabelas de domínio.
- Integradas as migrações à imagem da API e ao pipeline local, com comandos operacionais documentados.
- Validado em PostgreSQL 18 o ciclo upgrade, downgrade e novo upgrade em volume isolado; o volume existente do projeto foi preservado.
- Ampliada a suíte para 14 testes aprovados e 98,96% de cobertura.

## 2026-08-02 — TASK-010

- Definido o esquema relacional PostgreSQL para usuários, missões, critérios, transições, produtos, lojas, ofertas, coletas, preços, eventos e auditoria.
- Especificados tipos, chaves, relações, nulabilidade, unicidade, índices mínimos e regras monetárias e temporais.
- Incorporado o ciclo de vida da TASK-018 com estado atual, versão concorrente e histórico imutável de transições.
- Preservado o histórico de preços por observações anexadas, sem atualização destrutiva ou deduplicação de coletas repetidas.
- Delimitadas migrações, ORM e implementação persistente para as TASKs 011 a 017.

## 2026-08-02 — TASK-018

- Definidos os estados `draft`, `active`, `paused`, `completed`, `cancelled` e `expired` para missões.
- Documentados comandos, transições permitidas, condições e comportamento terminal.
- Estabelecidas invariantes para coleta, missões permanentes, concorrência, preservação de histórico e horários em UTC.
- Definido o registro mínimo e imutável de transições como entrada para a TASK-010, sem antecipar persistência ou implementação.
- Delimitadas as responsabilidades futuras das TASKs 010, 016 e 021.

## 2026-08-01 — TASK-009

- Criado pipeline local executável por `scripts\check.cmd`, compatível com a política de execução atual do Windows.
- Reunidas verificações de dependências, lint, formatação, testes, cobertura e Docker Compose com falha imediata.
- Garantida execução independente do diretório atual e restauração da senha temporária usada apenas para validar o Compose.
- Validado o pipeline completo com 9 testes aprovados e 98,65% de cobertura.

## 2026-08-01 — TASK-008

- Configurado logging JSON em `stdout` para a aplicação e os loggers do Uvicorn.
- Adicionado nível configurável por `AISHOPPING_LOG_LEVEL`.
- Criados eventos HTTP com método, caminho, status e duração, sem query string, corpo, cabeçalhos ou credenciais.
- Adicionados testes do formatador e dos fluxos HTTP de sucesso e falha.
- Validado o evento JSON real no ambiente Docker Compose.

## 2026-08-01 — TASK-007

- Definidas convenções para versionamento, rotas, métodos, códigos HTTP, JSON, erros, coleções e OpenAPI.
- Reservado `/api/v1` para endpoints de negócio e mantidos endpoints operacionais fora do prefixo.
- Alinhado `GET /health` com `operation_id`, código, descrição e resposta explícitos no OpenAPI.
- Validada a aderência do endpoint existente por testes automatizados.

## 2026-08-01 — TASK-006

- Criado módulo de saúde com `GET /health` e resposta estável `{"status":"ok"}`.
- Alterado o healthcheck do contêiner da API para usar o endpoint de vivacidade.
- Adicionados testes do contrato e do registro da rota no OpenAPI.
- Validado o endpoint por HTTP no ambiente Docker Compose, com API e PostgreSQL saudáveis.

## 2026-08-01 — TASK-005

- Configurado Pytest com descoberta explícita, validação estrita e cobertura mínima de 90%.
- Adicionados testes para os metadados FastAPI e para padrões, variáveis de ambiente e rejeição de configuração inválida.
- Separadas e documentadas as dependências de teste no conjunto de desenvolvimento.
- Validada a suíte com 4 testes aprovados e 100% de cobertura da base atual.

## 2026-08-01 — TASK-004

- Configurado Ruff para lint, ordenação de imports, modernização compatível com Python 3.14 e formatação.
- Separadas as dependências de desenvolvimento das dependências de runtime.
- Documentados comandos de verificação e correção automática.
- Corrigido o espaçamento dos blocos de importação existentes e validada toda a base atual.

## 2026-08-01 — TASK-003

- Criados Dockerfile da aplicação e Docker Compose para FastAPI e PostgreSQL 18.
- Adicionados volume persistente, healthchecks da API e do PostgreSQL e configuração local por `.env` não versionado.
- Documentados os comandos para iniciar e encerrar o ambiente local.
- Validado o ciclo completo com build sem cache, inicialização dos serviços, resposta HTTP da API, conexão do PostgreSQL e encerramento sem remoção do volume.

## 2026-08-01 — Consistência documental e versão do Python

- Confirmada pelo histórico e pelos critérios de aceite a conclusão das TASKs 000, 001 e 002.
- Sincronizados README, instruções, roadmap e índice de tarefas com o estado real do projeto.
- Adotada a política de uso da versão estável mais recente do Python, com Python 3.14.3 como versão atualmente validada.

## 2026-08-01 — Ambiente de desenvolvimento

- Criado `docs/DEPENDENCIES.md` como inventário de ferramentas e dependências da aplicação.
- Incluída no workflow a comparação de dependências em novas máquinas, com instalação somente após autorização.

## 2026-08-01 — TASK-002

- Adicionada gestão tipada de configuração por variáveis de ambiente.
- Criado exemplo seguro de configuração local e proteção para `backend/.env`.
- Validada a configuração padrão, a leitura de ambiente e a rejeição de valores inválidos.

## 2026-08-01 — TASK-001

- Criado o esqueleto mínimo da aplicação FastAPI.
- Declaradas as dependências FastAPI e Uvicorn.
- Validada a compilação, a integridade das dependências e a inicialização da aplicação.

## 2026-08-01 — Documentação

- Registrada a fase futura Marketplace Module para suporte a marketplaces.
- Mantido o escopo da V1 exclusivamente em lojas nacionais.
- Criados `docs/BACKLOG.md`, `docs/MVP.md` e `docs/OUT_OF_SCOPE.md` para controlar ideias futuras, escopo da V1 e exclusões explícitas.
- Adicionadas referências cruzadas a esses documentos no contexto do projeto e no roadmap.
- Criado `docs/DECISION_LOG.md` e adicionada política permanente de classificação prévia de novas funcionalidades no `AGENTS.md`.
- Reordenadas as dependências de ciclo de vida de missões e de catálogo de eventos; criada a TASK-055 para o Store Provider Kabum.
- Instituído o workflow oficial e permanente de execução de TASKs, com
  validação, testes, revisão técnica, documentação e commit convencional. A
  política original de push foi posteriormente substituída pela `DEC-030`.
- Nenhuma funcionalidade foi implementada.

## 2026-08-01 — TASK-000

- Criada a estrutura inicial de diretórios.
- Criados documentos de contexto, visão, arquitetura e módulos-alvo.
- Registrados ADRs e RFCs iniciais.
- Criados arquivos individuais TASK-000 a TASK-054.
- Nenhuma funcionalidade de aplicação foi implementada.
