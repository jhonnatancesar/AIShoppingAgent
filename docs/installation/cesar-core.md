# Instalação — César Core (IA e Web Search)

Esta página cobre só o necessário para **este** repositório (GG Oferta)
conversar com um César Core já em execução. A funcionalidade da integração
está em [Arquitetura → Integração com César Core](../architecture/cesar-core-integration.md);
falhas, rollback e reprodução da validação estão no
[Runbook César Core](../operations/cesar-core-runbook.md). Instalar o Core em
si (Docker/Compose ou nativo, OmniRoute, SearXNG, Redis, Control Plane) é
coberto pelo **`README.md` do repositório `cesar-core`** — repositório
próprio, privado, separado deste; não é duplicado aqui.
O passo a passo ponta a ponta de DEV, incluindo a configuração dos dois lados,
ordem de subida e smoke tests, está em
`docs/integration/gg-oferta-dev.md` no repositório `cesar-core`.
A arquitetura e a topologia oficiais estão em
`C:\cesar-core\docs\architecture\gg-oferta-core.md`.

O César Core é dependência obrigatória para AI, grounding e Search. Esta
documentação continua restrita a DEV; não há rollout PROD aprovado.

O César Core é a fonte de verdade para identidade, credentials, capabilities,
policies, quotas, targets, readiness e observabilidade. O GG Oferta é somente
consumidor: configura URL, sua cópia do Bearer, metadados e flags de rollout;
não escolhe nem substitui as decisões centrais do Core.

## Pré-requisitos

- Um César Core acessível por HTTP (loopback em DEV, `http://127.0.0.1:8100`
  por padrão) — já rodando, com `AI_ENABLED`/`SEARCH_ENABLED` habilitados.
- Uma credencial de aplicação (Bearer) exclusiva para este consumidor
  (`gg_oferta`) — uma credencial dinâmica emitida pelo Control Plane ou a
  credencial legada compatível fornecida por arquivo; nunca a credencial
  upstream do OmniRoute nem a senha do Control Plane administrativo.

## Passo a passo

1. **Obtenha a credencial do consumidor.** No Control Plane do César Core
   (`/admin`) ou pela API administrativa dele, confirme que a aplicação
   `gg_oferta` está `ACTIVE`, com as capabilities (`ai`/`search`) e a quota
   que você quer usar. Credenciais dinâmicas (`cc_<id>.<secret>`) são exibidas
   uma única vez pelo Core. A credencial legada compatível não é emitida nem
   persistida pelo Control Plane: o mesmo valor é provisionado em arquivos
   locais separados no consumidor e no Core.
2. **Grave a credencial num arquivo local**, fora do Git:

   ```powershell
   Set-Content -Path .secrets\cesar-core-client-dev -Value '<credencial-emitida-pelo-core>' -NoNewline
   ```

   Veja o inventário completo de secrets em [Secrets](secrets.md).
3. **Configure o `backend/.env`** deste repositório (é esse arquivo que o
   `Settings` do backend lê em execução nativa; ver a tabela completa em
   [Configuração](configuration.md#césar-core-ia-e-web-search)):

   ```dotenv
   AISHOPPING_CESAR_CORE_BASE_URL=http://127.0.0.1:8100
   AISHOPPING_CESAR_CORE_API_KEY_FILE=C:\AIShoppingAgent\AIShoppingAgent\.secrets\cesar-core-client-dev
   ```

   As capabilities precisam estar `ACTIVE` no Core; ausência ou negação fecha
   a chamada sem fallback local.
4. **Suba/reinicie** o processo consumidor nativo (API e/ou `collection_worker`,
   conforme `AISHOPPING_CESAR_CORE_SERVICE`) para carregar a nova
   configuração. O `compose.yaml` atual não encaminha as variáveis
   `AISHOPPING_CESAR_CORE_*` nem monta esta credencial; não o trate como caminho
   integrado até existir wiring versionado específico.
5. **Verifique**, nessa ordem:

   ```powershell
   curl http://127.0.0.1:8100/health
   curl http://127.0.0.1:8100/ready
   ```

   Depois, uma chamada real pelo `AIProviderManager`/`WebSearchManager` do
   GG Oferta deve responder com `provider="cesar_core"`. Um `401`/`403`
   nessa etapa é quase sempre credencial/capability desalinhada entre os
   dois lados (passo 1), não um bug de configuração deste repositório.

Não existe rollback para providers diretos no GG Oferta. Se o Core ou sua
configuração estiver indisponível, AI, grounding e Search falham de forma
fechada; Firecrawl `/v2/scrape` continua apenas como enriquecimento.

## Política real de providers AI (`ai_profile`, resolvido em 2026-09-07)

A lacuna anterior deste documento (OmniRoute sem Gemini/Groq/OpenRouter
como provider connections reais, só o catálogo nativo gratuito) **foi
fechada nesta rodada** (`DEC-117`, commits `cab1f98` no GG Oferta e
`83d3347` no César Core). O GG Oferta declara explicitamente
`ai_profile: "user" | "admin_dev"` em cada chamada a
`POST /v1/ai/generate` (derivado do `UserRole` já rastreado
localmente, nunca escolhendo provider); o César Core resolve isso para
um dos dois combos reais provisionados no OmniRoute:

- `ai_profile=user` → combo `user-cascade` = Gemini USER →
  `oc/mimo-v2.5-free` (Groq/OpenRouter nunca alcançáveis por USER --
  ausência estrutural no combo).
- `ai_profile=admin_dev` → combo `admin-dev-cascade` = Gemini ADMIN/DEV
  → Groq → OpenRouter → `oc/mimo-v2.5-free`.

Fallback controlado e reversível validado ponta a ponta em DEV
(cada conexão desativada/testada/reativada individualmente). Detalhe
completo, incluindo o runbook de provisionamento determinístico (sem
UUIDs de ambiente nenhum, só nomes/secrets) para reconstruir isso em
outro ambiente: `C:\cesar-core\docs\architecture\gg-oferta-core.md`
(seção "Política de providers AI") e
`C:\cesar-core\docs\operations\omniroute-ai-provider-provisioning.md`.

**Validação operacional pendente (não bloqueia PROD por si só, mas é
gate obrigatório antes de ativar esta política lá):** o happy path com
Gemini respondendo como prioridade 1 (USER e ADMIN/DEV, todas as
conexões ativas) não foi reproduzido em DEV por indisponibilidade/quota
real da API Gemini gratuita durante os testes desta sessão — as
conexões seguem `valid: true` isoladamente. Antes de PROD, reexecutar
essa prova específica (ver runbook §6).
