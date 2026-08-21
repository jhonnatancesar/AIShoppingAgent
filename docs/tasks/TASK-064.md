# TASK-064 — Revisar disponibilidade e fallback dos provedores de IA

Status: **Concluída em 2026-08-10, aprovada explicitamente pelo usuário**
(ver "Encerramento" ao final).

Dependência: TASK-063 funcionalmente concluída (relevância, identidade da
oferta, formatação). A TASK-054/`v1.0.0` continua suspensa como release
final até esta TASK-064 fechar.

## Contexto

Durante a validação real da TASK-063, `gemini-3.1-pro-preview` (camada
premium do `AdminDevAIProviderManager`) teve 248 tentativas reais e 0
sucessos, com mistura de `unavailable`/`quota_exceeded`; Groq e o Gemini
gratuito também mostraram falhas sob carga. O comportamento fail-closed da
TASK-063 está correto (nunca alerta com dado inválido), mas uma cascata
degradada pode fazer o monitor legitimamente perder promoções por falta de
classificação, não por decisão de produto.

## Objetivo

Revisar a disponibilidade real dos provedores/modelos configurados no
`AIProviderManager` e deixar a cascata ADMIN/DEV adequada para uso contínuo
da V1.

## Escopo solicitado pelo usuário (2026-08-09)

1. Auditar a configuração atual (modelo premium, Groq, modelo gratuito,
   ordem da cascata, quotas/limites conhecidos, tratamento de
   `quota_exceeded`/`unavailable`/timeout/demais erros).
2. Separar claramente nos dados/logs: rate limit/quota, indisponibilidade
   5xx, timeout, autenticação, rejeição de request, falha de
   parsing/resposta inválida.
3. Validar quais modelos Gemini a chave atual realmente pode usar —
   priorizar stable/GA, evitar preview com 0% de sucesso como primeira
   camada, com chamadas mínimas de teste, sem nova carga alta.
4. Propor a melhor ordem de fallback (não alterar sem diagnóstico).
5. Avaliar a carga real da TASK-063 (operações lógicas por oferta,
   tentativas por cascata, impacto de várias ofertas novas na mesma
   coleta, se o cache já reduz chamadas posteriores).
6. Batching: não implementar por hipótese; só propor se, depois de
   corrigir cascata/modelos, ainda houver evidência real de pressão
   excessiva de quota; preservar resultado individual por
   `(mission_id, offer_id)` se um dia for necessário.
7. Não alterar a semântica da TASK-063 (MATCH elegível; POSSIBLE_MATCH e
   NO_MATCH nunca alertam; falha de IA continua conservadora; dados
   factuais nunca vêm da IA).
8. Testes/validação: cada camada da cascata isoladamente; fallback quando a
   primeira camada falha; quota/unavailable; uma coleta representativa sem
   carga artificial excessiva; confirmar melhora real da taxa de
   classificação.

## Auditoria (2026-08-09)

### 1. Configuração atual

| | Valor atual | Fonte |
| --- | --- | --- |
| Modelo premium (1ª camada) | `gemini-3.1-pro-preview` | `Settings.gemini_premium_model` |
| Groq (2ª camada, opcional) | `openai/gpt-oss-120b` | `Settings.groq_model` |
| Modelo gratuito (3ª camada) | `gemini-3.6-flash` | `Settings.gemini_model` |
| Tentativas máximas na cascata | `3` (= nº de camadas) | `Settings.safe_retry_max_attempts` |
| Timeout por chamada | `10s` | `Settings.external_http_timeout_seconds` |
| Circuit breaker | 5 falhas → abre 30s | `Settings.circuit_failure_threshold`/`circuit_open_seconds`, por `(provider, model)` |

A cascata (`AdminDevAIProviderManager.generate`, `backend/app/ai_provider/manager.py`)
tenta as camadas em ordem fixa, uma tentativa por camada (sem retry
intra-camada), parando na primeira que suceder.

