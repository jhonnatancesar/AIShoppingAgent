# TASK-124 — Cupom novo não avisava/atualizava histórico quando o preço de tabela ficava igual (`UNCHANGED_REUSED` bloqueava tudo)

Status: **Concluída nesta sessão (2026-09-21)** — código, testes e
documentação prontos, commitada localmente. Deploy real em PROD
pendente (ver "Ação no deploy" abaixo); tag nova só depois que
TASK-125/126/127 também fecharem (decisão do usuário).
Achado real reportado pelo usuário: o 9800X3D saiu por R$ 2.249 na Kabum
com o cupom `CPUPROMO` (recorde histórico), o Coupon Worker coletou o
cupom normalmente, mas o GG nunca avisou nem aplicou o desconto —
apesar de o preço de tabela (sem cupom) ter ficado idêntico ao ciclo
anterior.

## Problema (causa raiz confirmada no código)

`backend/app/collection/orchestration.py` marca uma oferta como
`PriceObservationComparison.UNCHANGED_REUSED` quando o estado comercial
bruto (preço, moeda, disponibilidade, parcelamento) é idêntico ao da
última `PriceObservation` da mesma `Offer` — nesse caso a observação
antiga é reaproveitada, nenhuma linha nova é criada (TASK-093). Antes
desta correção, **três pontos diferentes** do pipeline estavam
condicionados a `alert_comparison is not UNCHANGED_REUSED`, sem
exceção nenhuma para cupom:

1. **Fase B — busca de cupom** (`_run_phase_b`, antes da correção):
   nem chegava a consultar se havia cupom aplicável quando o preço de
   tabela não mudou.
2. **Fase B — gatilho de pesquisa de mercado** (`evaluate_trigger_and_
   maybe_research`, F2/F3, que alimenta o Caminho C do avaliador —
   reoportunidade/re-arm): mesma condição, mesmo bloqueio.
3. **Fase C — avaliador de alerta** (`_persist_phase_c` →
   `evaluate_price_alerts`, `app/alerts/evaluator.py`): também não
   rodava, então nem o Caminho B (recorde histórico, matemática pura)
   nem o Caminho C (reoportunidade, via IA) eram avaliados.

Ou seja: um cupom pode surgir ou mudar num ciclo em que o preço BRUTO
anunciado continua idêntico (exatamente o caso relatado — Kabum manteve
o "de/por" igual, só o cupom `CPUPROMO` era novo), e isso sozinho pode
tornar o preço EFETIVO mais baixo — merecendo alerta e atualização de
histórico — mas o pipeline inteiro nem chegava a considerar essa
possibilidade.

## Correção aplicada

- **`orchestration.py:2385-2394`** (Fase B, busca de cupom): a consulta
  de cupom (`get_candidate_coupons_for_offer` + `best_applicable_
  coupon`) deixou de depender de `alert_comparison`, condicionada só a
  `effective_relevance is MATCH` e `settings.coupons_enabled`.
- **`orchestration.py:2423-2433`** (Fase B, gatilho F2/F3): nova
  variável `has_new_coupon_despite_unchanged_price` (`alert_comparison
  is UNCHANGED_REUSED and applied_coupon is not None`) passa a liberar
  `evaluate_trigger_and_maybe_research` mesmo com preço de tabela
  igual, sempre que um cupom foi encontrado — usa
  `applied_coupon.final_amount` como `current_amount` do gatilho, não
  `pending.amount`.
- **`orchestration.py:2642-2668`** (Fase C, avaliador): mesmo guard
  `has_new_coupon_despite_unchanged_price`, desta vez usando
  `ai_outcome.applied_coupon`. Como `previous.id == current.id`
  (`pending.observation_id`) é estruturalmente proibido pelo
  `_validate_entities` do evaluator (`PriceAlertEvaluationError`), o
  `previous.id` sintético usa `uuid4()` só neste caso específico —
  confirmado por busca no código que `previous_observation_id` nunca é
  dereferenciado por nenhum consumidor (Telegram/webapp só leem
  `observation_id`/`current.id`, sempre a linha real reaproveitada).

Nenhuma mudança no cálculo de cupom em si (`app/coupons/pricing.py`,
determinístico, já correto) nem no F1 (`historical_bootstrap`, roda
independente de `alert_comparison`, não estava afetado).

## Achado adicional (flags nascendo `False` sem decisão do usuário)

Ao investigar por que `coupons_enabled` sequer estava ativa em PROD,
confirmou-se que as três flags de feature ligadas a esta área
(`historical_bootstrap_enabled` F1, `market_research_external_
reference_enabled` F3, `coupons_enabled`) nasceram com `default=False`
numa sessão anterior (FASE G, `DEC-116`) por decisão **unilateral do
assistente**, nunca pedida pelo usuário — o que deixou incerto por dias
se `coupons_enabled` estava sequer ligada em PROD, mascarando este bug
real atrás de uma pergunta de configuração que ninguém tinha decidido
de fato.

