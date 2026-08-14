# TASK-083 — Confiabilidade da canonicalização de produto: verificação em camadas

Status: **Concluída e aprovada (2026-08-14)**. Suíte completa verde,
integração PostgreSQL verde, rebuild/recreate dos serviços de aplicação
concluído sem regressão, validação real feita (grounding bloqueado nesta
chave/projeto — ver limitação registrada abaixo; fallback via
`ProductIdentityResolver` confirmado com Kabum real). Commit local feito;
sem push/tag.

Dependência: estende `TASK-075`/`TASK-074` (`IntentInterpreter`, canonicalização,
campo `model`) sem reabri-las. **TASK-082 continua não iniciada** — o gate
interino `criteria.model is None` mencionado nela segue como estava.

## Problema (caso real relatado)

Entrada do usuário: `"9800X3D"`. Resposta observada da IA em uma interação
real: equivalente a `"Ryzen 9 9800X3D"` — tecnicamente incorreto (9800X3D
pertence à linha Ryzen 7; 9950X3D/9900X3D é que são Ryzen 9). A mesma auditoria
que motivou esta TASK confirmou que o mesmo termo já havia recebido, em outra
interação, uma resposta correta — ou seja, não é um bug determinístico de
código, é a consequência esperada de uma canonicalização que dependia
inteiramente do conhecimento treinado da IA, numa única chamada, sem nenhuma
verificação externa.

## Arquitetura final implementada

```
Mensagem do usuário
        │
        ▼
IntentInterpreter (1ª chamada, Gemini normal, purpose=interpret_purchase_intent)
        │
        ▼
model_confidence == "baixa"?  OU  contradição determinística conhecida?
OU  input bare + enriquecimento não comprovado (detect_unproven_enrichment)?
        │
   não ──┴── sim
   │          │
   │          ▼
   │   Verificação via AIProviderManager, require_search_grounding=True
   │   (2ª chamada, Gemini dedicado ao grounding, purpose=verify_product_identity)
   │          │
   │   grounding_requested + grounding_performed + grounding_sources não vazio?
   │          │
   │     sim ─┴─ não/falha/indisponível
   │      │          │
   │      ▼          ▼
   │  usa identidade   fallback seguro: search_query = model
   │  corrigida        (nunca mantém a canonicalização suspeita)
   │      │                 │
   └──────┴─────────────────┘
              │
              ▼
     Intent devolvido ao webhook (Playwright NUNCA entra aqui)
              │
              ▼
   Mission criada normalmente (search_query pode ainda ser o model cru)


Ciclo do collection_worker (assíncrono, run_batch):
  Fase A (transação curta)
    → ensure_missing_schedules / recover_stale_runs / claim_due_collections
    → ClaimedCollection passa a carregar também criteria.model
  [transação fecha]
  Fase B (fora de qualquer transação, TASK-083)
    → detecta missões com identidade ainda crua (same_code(search_query, model))
    → ProductIdentityResolver.resolve(model), no máximo 1x por mission_id
      → Kabum (dedicado) → se inconclusivo/falhar → Amazon (dedicado) → None
    → aplica o resultado em memória a todos os ClaimedCollection da missão
  Fase C (fan-out normal, asyncio.gather, inalterado)
```

## Gemini normal vs Gemini 2.5 Flash (grounding) — roteamento no `AIProviderManager`

- `AIRequest.require_search_grounding: bool = False` — campo novo, default
  compatível com toda chamada existente.
- `UserAIProviderManager` e `AdminDevAIProviderManager` passam a aceitar um
  `grounding_provider: AIProvider | None` opcional. Quando a requisição pede
  grounding, o manager usa **exclusivamente** esse provider — nunca o Gemini
  normal, nunca Groq (que nunca teve essa capability). Sem `grounding_provider`
  configurado, levanta `AIProviderCapabilityUnsupported("search_grounding")`
  de forma tipada, sem tentar nenhum tier.
- Os dois builders (`build_user_ai_provider_manager`,
  `build_admin_dev_ai_provider_manager`) constroem uma **segunda instância**
  de `GeminiProvider` (mesma API key do perfil, `model =
  Settings.gemini_grounding_model`, default `"gemini-2.5-flash"`) — nunca
  duplicação de provider, só uma segunda instância parametrizada.