### 2. Taxonomia de erro — já está separada corretamente, sem gap de código

`GeminiProvider`/`GroqProvider` (`gemini.py`/`groq.py`, funções
`_translate_api_error`) já classificam de forma idêntica e consistente
entre os dois provedores:

| Categoria pedida | Classificação atual |
| --- | --- |
| Rate limit/quota | HTTP `429` → `AIProviderQuotaExceeded` (com `quota_reset_at` do `retry-after`/`RetryInfo` quando informado) |
| Indisponibilidade 5xx | `{500, 502, 503, 504}` → `AIProviderUnavailable` |
| Timeout | `408` OU exceção de timeout do cliente → `AIProviderUnavailable("provider_timeout")` |
| Autenticação | `{401, 403}` → `AIProviderError("provider_authentication_failed")` |
| Rejeição de request | `400` → `AIProviderError("provider_request_rejected")` |
| Falha de parsing/resposta inválida (JSON fora do contrato) | **Não é erro de provedor** — `app.collection.relevance` trata como resposta bem-sucedida no provedor (`ai_outcome=succeeded` na telemetria) mas classificação/normalização inválida, logada separadamente (`offer_relevance_classification_failed`/`offer_title_normalization_failed`) |

Cada tentativa já emite `ai_provider_attempt` com `ai_purpose`,
`ai_provider`, `ai_model`, `ai_outcome`, `ai_fallback` e
`ai_quota_reset_at` — os dados já existem, distinguíveis por consulta aos
logs. **Achado**: quando o provedor responde `200` mas o JSON está fora do
contrato (ex.: chave inesperada, valor fora do vocabulário fechado), isso
aparece como `ai_outcome=succeeded` na telemetria do provedor, e só a
mensagem de aviso separada (`offer_relevance_classification_failed`) indica
que o resultado não foi utilizável — não há um único evento correlacionando
os dois. Não é uma falha do provedor (o provedor respondeu), é uma falha de
contrato de aplicação; correlacionar exige juntar duas linhas de log pelo
`trace_id`/`span_id`. Registrado como observação; não é bloqueante.

### 3. Quais modelos a chave atual realmente pode usar (chamadas mínimas, sem carga)

Uma chamada `client.models.list()` (sem custo de geração) mais duas
chamadas reais mínimas de `generateContent` (`"Responda só: ok"`), fora do
fluxo de coleta — nenhuma carga adicional parecida com a validação anterior.

- **`gemini-3.1-pro-preview` (config atual) é oficialmente `preview`** —
  confirmado pela própria listagem da API (`stable=False`), consistente com
  a taxa de 0% observada sob carga real.
- **`gemini-pro-latest` (candidato GA/"Pro" estável) falhou imediatamente
  com `quota_exceeded`** na única chamada de teste, sem nenhuma carga
  prévia hoje neste modelo específico.
- **`gemini-3.5-flash` (GA/estável, diferente do `gemini-3.6-flash` já
  usado como gratuito) respondeu com sucesso** na única chamada de teste.

**Diagnóstico principal, não hipótese**: o padrão observado (todo modelo da
família "Pro" falha — seja preview ou GA — enquanto modelos da família
"Flash" respondem) é mais consistente com **a chave atual não ter cota real
para o nível "Pro" nesta API** (comum em chaves gratuitas sem faturamento
habilitado, onde só o nível "Flash" tem cota generosa) do que com "o modelo
premium específico está degradado". Não consigo confirmar isso com certeza
sem saber se há faturamento habilitado no projeto Google da chave — **pergunta
para o usuário abaixo**. Alternativa não descartada: a cota diária
compartilhada do nível "Pro" já estava zerada pelos ~248 disparos reais de
hoje contra `gemini-3.1-pro-preview`, e um novo teste amanhã (cota
reiniciada) poderia responder diferente — não testei isso para não gerar
nova carga.

