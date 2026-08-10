# TASK-064 — Revisar disponibilidade e fallback dos provedores de IA

Status: Auditoria concluída e escopo final decidido pelo usuário em
2026-08-09 (ver "Decisão final do usuário" abaixo); aguardando autorização
explícita para implementar. **Não implementado ainda.**

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
| Groq (2ª camada, opcional) | `llama-3.3-70b-versatile` | `Settings.groq_model` |
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