- Circuit breaker: a chave já inclui o `model` (`ai:{provider_id}:{model}:generate`)
  — como o modelo de grounding é diferente do normal, o isolamento já existe
  estruturalmente, sem nenhum mecanismo novo. Provado por teste real: esgotar
  o circuito do grounding não afeta uma chamada normal subsequente no Gemini
  comum.
- `AIResponse` ganhou `grounding_requested`/`grounding_performed`/
  `grounding_sources`. `grounding_performed` só é `True` quando a API
  devolve evidência estrutural real (`grounding_metadata.web_search_queries`
  e/ou `.grounding_chunks` não vazios) — disponibilizar a ferramenta nunca é
  tratado como equivalente a "a busca aconteceu".

## Detectores determinísticos (sem IA, sem rede) — `backend/app/intent/nomenclature.py`

- `check_known_family_contradiction`: guardrail pequeno e explícito só para o
  caso real que motivou a TASK (família AMD Ryzen X3D) — nunca a defesa
  principal.
- `detect_unproven_enrichment`: defesa geral — para uma entrada que já é
  essencialmente só o código/modelo (sem outras palavras técnicas do
  usuário), qualquer token técnico que a IA tenha acrescentado ao
  `search_query` sem vir da mensagem original é tratado como inferido, não
  confirmado. Funciona para **qualquer** modelo, conhecido ou não — não
  depende de tabela cadastrada (provado com um código inventado sem
  correspondência real).
- `model_confidence` (`"alta"`/`"baixa"`/`null`): autorrelato da própria IA na
  chamada de interpretação, usado como **um entre vários sinais**, nunca como
  única defesa.
- Gatilho de verificação (`_needs_identity_verification` no
  `IntentInterpreter`): só aciona quando `model_confidence == "baixa"` OU
  contradição conhecida OU enriquecimento não comprovado em entrada bare.
  Nunca para busca genérica (`model is None`) nem por padrão.

## Fallback seguro

Quando a verificação é necessária mas grounding falha, tem quota excedida,
timeout, indisponibilidade, não executa busca de fato, ou não retorna fontes
— o `IntentInterpreter` **nunca mantém** a canonicalização já classificada
como suspeita. Reduz `search_query` ao próprio `model` (`_apply_safe_fallback`).
Exemplo real observado nesta validação: entrada `"9800x3d"`, IA respondeu
`"Ryzen 9 9800X3D"` (contradição detectada), grounding indisponível →
resultado final `search_query = "9800X3D"`, `model = "9800X3D"` — nunca
`"Ryzen 9"`.

## `ProductIdentityResolver` — Kabum → Amazon

`backend/app/collection/identity_resolution.py` (`StoreProductIdentityResolver`):

- Contrato mínimo: `ProductIdentityResolver.resolve(model: str) ->
  ResolvedProductIdentity | None`, com `ResolvedProductIdentity(model,
  search_query, source)` — nenhum campo além desses três.
- Instâncias **dedicadas** de `KabumProvider`/`AmazonProvider` — nunca as
  registradas no `CollectionAdapter` da coleta normal: `max_offers=5`
  (limite máximo inicial, não obrigação — encerra assim que encontra
  correspondência forte), `availability_fallback_max_candidates=0`,
  `circuit_namespace="identity"` (isolado de `store:{fonte}:search` da coleta
  normal — provado por teste que falhas num nunca abrem o outro), timeout de
  navegação de 6s por tentativa.
- Orçamento: `per_provider_timeout_seconds=8.0` por provider
  (`asyncio.wait_for`), orçamento global = soma dos orçamentos individuais
  (16s para os dois) — um único mecanismo de deadline, sem camada de retry
  nova por cima do que a cadeia Kabum→Amazon já faz
  (`RetryPolicy(max_attempts=1)` nas instâncias dedicadas).
- Matching: reutiliza exclusivamente `title_matches_model`/`model_search_pattern`
  da TASK-075 (extraídos para `app/collection/model_matching.py`, módulo
  compartilhado, comportamento idêntico preservado). Nunca aceita SKU vizinho
  (`7800X3D`/`9900X3D`/`9800X` para uma busca de `9800X3D`); tolera separador
  (`9800-X3D`/`9800 X3D`).
- `search_query` do resultado nunca é o título comercial inteiro: é truncado
  deterministicamente até o fim do trecho que casou com o modelo (sem IA, sem
  invenção) — validado com um título comercial real na validação desta
  subetapa (ver abaixo).