### 4. Carga real da TASK-063 (reconfirmando o que já foi medido)

- **2 operações lógicas de IA por oferta nova/não-cacheada**
  (`classify_offer_relevance` + `normalize_offer_title`), nunca por
  observação recorrente — `_resolve_offer_relevance`/`_ensure_display_name`
  (`orchestration.py`) só chamam a IA quando não existe cache válido para
  aquele `(mission_id, offer_id)`/`Product`.
- **Cache já elimina chamadas repetidas**: uma vez classificado com
  sucesso, o par nunca é reclassificado (insumos são imutáveis — ver
  `docs/tasks/TASK-063.md`), e o título normalizado também nunca é
  recalculado após o primeiro sucesso. Coletas seguintes da mesma
  oferta/missão não geram nenhuma chamada de IA.
- **Pico real observado**: ~20 ofertas novas na missão de validação, mas o
  log do container mostrou 248 operações lógicas no mesmo período — porque
  o restart do `collection_worker` fez várias missões pré-existentes
  ficarem due ao mesmo tempo (backfill), não porque uma única missão gere
  esse volume. Volume por coleta individual é proporcional ao nº de ofertas
  novas daquela busca (tipicamente 15-20 para uma busca genérica), não a
  um multiplicador da TASK-063 em si.

### 5. Batching — não implementado, sem evidência suficiente ainda

A causa dominante (camada premium com 0% de sucesso) independe de volume —
um único offer também falharia. Sem corrigir isso primeiro, qualquer
avaliação de batching ficaria contaminada pela mesma causa raiz. Adiado
para depois da correção de cascata/modelo, conforme pedido.

## Decisão final do usuário (2026-08-09) — substitui a proposta de Opção A/B

O usuário rejeitou explicitamente qualquer busca por modelo Gemini
Pro/premium alternativo (`gemini-pro-latest` incluído) e fechou a decisão
sem depender da pergunta sobre faturamento:

- **USER, ADMIN e DEV usam o mesmo modelo Gemini Flash** (o gratuito já
  configurado do projeto, `Settings.gemini_model`) para as operações
  automáticas (classificação/normalização da TASK-063). A distinção
  USER/ADMIN/DEV continua sendo só de permissão/autorização do resto do
  sistema, nunca de modelo de IA para essas tarefas.
- **Nenhum nível "Pro"/preview** entra na cascata — nem o atual
  (`gemini-3.1-pro-preview`), nem `gemini-pro-latest`, nem qualquer outro
  candidato "Pro". Não procurar mais alternativas nessa família.
- **Fallback só por disponibilidade, não por qualidade**: Gemini Flash →
  Groq → outros fallbacks já aprovados, se existirem e fizerem sentido —
  nunca dois modelos Gemini equivalentes em sequência sem necessidade.
- Memória de projeto registrada:
  `project_gemini_flash_only_v1.md` (índice em `MEMORY.md`).

### Plano de implementação decorrente (ainda não implementado)

`AdminDevAIProviderManager`/`build_admin_dev_ai_provider_manager`
(`backend/app/ai_provider/manager.py`) hoje monta 3 camadas (premium via
`gemini_premium_model`, Groq opcional, gratuito via `gemini_model`) — as
camadas 1 e 3 usam a mesma chave `gemini_api_key_admin_dev`, então depois
de remover a camada "Pro" elas ficariam idênticas. A simplificação correta
não é só trocar o nome do modelo premium — é **colapsar as camadas 1 e 3
em uma só**, resultando numa cascata de 2 camadas:

```
Gemini Flash (gemini_model, via gemini_api_key_admin_dev)
→ Groq (groq_model, se AISHOPPING_GROQ_API_KEY estiver configurada)
```

