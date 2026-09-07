# Handoff de deploy para PROD — GG Oferta + César Core/OmniRoute + Coupon Worker

Documento autossuficiente para o Claude que vai executar este deploy
**diretamente no Windows Server de PROD**, sem acesso à conversa que
preparou este handoff. Tudo que você precisa para decidir e agir está
aqui ou nos documentos linkados — nenhum deles pressupõe contexto de
chat anterior.

**Escopo deste deploy:** publicar em PROD um conjunto de trabalho já
concluído, testado e aprovado em DEV, mas nunca antes implantado:
FASE E.1–E.3 (Fetch/Enrichment via César Core), F1/F2/F3 (bootstrap
histórico + avaliação de mercado), consumo de cupons no GG Oferta +
coleta real no Coupon Worker, FASE G (feature flags), a política real
de providers AI (`ai_profile`, César Core/OmniRoute) e a separação de
cota de missões USER/ADMIN_DEV + correção do Telegram.

**Antes de fazer qualquer coisa:** leia a seção "Gate Gemini" perto do
fim deste documento. Se esse gate falhar por um motivo NÃO documentado
ali como aceito, pare e não force nada — reporte, não improvise.

---

## 1. Repositórios

| Componente | Repositório | Branch |
|---|---|---|
| GG Oferta | `https://github.com/jhonnatancesar/AIShoppingAgent` | `main` |
| César Core | `https://github.com/jhonnatancesar/cesar-core.git` | `main` |
| Coupon Worker | `https://github.com/jhonnatancesar/AIShoppingAgent-cupom.git` | **`master`** (não é `main` — confirme antes de qualquer comando que assuma o nome da branch) |

**HEAD esperado em `origin` no momento em que este handoff foi escrito**
(confirme sempre com `git fetch` + `git log` no servidor — não assuma
que nenhum commit novo aconteceu depois):

- **GG Oferta:** `<PREENCHER_APOS_PUSH_DESTA_RODADA>` — histórico
  relevante até aqui: `208b0bb` (consumo de cupons), `9fd5108` (FASE G:
  F1/F2/F3 + cupons sob flags), `fba5472` (correção de checkpoint de
  revisão), `cab1f98` (`ai_profile` no contrato com o Core), e o commit
  desta rodada (quota ADMIN/DEV=50 + correção do Telegram + saneamento
  de documentação + este handoff).
- **César Core:** `<PREENCHER_APOS_PUSH_DESTA_RODADA>` — histórico
  relevante: `83d3347` (política real de providers AI: 4 connections +
  2 combos), e o commit documental desta rodada.
- **Coupon Worker:** `<PREENCHER_APOS_PUSH_DESTA_RODADA>` — histórico
  relevante: `caca098` (persistência no PostgreSQL do GG + refinamento
  de esgotamento), e o commit documental desta rodada.

## 2. O que fazer primeiro (antes de qualquer deploy)

Para **cada um dos três repositórios**, nesta ordem, sem pular etapas:

1. Identifique o diretório real do repositório no servidor (caminho
   pode diferir do usado em DEV — confirme, não assuma
   `C:\AIShoppingAgent\AIShoppingAgent` / `C:\cesar-core` /
   `C:\AIShoppingAgenteCupom` sem checar; a topologia aprovada usa
   `C:\App\AIShoppingAgent`, `C:\App\cesar-core`, `C:\App\omniroute`
   para deployments independentes em PROD — `DEC-118` item 8, GG
   Oferta).
2. `git status` — leia com atenção. Se houver qualquer alteração local
   não commitada, **preserve-a** (não descarte, não faça `stash drop`,
   não faça `checkout .`/`restore .`) até entender o que é e confirmar
   com quem pode responder por isso.
3. `git branch --show-current` — confirme que está na branch certa
   (`main` para GG Oferta/César Core, `master` para Coupon Worker).
4. `git fetch origin`.
5. Compare HEAD local com `origin/<branch>`
   (`git rev-parse HEAD` vs. `git rev-parse origin/<branch>`).