- Nunca roda dentro do caminho síncrono do webhook Telegram/`IntentInterpreter`
  — só dentro do `collection_worker`, decisão arquitetural confirmada
  explicitamente pelo usuário durante a implementação.

## Resolução fora de transação, uma vez por `mission_id`

`CollectionOrchestrator.run_batch`: a Fase B (`_resolve_identities`) só é
alcançada depois que o `async with session.begin()` da Fase A já saiu de
escopo — estruturalmente impossível chamá-la com a transação ainda aberta,
provado por teste com um `session.begin()` que rastreia estado. Deduplicação
por `mission_id` via `set` — uma missão com 4 fontes aciona o resolver uma
única vez; o resultado é aplicado em memória (`dataclasses.replace`) a todos
os `ClaimedCollection` daquela missão antes do `asyncio.gather` do fan-out.
`identity_resolver` é opcional (`None` preserva o comportamento anterior
integralmente) — o `collection_worker` de produção injeta
`StoreProductIdentityResolver()` real; `IntentInterpreter` nunca recebeu essa
dependência.

## Ausência de persistência / cache

Nenhuma tabela, coluna, índice ou cache persistente nesta TASK.
`MissionCriteria.search_query`/`.model` nunca são escritos pela resolução —
o resultado vive só como variável local dentro de `run_batch()`, descartado
ao final do ciclo. Confirmado por teste que `_resolve_identities` não recebe
`session` como parâmetro (estruturalmente impossível persistir) e que nenhuma
chamada extra a `session.execute`/`session.scalar` acontece.

**Evolução futura registrada, não implementada**: uma tabela de
identidade/alias resolvido (`model` normalizado → identidade verificada,
similar ao padrão já usado por `MissionOfferRelevance`/`Product.display_name`)
poderia evitar repetir IA/busca para identidades já resolvidas. Decisão de
implementação de uma TASK futura — precisa definir chave de cache,
invalidação e volume real antes de qualquer migration.

## Resultado da validação real (2026-08-14)

**Gemini grounding**: acionado corretamente (contradição detectada,
`model_confidence = "baixa"`), request `require_search_grounding=True`
confirmado usando o provider dedicado `gemini-2.5-flash` (nunca o Gemini
normal nem Groq). **Falhou com `404 NOT_FOUND`**, mensagem literal devolvida
pela API do Google: `"This model models/gemini-2.5-flash is no longer
available to new users. Please update your code to use a newer model..."`.

Auditoria adicional feita para não presumir a causa: `client.models.list()`
(chamada de catálogo, sem gerar conteúdo) com a **mesma chave ADMIN/DEV**
mostra que **tanto `models/gemini-2.5-flash` quanto
`models/gemini-2.5-flash-lite` aparecem listados como existentes** para este
projeto/chave. Testado `gemini-2.5-flash-lite` como candidato alternativo de
grounding (uma única chamada real, `require_search_grounding=True`,
`"9800X3D"`) — **mesmo erro `404`, mesma mensagem literal da API**, só
trocando o nome do modelo citado.

**Conclusão precisa (sem extrapolar)**: para a chave/projeto ADMIN/DEV atual,
os dois modelos aparecem no catálogo mas a chamada de geração é recusada pela
própria API com a mensagem acima — **não fica comprovado se a causa é
descontinuação geral do modelo, uma restrição específica desta chave/projeto,
ou outro motivo de permissão/plano**; só a mensagem literal da API está
comprovada. `gemini-2.5-pro` (também presente no catálogo) não foi testado —
fora do escopo desta auditoria, por instrução explícita de não tentar outros
modelos além dos dois já aprovados. **Limitação registrada, não corrigida
nesta subetapa** — precisa de decisão do usuário sobre qual modelo Gemini usar
para grounding (`Settings.gemini_grounding_model` já é um campo de
configuração isolado exatamente para essa troca ser barata; nenhuma mudança
de código foi necessária, já é opcional/configurável).

O fallback seguro funcionou corretamente diante dessa falha real: `search_query`
final = `"9800X3D"`, `model = "9800X3D"` — nunca a canonicalização suspeita.
Uma tentativa adicional com o modelo normal (`gemini-3.6-flash`) + grounding
foi tentada só para diagnóstico e recebeu `429` (cota excedida) — não repetida,
conforme instrução de economizar chamadas reais.