Isso elimina `gemini_premium_model`/`AISHOPPING_GEMINI_PREMIUM_MODEL` do
config (deixa de existir uma "camada premium" separada) e remove a
tentativa redundante contra o mesmo modelo Gemini duas vezes na mesma
chamada — exatamente o pedido de "auditar a cascata atual e simplificar
para evitar tentativas redundantes". `UserAIProviderManager` não muda (já
usa só `gemini_model` via `gemini_api_key_user`, sem fallback, perfil
`USER` já usa Flash hoje). Esta mudança é no `AdminDevAIProviderManager`
compartilhado — afeta tanto as chamadas automáticas da TASK-063
(`collection_worker`) quanto as chamadas interativas de ADMIN/DEV via
Telegram (`IntentInterpreter`, confirmação sim/não) que já usam essa mesma
cascata; ambas se beneficiam igualmente de não gastar uma tentativa
garantidamente perdida contra um modelo Pro/preview sem cota.

### Testes/validação a fazer (sem carga artificial)

- Cada camada isoladamente (Flash sozinho; Groq sozinho via mock/força de
  falha do Flash).
- Fallback Flash→Groq quando a primeira camada falha.
- `quota_exceeded`/`unavailable` tratados como já são (sem mudança de
  taxonomia, só menos camadas).
- Uma coleta pequena e representativa (não repetir o volume da validação
  anterior) confirmando que a taxa de classificação melhora de fato.
- Sem alterar a semântica `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH` da TASK-063.
- Sem implementar batching nesta TASK.

## Fora do escopo desta TASK

- Alterar a tag `v1.0.0` ou tocar em `main`/`origin/main`;
- Qualquer modelo Gemini Pro/preview, novo ou existente;
- Implementar batching (fica para decisão futura, só com evidência real de
  pressão de quota depois desta correção);
- Alterar a semântica MATCH/POSSIBLE_MATCH/NO_MATCH da TASK-063;
- Qualquer implementação antes da autorização explícita do usuário.

## Implementação (2026-08-09, autorizada explicitamente pelo usuário)

O usuário reforçou por escrito a decisão do DEC-050 (sem buscar/testar/
substituir por outro Gemini Pro; USER/ADMIN/DEV todos em Gemini Flash;
diferença continua sendo só permissão) e autorizou a implementação.

- **`backend/app/core/config.py`**: removido `Settings.gemini_premium_model`
  (`AISHOPPING_GEMINI_PREMIUM_MODEL`) — o config não conhece mais um nível
  "premium" separado.
- **`backend/app/ai_provider/manager.py`**: `AdminDevAIProviderManager`
  colapsado de 3 para 2 camadas. Construtor passa a receber um único
  provider `gemini` (antes: `premium` + `free` redundantes sobre a mesma
  chave) e o `groq` opcional continua como segunda camada. `generate()`
  monta `tiers = [gemini]` e só acrescenta `groq` quando configurado — sem
  terceira camada. `build_admin_dev_ai_provider_manager` monta o único
  Gemini com `Settings.gemini_model` (o mesmo Flash do perfil `USER`) em
  vez de `gemini_premium_model`. `UserAIProviderManager` não foi tocado.
- **`.env.example`, `backend/.env.example`**: removida a linha
  `AISHOPPING_GEMINI_PREMIUM_MODEL`. `compose.yaml` já não referenciava essa
  variável (nada a alterar ali).
- **Comentários/docstrings** desatualizados sobre "cascata premium/Groq/
  gratuito" corrigidos em `backend/app/telegram/router.py` e
  `backend/scripts/validate_intent_interpreter.py` (só texto, nenhuma
  mudança de comportamento).
- **Testes**: `tests/test_gemini_user_profile.py`,
  `tests/test_resilience.py` e `tests/test_ai_telemetry.py` reescritos para
  o construtor de 2 camadas — cobrindo Flash isolado, fallback Flash→Groq
  (`AIProviderQuotaExceeded`/`AIProviderUnavailable`), erro não retryable
  interrompendo a cascata sem tentar Groq, ausência de Groq configurado, e
  a telemetria/sanitização de log do fallback real. Nenhuma mudança de
  comportamento fora do escopo (semântica MATCH/POSSIBLE_MATCH/NO_MATCH da
  TASK-063 intocada).