6. **Nunca** use `git reset --hard` automaticamente para "resolver" uma
   divergência — se HEAD local diverge de `origin` de um jeito que não
   seja um simples atraso (fast-forward possível), pare e reporte antes
   de decidir.
7. Só depois de entender o estado, faça o pull/fast-forward seguro
   (`git pull --ff-only`, nunca merge/rebase automático sem revisão).

Repita para os três repositórios antes de prosseguir para a seção 8.

## 3. Migrations necessárias (só GG Oferta)

Head esperado após o pull: `20260906_0002`. Duas migrations compõem
esse head, ambas já testadas em DEV:

- `20260906_0001_add_historical_bootstrap_retry` — retry/backoff
  exponencial e lease do `HistoricalBootstrap` (F1).
- `20260906_0002_add_coupons` — schema de `coupons`/`coupon_offer_links`.

**DEV já está no head `20260906_0002`. PROD ainda não teve nenhuma das
duas aplicada — confirme o head real de PROD antes de rodar qualquer
coisa:**

```powershell
docker compose run --rm api python -m alembic -c alembic.ini current
```

Se o head de PROD já for `20260906_0002` por algum motivo não
documentado aqui, **pare e reporte** — não é o estado esperado.
Aplicar:

```powershell
docker compose run --rm api python -m alembic -c alembic.ini upgrade head
```

César Core e Coupon Worker não têm migration própria neste deploy
(Coupon Worker grava em tabelas já existentes do GG Oferta — a
migration acima precisa estar aplicada **antes** de configurar
`COUPONS_POSTGRES_DSN` nele).

## 4. Configuração necessária (nomes de variável — nunca valores aqui)

### GG Oferta (`backend/.env` nativo ou `.env` do Compose + `.secrets/`)

| Variável | Papel |
|---|---|
| `AISHOPPING_DEFAULT_MAX_ACTIVE_MISSIONS` | Cota de missões ativas USER — default de código `5`, não precisa de variável explícita a menos que se queira outro valor |
| `AISHOPPING_DEFAULT_MAX_ACTIVE_MISSIONS_ADMIN_DEV` | Cota de missões ativas ADMIN/DEV — **valor decidido: `50`**. Sem esta variável, ADMIN/DEV herda o default do USER (`5`) |
| `AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` | Flag F1 — default `false` |
| `AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` | Flag F3 — default `false` |
| `AISHOPPING_COUPONS_ENABLED` | Flag de consumo de cupons — default `false` |
| `AISHOPPING_CESAR_CORE_BASE_URL` | URL do César Core (ver seção 6) |
| `AISHOPPING_CESAR_CORE_API_KEY_FILE` | Arquivo com o Bearer do GG Oferta → César Core |
| `AISHOPPING_TELEGRAM_BOT_TOKEN_FILE` / `AISHOPPING_TELEGRAM_WEBHOOK_SECRET_FILE` | Secrets do bot (já existentes, sem mudança nesta rodada) |
| `AISHOPPING_DATABASE_*` | PostgreSQL do GG Oferta (já existente) |

Detalhe completo de todas as variáveis (não só as novas):
[`docs/installation/configuration.md`](../installation/configuration.md).

### César Core (`.env` na raiz do repositório + `.secrets/`)

