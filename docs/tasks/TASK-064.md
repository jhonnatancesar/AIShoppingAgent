# TASK-064 — Revisar disponibilidade e fallback dos provedores de IA

Status: Auditoria concluída em 2026-08-09; aguardando autorização explícita
do usuário para implementar. **Não implementado ainda.**

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

## Proposta de nova ordem de cascata (aguardando aprovação, nada implementado)

Duas opções, dependendo da resposta à pergunta sobre faturamento:

**Opção A — se a chave não tem/não vai ter acesso real ao nível "Pro":**
```
gemini-3.5-flash (estável, 1ª tentativa)
→ Groq llama-3.3-70b-versatile (já configurado)
→ gemini-3.6-flash (gratuito atual, última tentativa)
```
Duas chaves/modelos Flash distintos como 1ª e 3ª camada dão alguma
diversificação de cota mesmo sendo a mesma família, sem depender de "Pro"
que hoje não responde.

**Opção B — se o usuário confirmar/ativar faturamento e quiser manter uma
camada "Pro" de verdade:**
```
gemini-pro-latest (GA, sem sufixo "preview"; reteste após confirmar cota)
→ Groq llama-3.3-70b-versatile
→ gemini-3.6-flash
```
Mesma estrutura de 3 camadas atual, só trocando o nome do modelo preview
pelo GA equivalente — menor mudança possível, mas só funciona se a cota
"Pro" existir de verdade.

Em ambos os casos: `AISHOPPING_GEMINI_PREMIUM_MODEL` já é configurável via
`Settings.gemini_premium_model` — a troca é só uma env var, sem alterar
`AdminDevAIProviderManager` nem `GeminiProvider`.

## Pergunta para o usuário antes de implementar

**A chave Gemini ADMIN/DEV (`AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`) tem
faturamento habilitado no projeto Google, ou é uma chave gratuita sem
billing?** Isso decide entre a Opção A (assumir que "Pro" não é viável e
usar dois modelos Flash estáveis) e a Opção B (manter uma camada "Pro" GA,
já que a mudança de preview→GA é a menor possível). Se não tiver certeza,
a Opção A é a mais segura para aplicar já, sem depender de uma resposta.

## Fora do escopo desta TASK

- Alterar a tag `v1.0.0` ou tocar em `main`/`origin/main`;
- Implementar batching (só proposta, se necessário, depois da correção);
- Alterar a semântica MATCH/POSSIBLE_MATCH/NO_MATCH da TASK-063;
- Qualquer implementação antes da autorização explícita do usuário.