Correção explícita do usuário (2026-09-21, ver memória
`feedback_never_assume_flag_default_always_ask`): as três flags devem
subir **ativas** — `backend/app/core/config.py:390,395,401` agora tem
`default=True` nas três, com comentário explicando o achado e a
correção. Regra registrada como padrão permanente: o assistente nunca
decide sozinho se uma flag nova nasce ativa ou não, sempre pergunta
antes.

Como PROD hoje **não tem nenhuma variável de ambiente sobrescrevendo**
`AISHOPPING_HISTORICAL_BOOTSTRAP_ENABLED` /
`AISHOPPING_MARKET_RESEARCH_EXTERNAL_REFERENCE_ENABLED` /
`AISHOPPING_COUPONS_ENABLED` (confirmado em `docs/operations/
prod-deployment-handoff.md`), as três vão herdar `True` automaticamente
assim que este código for implantado — sem passo manual extra.
Sequência de deploy combinada com o usuário (ver seção "Ação no
deploy" abaixo).

## Escopo

Os dois itens acima, juntos: (1) desacoplar cupom de
`UNCHANGED_REUSED` nas três frentes do pipeline; (2) mudar o default
das três flags de `False` para `True`. Nenhuma mudança em `app/coupons/
pricing.py`, `app/alerts/evaluator.py` (lógica de decisão em si) ou
`app/market_research/service.py` — só os pontos de gating em
`orchestration.py` e o default das flags.

## Fora de escopo

**TASK-125** (o gráfico de histórico de preço nunca mostra o preço com
cupom aplicado, só o preço de tabela) — mesma origem do relato do
usuário, mas é uma superfície diferente (query de série histórica, não
o pipeline de alerta), tratada como TASK separada por decisão do
usuário.

## Validação

- `tests/test_collection_orchestration_async.py`: 2 testes novos —
  `test_run_phase_b_triggers_market_research_when_coupon_found_despite_unchanged_price`
  (prova o cenário exato relatado: preço de tabela igual + cupom novo →
  gatilho F2/F3 dispara com o preço já descontado) e
  `test_run_phase_b_skips_market_research_when_unchanged_price_and_no_coupon`
  (contraprova: sem cupom aplicável, `UNCHANGED_REUSED` continua sem
  disparar nada — preserva o controle de custo de IA já existente,
  nenhum gatilho extra em todo ciclo parado). 205 testes no arquivo,
  todos passando.
- 5 testes existentes ajustados pelo impacto do novo default `True`
  (`tests/integration/test_market_research.py` ×2,
  `tests/test_webapp_offers_router.py` ×2 — nova fixture
  `client_with_coupons_disabled` —, `tests/test_market_research_service_async.py`
  ×1) — cada um agora liga explicitamente `_SETTINGS_FLAGS_OFF`/
  `coupons_enabled=False` onde o teste é especificamente sobre o
  caminho "flags desligadas", que deixou de ser o comportamento padrão.
- `python scripts/run_integration_tests.py tests/integration/
  test_market_research.py tests/integration/test_coupons.py`: 38
  passed (banco Postgres real).
- `tests/test_collection_orchestration.py` (síncrono): 54 passed.
- `ruff check`/`format`: limpos em todos os arquivos tocados.

**Gap de cobertura conhecido, registrado honestamente**: os testes
acima são unitários (`tests/test_collection_orchestration_async.py`,
Fase B e Fase C mockadas separadamente) + integração dos módulos de
cupom/pesquisa de mercado isolados
(`tests/integration/test_market_research.py`/`test_coupons.py`) — não
existe um teste de integração com banco real cobrindo o pipeline
inteiro (Fase A→B→C) com cupom + `UNCHANGED_REUSED` juntos, ponta a
ponta, simulando uma coleta real do início ao fim. Não bloqueia o
deploy (a cobertura por fase já prova cada ponto de decisão
isoladamente), mas a confirmação total desse caminho específico só vai
dar pra saber de verdade observando um ciclo real de coleta em PROD,
depois da ativação (ver "Ação no deploy" abaixo) — anotar o resultado
real quando isso acontecer.

## Pendências

- Sem tag nova cortada — só depois que TASK-125/126/127 também
  fecharem (decisão do usuário, 2026-09-21).

## Ação no deploy (decisão do usuário, 2026-09-21)

1. Sobe o código em PROD — as três flags já vêm `True` automaticamente
   (sem passo manual, ver "Achado adicional" acima).
2. PROD (worker de coleta) continua **parada** nesse momento — mesma
   pausa já em vigor desde o crash do TASK-123 (`v1.3.24`), aguardando
   autorização do usuário.
3. Roda o script de deduplicação e vínculo de itens do TASK-123
   (`reprocess_unresolved_product_identity.py --apply`) com a PROD
   ainda parada.
4. Só depois disso o usuário autoriza religar a PROD.

Importante: **não é** "manter as flags desligadas até o passo 3" — é
"manter a PROD parada até o passo 3". As flags já sobem ativas no passo
1, sem depender de nenhum passo posterior.