| Variável | Papel |
|---|---|
| `CESAR_CORE_AI_ENABLED` / `CESAR_CORE_SEARCH_ENABLED` | Opt-in — default `false`. Sem eles, `/v1/ai/generate`/`/v1/search` respondem "não configurado" mesmo com tudo mais no ar |
| `CESAR_CORE_AI_USER_MODEL` | Nome do combo OmniRoute para `ai_profile=user` — **`user-cascade`** |
| `CESAR_CORE_AI_ADMIN_DEV_MODEL` | Nome do combo OmniRoute para `ai_profile=admin_dev` — **`admin-dev-cascade`** |
| `CESAR_CORE_SECURITY_GG_OFERTA_API_KEY_FILE` | Mesmo Bearer do lado do Core (par do `AISHOPPING_CESAR_CORE_API_KEY_FILE` do GG) |
| `CESAR_CORE_OMNIROUTE_AI_API_KEY_FILE` / `_SEARCH_API_KEY_FILE` / `_FETCH_API_KEY_FILE` | Credenciais do Core → OmniRoute (consumidor, diferentes das 4 connections AI abaixo) |
| `CESAR_CORE_SEARXNG_SECRET_FILE` | Segredo interno do SearXNG |
| `CESAR_CORE_SECURITY_QUOTA_REDIS_URL` | Conexão com o Redis (dentro do mesmo Compose) |

Tabela completa: seção "Configuração" do `README.md` do repositório
`cesar-core`.

### OmniRoute (dentro do volume do César Core, não é `.env`)

- Senha administrativa do painel (`/api/auth/login`).
- 4 chaves de provider AI: `gemini_user_api_key`,
  `gemini_admin_dev_api_key` (chave **diferente** da de USER),
  `groq_admin_dev_api_key`, `openrouter_admin_dev_api_key`.

Nomes de secret, procedimento completo e corpo de requisição exatos:
seção 5 deste documento e
`C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`.

### Coupon Worker (`.env` gerado por `install.ps1`)

| Variável | Papel |
|---|---|
| `AUTH_TOKEN` | Autentica o endpoint de controle local do worker |
| `COUPONS_POSTGRES_DSN` | **Obrigatória para PROD** — aponta para o mesmo PostgreSQL do GG Oferta, credencial própria. Sem ela, o worker grava só em SQLite local e o GG Oferta nunca vê os cupons coletados (ver seção 9) |

### Portas/URLs aprovadas para PROD

Este documento não define uma topologia de rede nova — a decisão
estrutural (`DEC-118` item 8, GG Oferta) é: mesmo Windows Server físico,
deployments independentes em `C:\App\AIShoppingAgent`,
`C:\App\cesar-core` e `C:\App\omniroute`, rede Docker externa
conceitual `cesar-platform` entre eles, nenhuma exposição pública do
Core ou do OmniRoute. Só o GG Oferta é público (via Tailscale Funnel,
já em uso — ver
[Instalação → Windows Server](../installation/windows-server.md)).
César Core publica só `127.0.0.1:8100` no host que o hospeda; Redis/
OmniRoute/SearXNG não têm porta publicada. Se a topologia real do
servidor divergir do que este parágrafo descreve, **pare e reporte** —
não é uma decisão que este handoff autoriza a improvisar.

## 5. OmniRoute — provider connections e combos (não estão no Git)

**Fato crítico:** connections e combos do OmniRoute vivem no
banco/volume dele, não em nenhum repositório. Isso significa que
**cada ambiente novo (PROD incluído) precisa ser provisionado do
zero**, e **nenhum UUID de connection/combo do DEV pode ser copiado
para PROD** — cada ambiente gera os seus próprios IDs ao criar os
recursos.

**Antes de provisionar, confira se já não existe** (idempotência —
não duplicar): liste as connections/combos existentes no OmniRoute de
PROD via API administrativa (`GET /api/providers`, `GET /api/combos`)
autenticado com a senha administrativa de PROD. Se as 4 connections e
os 2 combos abaixo já existirem com os nomes certos, pule para a
verificação (passo final desta seção).

**4 connections a garantir que existam** (nomes exatos, um provider
cada):

| Nome | Provider | Modelo aprovado |
|---|---|---|
| Gemini USER | `gemini` | `gemini-3.6-flash` |
| Gemini ADMIN_DEV | `gemini` | `gemini-3.6-flash` |
| Groq ADMIN_DEV | `groq` | `openai/gpt-oss-120b` |
| OpenRouter ADMIN_DEV | `openrouter` | `openrouter/free` |

Duas chaves Gemini **distintas** (USER e ADMIN/DEV nunca compartilham
credencial). Nenhum modelo `latest`/preview — os quatro acima são os
únicos aprovados.