**`ProductIdentityResolver` real** (já que o grounding não resolveu): `resolve("9800X3D")`
executado diretamente, sem missão, sem persistência. **Kabum resolveu no
primeiro candidato examinado** (Amazon nunca foi chamada) em ~7,4s — dentro do
orçamento de 8s por provider. Título real da loja: `"Processador AMD Ryzen 7
9800X3D, 4.7GHz (5.2GHz Max Turbo), Cache 8MB, 8 Núcleos, 16 Threads, AM5, Sem
Vídeo Integrado - 100-100001084WOF"`. Resultado: `search_query = "Processador
AMD Ryzen 7 9800X3D"`, `model = "9800X3D"`, `source = "kabum"` — truncamento
determinístico removeu clock/cache/núcleos/soquete/part number, mantendo uma
`search_query` limpa e utilizável, sem inventar nada que não estivesse no
título original.

## Validação estática/local

- Suíte completa (não-integração): 1073 passed, 1 skipped, cobertura 90,96%.
- Ruff (check + format) limpo em todos os arquivos desta TASK.
- Integração PostgreSQL real (`tests/integration/test_collection_orchestration.py`,
  12 testes incluindo os casos de concorrência/deadlock da TASK-079): todos
  verdes, via cópia temporária do runner que pula só o sub-passo `alembic
  check` (drift pré-existente e não relacionado, já documentado na
  TASK-079/080 — constraints de enum em `mission_transitions`/`stores`/
  `users`, tabelas que esta TASK não toca).
- `alembic heads` == `alembic current` == `20260811_0001` (um único head, sem
  drift novo). **Zero migration** criada por esta TASK.
- `docker compose config` válido.

## Runtime Docker

Rebuild da imagem (`api`, `telegram_notifier`, `collection_worker`) e
recriação apenas desses três serviços (`--no-deps`), preservando PostgreSQL e
volumes. Todos healthy, `RestartCount=0`, logs sem erro/traceback. Worker
confirmado vivo e processando lotes reais via métrica
`aishopping_worker_batches_total{worker="collection_orchestrator"}` (5 lotes
concluídos nos primeiros segundos após o restart, sem nenhuma missão devida
nesse intervalo — silêncio esperado, não falha). `docker top` confirma
`docker-init` como processo raiz do `collection_worker` (init: true
preservado da TASK-081) e nenhum processo zumbi (`Z`) na checagem leve feita
após a validação real do `ProductIdentityResolver`.

## Limitações conhecidas / pendências

1. **`gemini-2.5-flash` e `gemini-2.5-flash-lite` indisponíveis para geração
   nesta chave/projeto ADMIN/DEV** (ambos aparecem no catálogo via
   `models.list()`, mas `generate_content` recusa os dois com `404` e a
   mesma mensagem literal da API — causa exata não comprovada, ver seção de
   validação real acima) — grounding real não pôde ser confirmado ponta a
   ponta; precisa de decisão do usuário sobre qual modelo usar
   (`Settings.gemini_grounding_model`, já configurável, nenhuma mudança de
   código pendente para trocar o valor).
2. Resolução de identidades de múltiplas missões no mesmo `run_batch` é
   sequencial (não paralela) — simples e correto, mas não é a opção de menor
   latência possível para lotes com muitas missões precisando de resolução
   simultaneamente; não otimizado nesta TASK por decisão deliberada de manter
   o mecanismo simples.
3. Cache/alias persistente de identidade — registrado como evolução futura,
   não implementado.
4. TASK-082 (limite de candidatos em busca genérica) continua não iniciada;
   nenhuma sobreposição de responsabilidade foi introduzida.

## Arquivos alterados (resumo)

`backend/app/ai_provider/{contracts,gemini,groq,manager}.py`,
`backend/app/intent/{contracts,interpreter}.py`,
`backend/app/intent/nomenclature.py` (novo),
`backend/app/collection/{contracts,orchestration,worker}.py`,
`backend/app/collection/providers/base.py`,
`backend/app/collection/model_matching.py` (novo),
`backend/app/collection/identity_resolution.py` (novo),
`backend/app/core/config.py`,
`backend/scripts/validate_intent_interpreter.py`,
mais os arquivos de teste correspondentes (novos e estendidos) em `tests/`.