## Validação real (2026-08-09/2026-08-10)

- **Pipeline oficial** (`scripts/check.ps1`): aprovado — 752 testes rápidos
  (753 → 752: dois testes de cascata de 3 camadas foram consolidados em um
  só de 2 camadas), 90,63% de cobertura, migration head `20260809_0004`
  inalterada, 14 integrações PostgreSQL reais aprovadas.
- **E2E reproduzível** (`scripts/run_e2e_tests.py`): 2/2 aprovados (a
  fronteira de IA continua mockada nesse suite, por design —
  `docs/development/e2e-tests.md` — então isso valida a fiação do
  `collection_worker`/`build_admin_dev_ai_provider_manager`, não a
  disponibilidade real).
- **Stack Docker reconstruído** com o código novo
  (`docker compose build api collection_worker telegram_notifier` +
  `--force-recreate`), todos os serviços saudáveis.
- **Camada Gemini Flash isolada, chamada real**: confirmado por código e
  por log que a cascata nunca mais tenta `gemini-3.1-pro-preview` nem
  qualquer variante "Pro" (zero ocorrências em logs/telemetria). Uma
  chamada real direta ao `GeminiProvider` com `gemini-3.6-flash` e a chave
  `AISHOPPING_GEMINI_API_KEY_ADMIN_DEV` retornou `quota_exceeded` real no
  momento do teste — resíduo esperado da carga real da própria validação da
  TASK-063 mais cedo no mesmo dia (a mesma chave/mesmo modelo já usado como
  camada gratuita antiga também tinha mostrado falhas sob carga, conforme
  a auditoria). Não insisti em novas tentativas além do necessário para
  confirmar o comportamento (evitar nova carga artificial), mas o sucesso
  do Flash como modelo já está documentado nesta auditoria (`gemini-3.5-flash`
  respondeu com sucesso num teste mínimo) e em validações reais anteriores
  (`docs/architecture/ai-provider-manager.md`, 2026-08-02 e 2026-08-08) — o código de
  chamada do Flash não foi alterado por esta TASK, só a ordem/composição da
  cascata.
- **Fallback Flash→Groq, chamada real**: validado de duas formas
  independentes. (1) Script isolado com Gemini forçado a falhar
  (`AIProviderQuotaExceeded`, sem chamada real) e um `GroqProvider` real
  configurado com a chave/modelo de produção — resposta real do Groq
  (`openai/gpt-oss-120b`, conteúdo `"ok"`) recebida com sucesso. (2) O
  mesmo padrão ocorreu organicamente durante a coleta real abaixo: toda vez
  que o Flash falhou (quota real ou `unavailable`), o Groq real respondeu
  como segunda e última camada — nunca uma terceira tentativa contra o
  próprio Gemini.
- **`quota_exceeded`/`unavailable` com a nova cascata**: taxonomia
  inalterada, confirmada tanto pelos testes automatizados (`AIProviderError`
  não-retryable interrompe a cascata sem tentar Groq; `quota_exceeded`/
  `unavailable` acionam fallback) quanto pelos logs reais abaixo — inclusive
  o circuit breaker (`DEC-037`) abrindo corretamente para o par
  `(gemini, gemini-3.6-flash)` depois de falhas reais consecutivas, sem
  gastar chamadas de rede adicionais enquanto aberto.