**2 combos a garantir que existam** (`strategy: "priority"`):

- `user-cascade` = Gemini USER → `oc/mimo-v2.5-free` (Groq/OpenRouter
  **não existem** neste combo — é assim, estruturalmente, que USER
  nunca alcança Groq/OpenRouter, não uma checagem em runtime).
- `admin-dev-cascade` = Gemini ADMIN_DEV → Groq ADMIN_DEV →
  OpenRouter ADMIN_DEV → `oc/mimo-v2.5-free`.

`oc/mimo-v2.5-free` é o fallback gratuito/no-auth do catálogo nativo do
OmniRoute (prefixo `oc/`, não `opencode/`) — não precisa de connection
nem de chave.

**Procedimento completo, com corpo de requisição JSON exato, nomes de
secret e teste de cada connection/combo isoladamente:**
`C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`.
Siga esse runbook à risca para o provisionamento em si — esta seção só
resume o que precisa existir ao final.

**Verificação final desta seção** (real, não simulada):

```
POST /api/combos/test  {"comboName": "user-cascade"}
POST /api/combos/test  {"comboName": "admin-dev-cascade"}
```

Cada passo distinto de cada combo deve responder `status: "ok"`. O
passo `oc/mimo-v2.5-free` pode ocasionalmente exceder 15–20s de
latência (característica operacional conhecida do fallback gratuito,
não falha de configuração) — repita o teste antes de investigar mais.

## 6. Feature flags

| Flag | Onde | Default | OFF (comportamento) | ON (comportamento) |
|---|---|---|---|---|
| `AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` | GG Oferta | `false` | Idêntico ao fluxo anterior à FASE G — sem bootstrap histórico externo | `run_historical_bootstrap` (F1) roda: busca referência histórica externa uma vez por produto elegível, com retry/backoff exponencial e lease contra dupla execução, revalidando a cada 90 dias |
| `AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` | GG Oferta | `false` | Avaliação de mercado (F2) usa só histórico interno, como antes | Avaliação de mercado (F3) também considera a referência histórica externa buscada pelo F1 |
| `AISHOPPING_COUPONS_ENABLED` | GG Oferta | `false` | Preço exibido/alertado nunca considera cupom, mesmo que existam cupons coletados no banco | Ofertas elegíveis (Web e alerta) calculam o melhor cupom aplicável em tempo real (`best_applicable_coupon`) e mostram o preço com desconto |
| `CESAR_CORE_AI_ENABLED` | César Core | `false` | `/v1/ai/generate` responde "não configurado" | AI real via OmniRoute, resolvida por `ai_profile` |
| `CESAR_CORE_SEARCH_ENABLED` | César Core | `false` | `/v1/search` responde "não configurado" | Search real via SearXNG |

**Ordem segura de ativação** (cada uma é independente das outras —
ative uma de cada vez, prove antes de ativar a próxima):

1. `CESAR_CORE_AI_ENABLED` + `CESAR_CORE_SEARCH_ENABLED` (dependem do
   provisionamento da seção 5 já estar feito e verificado).
2. `AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` (F1) — não depende de
   nenhuma outra flag.
3. `AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` (F3) —
   funciona melhor com F1 já ativo há um tempo (referência histórica
   populada), mas não trava se F1 estiver OFF (só não tem o que
   referenciar ainda).
4. `AISHOPPING_COUPONS_ENABLED` — independente das anteriores; precisa
   do Coupon Worker (seção 9) já rodando e gravando cupons reais para
   ter efeito visível.

## 7. Ordem de deploy

1. **Preflight** — seção 2 deste documento, nos três repositórios.
2. **Pull dos três repositórios** (fast-forward seguro, já coberto na
   seção 2).
3. **Backup do estado atual** — snapshot/backup do PostgreSQL de PROD
   antes de qualquer migration (ver
   [Backup e restauração](backup-restore.md) do GG Oferta).
