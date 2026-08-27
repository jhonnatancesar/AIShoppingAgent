# Alertas de preço

A TASK-027 avalia uma nova observação persistida no contexto de uma missão ativa
e produz candidatos de evento do catálogo da V1. Ela não grava nem publica
eventos e não envia notificações.

## Regras

Na V1 (`DEC-045`), alertas monitoram o preço do produto (`amount`). Frete é
dado adicional quando conhecido e não participa do critério de alerta —
custo final com frete continua exclusivo de `app.purchase`
(recomendação/comparação, TASK-038/039), que não muda.

- `price.decreased.v1` é produzido quando `amount` diminui em relação à
  observação anterior disponível e comparável da mesma oferta e moeda —
  "anterior" é sempre escopado pela mesma missão (`DEC-048`/TASK-063): duas
  missões diferentes que coletem a mesma `Offer` avaliam cruzamento de
  alvo pela sua própria história, sem compartilhar estado uma com a outra.
- `price.target_reached.v1` é produzido quando uma observação disponível fica
  menor ou igual ao `target_amount` da missão (preço-alvo do produto, sem
  exigir frete). Após atingir o alvo, novas observações que continuem abaixo
  dele não repetem o alerta; uma nova passagem de cima para baixo pode
  alertar novamente.
- A primeira observação disponível já dentro do alvo produz o alerta; uma
  observação anterior indisponível não suprime esse fato.
- Missões fora de `active` e ofertas `unavailable` ou `unknown` não alertam.
- Moedas diferentes nunca são comparadas nem convertidas.
- Frete desconhecido **não bloqueia** alerta: a comparação usa sempre
  `amount`, nunca `total_amount` — misturar as duas bases geraria alerta
  falso quando o frete muda de conhecido para desconhecido (ou vice-versa)
  entre duas observações. Frete nulo nunca é tratado como zero ou grátis;
  ele simplesmente não participa da decisão de alertar.
- Missões sem preço-alvo ainda podem produzir o evento de queda.

Os campos legados do catálogo (`previous_total`/`current_total`/
`target_total`, em `PriceDecreasedPayload`/`PriceTargetReachedPayload`)
carregam o valor de `amount` para os alertas da V1, por compatibilidade de
contrato — não foram renomeados. `total_amount` continua existindo e sendo
persistido normalmente (`docs/architecture/price-engine.md`), mas não é a base de
comparação do avaliador.

## Relevância produto-missão (`DEC-048`/TASK-063)

`evaluate_price_alerts` em si não mudou: continua avaliando só preço,
disponibilidade e moeda. A TASK-063 acrescentou um filtro **antes** dela,
em `app.collection.orchestration._persist_success`: cada oferta só é
avaliada para alerta se `MissionOfferRelevance` para aquele
`(mission_id, offer_id)` já estiver classificada como `MATCH` (IA via
`AIProviderManager`, comparando o `search_query` da missão com o título
bruto do anúncio). `POSSIBLE_MATCH`, `NO_MATCH` e ausência de classificação
válida (IA indisponível/resposta fora do contrato) são todos tratados como
não elegíveis para alerta — comportamento conservador. A classificação é
feita uma única vez por par e cacheada (`docs/database/schema.md`); ver
`docs/tasks/TASK-063.md` para o desenho completo. Preço, disponibilidade e
moeda nunca são decididos pela IA — continuam vindo exclusivamente da
coleta.

## Checkpoint por Mission+Product e avaliação de mercado (TASK-113)

A regra acima ("`price.decreased.v1` sempre que `amount` cai vs. a
observação anterior") permanecia verdadeira só enquanto nunca existia
`MissionProductAlertState` para aquele `(mission_id, product_id)`
(nunca houve alerta antes) — sem isso, uma Mission podia ser alertada
repetidamente por quedas locais que nunca batiam um preço já visto
como melhor (ex.: R$3.900 alertado → sobe para R$4.500 → cai para
R$4.199,99 → alerta de novo, mesmo pior que R$3.900). `evaluate_price_
alerts` (`app.alerts.evaluator`) ganhou um parâmetro opcional,
`checkpoint: AlertCheckpoint | None` — `None` preserva exatamente o
comportamento descrito acima (chamado de "caminho A" no desenho); um
checkpoint existente exige, além da queda local, um dos dois caminhos
adicionais:

- **Caminho B** — `current` materialmente melhor que `checkpoint.
  best_notified_amount` (`app.alerts.material_improvement`, `clamp(
  reference_amount × percent, min_amount, max_amount)`, valores em
  `Settings.material_improvement_*`).
- **Caminho C** — re-alert de oportunidade: o checkpoint está
  REARMADO (`rearmed_at` setado quando o preço sobe materialmente
  acima do último alerta, `should_rearm`), já passou a janela mínima
  (`Settings.realert_*_hours`, resolvida por `app.collection.cadence.
  resolve_product_market_mode` — o mesmo modo NORMAL/PROMO_CALENDAR/
  HIGH_ACTIVITY usado para o TTL do assessment abaixo), **e** existe
  um `MarketPriceAssessment` atual classificado `GOOD_DEAL`/
  `EXCELLENT_DEAL` — sem avaliação externa favorável, o caminho C nunca
  dispara.

`MissionProductAlertState` (`app.alerts.models`, PK `(mission_id,
product_id)` — nunca `offer_id`, para que o mesmo produto vendido em
duas lojas diferentes dentro da mesma Mission compartilhe o mesmo
checkpoint) é lido e escrito dentro da MESMA seção crítica que já
existia em `_persist_phase_c` (`app.collection.orchestration`) —
`SELECT missions ... FOR UPDATE` no topo da função já serializa toda a
decisão por `mission_id`, então nenhum lock adicional foi necessário
sobre o checkpoint em si (provado sob concorrência real por
`tests/integration/test_alert_checkpoint.py::
test_two_stores_same_mission_product_never_double_alert`, não só por
comentário). O mesmo caminho é usado tanto pela coleta de missão única
quanto pelo fan-out compartilhado da TASK-112 (`_run_phase_b`/
`_persist_phase_c` são reaproveitados sem alteração de import por
`app.collection.shared_collection._process_pending_fan_out`).

`MarketPriceAssessment` (`app.market_research`) é a avaliação de
mercado externa (Firecrawl + IA), compartilhada por `product_id`
(nunca por Mission/usuário) — dez Missions monitorando o mesmo Product
disparam no máximo uma pesquisa externa; cada uma decide seu próprio
alerta de forma independente. Desenho completo (gatilho determinístico,
single-flight com lease, TTL, quórum de evidência, fallback `/v2/scrape`,
retry/circuit breaker) em `docs/tasks/TASK-113.md` §33/§39 — não
duplicado aqui.

O resultado é um `PriceAlertCandidate` validado contra `app.events`.
Persistência e publicação foram implementadas na TASK-043
(`app.events.service.publish_event`) — quem tiver um `PriceAlertCandidate`
desempacota seus campos (`event_type`, `aggregate_type`, `aggregate_id`,
`payload`) na chamada. A TASK-044 implementou o consumo genérico at-least-once
por consumidor (`docs/architecture/event-consumption.md`); o consumidor concreto e a
notificação Telegram foram implementados na TASK-036. A TASK-062 invoca
automaticamente avaliação e publicação após persistir cada lote; quando o
evento durável existe, `telegram_price_alerts_v1` o entrega e registra o
resultado.