- **Coleta pequena representativa (real, Telegram/DB reais)**: missão
  descartável de validação (`user_id`/`mission_id` só para este teste, um
  único Store Provider — Kabum — consulta "teclado mecanico", sem alvo de
  preço), processada pelo `collection_worker` real. Resultado da execução:
  `collection_run.status = succeeded`. Telemetria real (`ai_provider_attempt`,
  deduplicada por `ai_request_id`, contando só o resultado final de cada
  operação lógica):

  | Operação | Total | Sucesso | Falha (`quota_exceeded`/`unavailable` nas duas camadas) |
  | --- | --- | --- | --- |
  | `classify_offer_relevance` | 20 | 15 (75%) | 5 (25%) |
  | `normalize_offer_title` | 20 | 15 (75%) | 5 (25%) |

  Todo sucesso veio do Groq (camada 2) nesta execução específica, porque a
  cota do Flash da chave ADMIN/DEV estava momentaneamente pressionada (ver
  item acima) — mesmo assim, **75% de sucesso** é uma melhora real e
  mensurável sobre o achado original da TASK-063 ("sob carga real, a
  maioria das chamadas de IA falhou" com a cascata de 3 camadas, que
  desperdiçava a primeira tentativa inteira contra `gemini-3.1-pro-preview`
  antes de sequer chegar ao Groq). As falhas restantes (5/20 em cada
  operação) são `unavailable`/`quota_exceeded` reais em **ambas** as
  camadas (Flash pressionado + Groq também rate-limited pelo mesmo burst de
  20 chamadas em poucos segundos) — tratadas de forma conservadora, sem
  persistir classificação inválida, exatamente como a TASK-063 especifica.
  Nenhuma referência a `gemini-3.1-pro-preview`/`gemini-pro-latest` em
  nenhum log desta execução.
- **Semântica da TASK-063**: intocada — nenhuma mudança em
  `app/collection/relevance.py` nem no vocabulário
  `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`.
- **Batching**: não implementado, conforme escopo.

## Encerramento

Aprovado explicitamente pelo usuário em 2026-08-10. Registro do
fechamento:

- `USER`, `ADMIN` e `DEV` usam o mesmo Gemini Flash (`Settings.gemini_model`)
  nas operações automáticas de IA (classificação de relevância e
  normalização de título da TASK-063, e as chamadas interativas de
  `ADMIN`/`DEV` no Telegram). O papel continua sendo só permissão/
  autorização, nunca escolha de modelo.
- **Cascata oficial da V1**: Gemini Flash → Groq. Nenhuma terceira camada.
- Os modelos Gemini Pro/preview (`gemini-3.1-pro-preview`, candidato GA
  `gemini-pro-latest`, ou qualquer outro dessa família) foram **removidos**
  dessa função — `Settings.gemini_premium_model`/
  `AISHOPPING_GEMINI_PREMIUM_MODEL` não existem mais no código.
- O fallback real Flash→Groq foi validado com chamadas reais (Groq
  respondendo com sucesso quando o Flash falhava).
- A validação representativa (missão descartável, uma fonte, 20 ofertas
  novas) obteve **15/20 classificações** (`classify_offer_relevance`) e
  **15/20 normalizações** (`normalize_offer_title`) com sucesso — 75% em
  ambas as operações.
- As falhas restantes do Flash por cota (`quota_exceeded`/`unavailable`)
  observadas durante a validação **ficam registradas como condição
  operacional externa** (pressão real e momentânea sobre a chave
  compartilhada, residual da própria carga de validação da TASK-063 mais
  cedo no mesmo dia) — **não como falha desta TASK**, cujo objetivo era
  corrigir a composição/ordem da cascata, não eliminar limites de cota de
  terceiros.
- Nenhuma alteração adicional de arquitetura além da cascata de 2 camadas.
  Batching não implementado (fora do escopo, permanece adiado). Semântica
  `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH` da TASK-063 intocada.
- Nenhuma outra TASK iniciada.

`docs/architecture/ai-provider-manager.md`, `docs/releases/changelog.md`, `docs/internal/project-context.md`,
`docs/internal/roadmap.md`, `docs/tasks/README.md`, `docs/releases/checklist.md` e
`AGENTS.md` foram sincronizados com este fechamento. `v1.0.0` e
`origin/main` não foram tocados.