4. **Migrations** (seção 3) — só GG Oferta, só depois do backup.
5. **César Core + OmniRoute + Redis + SearXNG** (mesmo Compose do
   César Core) — subir/atualizar antes do GG Oferta, já que ele é
   dependência obrigatória (fail-closed) de AI/Search/enrichment.
   Confirmar `GET /health` e `GET /ready` antes de prosseguir.
6. **GG Oferta** (api + `collection_worker` + `telegram_notifier`) —
   com todas as flags da seção 6 **ainda OFF** neste ponto. Confirmar
   `GET /health` e `GET /ready`.
7. **Coupon Worker** — instalar/atualizar o agendamento (Windows
   Scheduled Task), com `COUPONS_POSTGRES_DSN` apontando para o
   Postgres de PROD. Pode subir a qualquer momento depois do passo 4;
   não depende do César Core.
8. **Health/readiness dos três** — confirmar antes de tocar em
   qualquer flag (tabela na seção 8 abaixo).
9. **Flags OFF, prova básica** — com tudo no ar e flags ainda
   desligadas, confirmar que o comportamento é idêntico ao anterior a
   este deploy inteiro (nenhuma regressão visível com tudo desligado).
10. **Ativação gradual** — seguir a ordem da seção 6, uma flag por vez,
    com prova real entre cada uma.
11. **Gate Gemini** (seção 10) — antes especificamente da política de
    providers AI valer para tráfego real, não só para as flags de
    F1/F3/cupons.
12. **Prova funcional final** — um ciclo completo real: criar/observar
    uma missão, ver um cupom sendo considerado numa oferta elegível,
    confirmar quota USER=5/ADMIN_DEV=50 na prática, confirmar que o
    Telegram responde claramente quando a quota está cheia.

## 8. Health/readiness

| Serviço | Vivo | Pronto (dependências reais) |
|---|---|---|
| GG Oferta API | `GET /health` | `GET /ready` |
| César Core | `GET /health` | `GET /ready`, `GET /v1/capabilities` |
| Coupon Worker | — | endpoint de controle próprio (`config.json`), autenticado por `AUTH_TOKEN` |

## 9. Coupon Worker — comportamento esperado em PROD

- Roda **na mesma máquina** do GG Oferta (Playwright + Edge real via
  CDP — não containeriza, não roda em Linux).
- Usa o **mesmo PostgreSQL** do GG Oferta, via `PostgresCouponStore`
  (`COUPONS_POSTGRES_DSN`), credencial própria e independente.
- **SQLite continua em uso mesmo com Postgres configurado** — mas só
  para bookkeeping interno que o GG nunca lê (`source_candidates`,
  janela promocional/`control`). Só a tabela `coupons` vai para o
  Postgres do GG.
- **Não existe fallback silencioso Postgres → SQLite.** Se
  `COUPONS_POSTGRES_DSN` estiver definida e a conexão falhar, o worker
  levanta erro explícito (`CouponStoreIntegrationError`) e não roda —
  nunca degrada para SQLite-only sem avisar. SQLite-only só acontece
  quando `COUPONS_POSTGRES_DSN` está **ausente desde o início**
  (modo local/teste intencional, não esperado em PROD).
- **Ciclo `active`/`expired`:** só esses dois status existem.
  - *Staleness* (`expire_stale`): um cupom `active` que não é
    reconfirmado (não aparece de novo numa varredura) desde um corte de
    tempo vira `expired` — comparação de ausência no banco, sem IA.
  - *Esgotamento explícito*: quando o texto da própria página diz
    literalmente "esgotado"/"esgotada" (nunca "está esgotando", que é
    tratado como ainda válido), E o achado está num escopo confiável
    (card/produto/busca — nunca páginas de nível de loja como home/
    banners, onde o texto pode se referir a outro produto), o cupom já
    nasce `expired` nessa mesma varredura. Reaproveita o status
    `expired` existente — não é um terceiro estado.
  - *Reativação*: se um cupom marcado `expired` (por qualquer um dos
    dois motivos acima) reaparecer numa varredura futura sem o sinal de
    esgotamento, o `upsert` (mesma chave `store_id`+`code`+`evidence`)
    volta o status para `active` automaticamente — sem passo manual.
- **O worker só preserva evidência real** (`raw_rule_text`, `evidence`)
  — nunca fabrica código, valor de desconto ou decide se o cupom se
  aplica a uma oferta específica.
- **O GG Oferta resolve aplicabilidade**, não o worker:
  `best_applicable_coupon` (`backend/app/coupons/pricing.py`) decide,
  em tempo real a cada exibição de oferta (site) e no momento da
  decisão de alerta (snapshot imutável gravado no evento, nunca
  recalculado depois pelo Telegram), qual cupom entre os candidatos
  realmente reduz o preço da oferta específica.

## 10. Gate Gemini — obrigatório antes de ativar a política de providers AI

A política de providers AI (USER → Gemini USER → `oc/mimo-v2.5-free`;
ADMIN/DEV → Gemini ADMIN_DEV → Groq → OpenRouter → `oc/mimo-v2.5-free`)
foi validada em DEV com o fallback completo funcionando (cada
connection desativada/testada/reativada com sucesso), **mas o happy
path com Gemini respondendo como prioridade 1 não foi reproduzido em
DEV** — ficou bloqueado por indisponibilidade/quota real da API Gemini
gratuita usada na própria bateria de testes (confirmado: as duas
connections Gemini seguem `valid: true` isoladamente, não é problema de
credencial/config).

**Antes de considerar esta política ativa para tráfego real em PROD**,
com todas as 4 connections ativas e nenhum fallback forçado:

1. `POST /v1/ai/generate` com `ai_profile=user` → esperado
   `AIResponse.provider == "gemini"`.
2. `POST /v1/ai/generate` com `ai_profile=admin_dev` → esperado
   `AIResponse.provider == "gemini"`.

**Se qualquer uma falhar:**
- Se a causa for indisponibilidade/quota externa **da própria API
  Gemini** (não do OmniRoute, não de configuração) — é a condição já
  aceita e documentada; não é motivo para reverter a política, mas
  registre o resultado e repita mais tarde antes de confiar
  operacionalmente no happy path.
- Se a causa for qualquer outra coisa (erro de configuração, connection
  inválida, combo errado, etc.) — **isso é um blocker operacional
  real**. Não altere fila, prioridade, combo, fallback ou arquitetura
  para tentar forçar sucesso. Pare e reporte.

## 11. O que NÃO fazer

- Não copiar UUID de connection/combo do OmniRoute de DEV para PROD.
- Não usar `git reset --hard` para resolver divergência sem entender a
  causa primeiro.
- Não aplicar migration sem backup do banco antes.
- Não ativar mais de uma flag por vez sem prova real entre elas.
- Não forçar o gate Gemini a "passar" alterando arquitetura/fallback.
- Não criar tag/release como parte deste handoff — isso é decisão
  separada, não coberta aqui.

## 12. Referências (mesma arquitetura, sem redesenho)

- Arquitetura canônica GG ↔ Core: `C:\cesar-core\docs\architecture\gg-oferta-core.md`.
- Política de providers AI (detalhe completo, validação real,
  auditoria de tracing): mesmo arquivo acima, seção "Política de
  providers AI".
- Runbook de provisionamento OmniRoute: `C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`.
- Setup integrado dos três componentes (visão de instalação, não de
  deploy): [`docs/installation/integrated-setup.md`](../installation/integrated-setup.md).
- Runbook operacional geral (rotina, diagnóstico, rollback pós-deploy):
  [`docs/operations/runbook.md`](runbook.md).
- Backup e restauração: [`docs/operations/backup-restore.md`](backup-restore.md).
- Estado vivo detalhado (histórico completo de decisões): `docs/internal/project-context.md` e `docs/internal/decision-log.md` (`DEC-109` a `DEC-118`).
