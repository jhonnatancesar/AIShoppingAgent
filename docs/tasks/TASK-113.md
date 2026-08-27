# TASK-113 — Avaliação inteligente de preço, pesquisa de mercado e qualidade dos alertas

Status: **DESENHO FECHADO (2026-08-27) — pronta para implementação
futura, nenhuma linha de código/migration escrita ainda.** O pré-flight
(§32) foi executado (3 agentes de auditoria read-only) e revisado pelo
usuário em duas rodadas de correção; o resultado final, já aprovado,
está em **§33 — Desenho final aprovado**, que é a fonte de verdade para
implementação (supersede qualquer proposta conflitante em §1-31, que
ficam como contexto/motivação original). Quando o usuário mandar
executar esta TASK, **não repetir o pré-flight** — implementar
diretamente a partir de §33, salvo achado concreto que contradiga o
desenho. Ver §36/§37 para o histórico de registro/fechamento.

## 1. Problema atual

Hoje o sistema pode alertar simplesmente porque houve uma queda em relação
à observação anterior, mesmo que o preço atual seja pior do que um preço
muito melhor que o usuário já recebeu anteriormente.

Exemplo real do comportamento indesejado:

```
R$ 3.900,00  → usuário recebe alerta.
Preço sobe para R$ 4.199,99 → sem alerta.
Preço sobe para R$ 4.500,00 → sem alerta.
Depois cai para R$ 4.199,99 → sistema pode considerar uma nova queda e alertar.
```

Isso está errado para um monitor de promoções.

Também não queremos:

```
R$ 3.900,00 → alerta.
R$ 3.899,99 → novo alerta por melhoria de R$ 0,01.
```

`PriceObservation` deve continuar registrando mudanças comerciais reais,
inclusive pequenas. O problema está na decisão de **ALERTAR**, não no
histórico.

## 2. Objetivo

Separar claramente:

- **HISTÓRICO COMERCIAL** → registra mudanças reais.
- **AVALIAÇÃO DA OFERTA** → determina se o preço atual realmente parece bom.
- **DECISÃO DE ALERTA** → determina se vale incomodar aquela Mission/usuário novamente.

O sistema deve parar de interpretar "caiu desde a última observação" como
"é uma nova promoção que merece alerta".

## 3. Referências de preço

O desenho deve considerar pelo menos três conceitos distintos:

- **`previous_price`** → preço comercial anterior. Útil para detectar
  `CHANGED` e calcular queda recente.
- **`historical_best`** → melhor preço comercial conhecido pelo nosso
  próprio histórico.
- **`last_notified_best`** → melhor preço relevante pelo qual ESTA Mission
  já recebeu alerta.

A decisão de alerta não deve depender somente de `previous_price`.

## 4. Checkpoint individual por Mission

O histórico comercial pode ser compartilhado. Mas o estado de alerta é
individual. Duas Missions podem monitorar o mesmo produto com targets
diferentes, stores diferentes, histórico de alertas diferente, critérios
diferentes.

Precisamos conseguir responder: "qual foi o melhor preço sobre o qual esta
Mission já foi avisada?"

Não criar estado por usuário se a granularidade correta for Mission.
Auditar modelos existentes antes de criar campo/tabela nova. Reaproveitar
`MissionOfferRelevance`/checkpoints existentes se fizer sentido.

> **Superseded por §33.4/§33.5** — auditado: `MissionOfferRelevance`
> `(mission_id, offer_id)` é estreito demais (offer é por loja, o mesmo
> produto em duas lojas tem `Offer`s diferentes). Decisão final: tabela
> nova `MissionProductAlertState`, chave `(mission_id, product_id)`.

## 5. Melhoria material

Não queremos alerta por centavos. Deve existir conceito de MATERIAL
IMPROVEMENT.

Exemplo:

```
último melhor alertado: R$ 3.900,00
novo: R$ 3.899,99 → não alertar.
novo: R$ 3.890,00 → provavelmente não.
novo: R$ 3.850,00 → pode merecer novo alerta.
```

NÃO fixar agora uma regra arbitrária sem auditar o sistema. Durante o
preflight futuro, avaliar uma política combinando: melhoria percentual;
melhoria mínima absoluta; preço alvo; classificação externa de mercado;
contexto do histórico local. Valores precisam ser configuráveis. Não
espalhar números mágicos.

## 6. Pesquisa externa — Firecrawl

A pesquisa online existente a ser utilizada é Firecrawl. Não introduzir
Perplexity ou outro provider sem necessidade.

Responsabilidade:

- **Firecrawl** → pesquisar/coletar/extrair evidências externas.
- **AI Provider Manager** → interpretar essas evidências.

Não usar IA como crawler. Não usar Firecrawl em toda coleta.

## 7. Gatilho para pesquisa externa

Firecrawl + IA são operações mais caras. Só devem ser chamados quando o
sistema local detectar um candidato potencialmente interessante.

Exemplos conceituais de gatilho: queda percentual relevante; nova mínima
local; cruzamento do target; preço materialmente melhor que
`last_notified_best`; outra evidência forte definida no preflight.

Exemplo: `R$ 2.599,99 → R$ 2.199,99` (queda aproximada de 15%) pode
justificar validação externa. Já `R$ 2.199,99 → R$ 2.199,98` não deve
disparar pesquisa externa.

A regra final deve ser determinística. **IA NÃO decide se devemos chamar
IA.**

## 8. Market Price Assessment

Avaliar durante o preflight a criação de um conceito persistente
equivalente a `MarketPriceAssessment`. Possíveis informações:
identity/monitoring identity avaliada; preço da oferta que originou a
avaliação; timestamp; menor preço atual encontrado; faixa normal de
mercado; menor preço histórico externo encontrado, SE houver evidência;
classificação; confiança; evidências/fontes; validade/expiração;
contexto/store quando necessário.

Não assumir agora que todos esses campos precisam existir. Auditar antes.

## 9. Classificação externa

Preferência inicial por classificação simples:

```
EXCELLENT_DEAL
GOOD_DEAL
NORMAL_PRICE
INSUFFICIENT_EVIDENCE
```

Evitar score complexo na primeira versão. A classificação deve ser
baseada em evidências coletadas. Nunca inventar "menor preço histórico"
sem fonte que realmente sustente isso. Se histórico externo não estiver
disponível: `historical_low = desconhecido`, mas ainda pode ser possível
dizer "bom preço em relação ao mercado atual".

## 10. Fontes / evidências

A avaliação precisa manter rastreabilidade suficiente. Persistir ou
referenciar evidências importantes, conforme arquitetura real.

A IA deve receber: resultados Firecrawl; preço atual; identidade
canônica; histórico local relevante; eventualmente preço anterior/target.
Ela deve retornar estrutura validável. Não aceitar texto livre como única
fonte de verdade se o sistema precisa tomar decisão automática depois.

## 11. Cache da avaliação

Uma pesquisa externa não deve ser repetida por cada Mission, cada
usuário, cada centavo de variação, cada `CollectionRun`. Se uma avaliação
recente já existe para a mesma identidade comercial, reutilizar.

Exemplo:

```
14:00: R$ 2.199 → Firecrawl + IA → GOOD_DEAL → salva avaliação.
15:00: R$ 2.189 → avaliação ainda válida → reutiliza → zero Firecrawl → zero IA externa adicional.
```

## 12. Granularidade do cache

Avaliar cuidadosamente a granularidade correta. Preferência conceitual:
`MonitoringItem`/identidade comercial determinística compartilhável. Não
usar Mission como unidade do `MarketPriceAssessment` se vários usuários
estão avaliando o mesmo produto. Mas preservar constraints — ex.: "RTX
5070 Ti ANY brand" não necessariamente compartilha a mesma avaliação de
"RTX 5070 Ti ASUS TUF" se o mercado/preço dessa variante é diferente.
Usar Product Identity Engine existente. IA nunca decide equivalência.

> **Superseded por §33.1** — auditado: `MonitoringItem` representa a
> NECESSIDADE de monitoramento (pode ser `ANY`/family, gera N `Product`s
> diferentes por trás do mesmo item — exatamente o risco de contaminação
> citado no parágrafo acima). A chave correta, confirmada no modelo real
> (`Offer.product_id` → `Product.identity_key`, TASK-097), é
> **`product_id`**, nunca `monitoring_item_id`.

## 13. Validade / TTL

`MarketPriceAssessment` não pode ser eterno — preço de mercado muda.
Durante preflight avaliar TTL configurável. Direção inicial (NÃO fixar
sem análise): NORMAL pode durar mais (ex. 12-24h); PROMO_CALENDAR/
HIGH_ACTIVITY validade menor (ex. 4-6h). TTL deve ser configurável.
Quando expira, a próxima oportunidade relevante pode disparar nova
pesquisa.

## 14. Reutilização quando preço muda

Avaliar se uma avaliação permanece válida quando o preço atual muda
pouco. Exemplo: assessment criado para R$ 2.199, nova oferta R$ 2.189 —
provavelmente não precisa nova pesquisa. Mas se preço/mercado mudar
drasticamente, pode justificar refresh. Definir tolerância durante
preflight.

## 15. Decisão de alerta

A decisão final deve combinar de forma determinística: preço atual;
target da Mission; `last_notified_best`; `historical_best` local;
magnitude da melhoria; `MarketPriceAssessment` válido, quando existir;
classificação/confiança externa; demais regras de relevância existentes.
Não substituir todo o evaluator atual sem auditoria — integrar com o que
já existe.

## 16. Exemplo principal

```
R$ 3.900 → alerta enviado.
R$ 4.199,99 → nenhum alerta.
R$ 4.500 → nenhum alerta.
R$ 4.199,99 → NÃO alertar apenas porque caiu de R$ 4.500.
R$ 4.100 → normalmente NÃO alertar.
R$ 3.900 → normalmente NÃO repetir o mesmo alerta imediatamente.
R$ 3.899,99 → NÃO alertar por R$ 0,01.
R$ 3.850 → avaliar se melhoria material e/ou avaliação de mercado justificam.
```

## 17. Grande queda

Exemplo: `R$ 2.599,99 → R$ 2.199,99`. Sistema local detecta queda forte.
Verifica cache: sem `MarketPriceAssessment` recente. Então Firecrawl
pesquisa mercado/fontes e o AI Provider Manager interpreta.

- Possibilidade A: mercado atual ~R$ 2.500-2.700, oferta R$ 2.199 →
  `EXCELLENT_DEAL`, forte candidato a alerta.
- Possibilidade B: mercado inteiro ~R$ 2.150-2.250, oferta R$ 2.199 →
  preço normal/bom, mas não promoção extraordinária.

Isso protege contra falso desconto do tipo "DE R$ 2.599 POR R$ 2.199"
quando R$ 2.199 já é preço comum.

## 18. Re-alerta

Existe um caso que precisa ser decidido no preflight: `R$ 3.900` foi um
ótimo preço, alertado; depois o preço sobe por semanas/meses; muito
tempo depois, `R$ 3.950` ainda pode ser excelente comparado ao mercado
atual. Não queremos necessariamente ficar em silêncio para sempre só
porque R$ 3.900 já existiu historicamente.

Avaliar política de re-alerta baseada em: tempo desde último alerta;
validade da avaliação externa; mercado atual; diferença para
`last_notified_best`; magnitude da oportunidade atual. Não implementar
cooldown temporal arbitrário sem análise.

## 19. Não duplicar pesquisa por usuário

Se 10 usuários monitoram o mesmo `MonitoringItem` e surge queda
relevante, esperado: 1 pesquisa Firecrawl compartilhada, 1 avaliação de
mercado compartilhada, depois fan-out individual — cada Mission decide
seu próprio alerta. Nunca 10 Firecrawl / 10 chamadas IA / 10 avaliações
iguais.

## 20. Concorrência / single-flight

Durante preflight verificar risco: dois workers detectam simultaneamente
que não existe assessment válido. Não queremos duplicar pesquisa
externa. Avaliar mecanismo apropriado: unique constraint; lock;
assessment status; single-flight persistente; outro mecanismo simples e
crash-safe. Não desenhar solução em memória se múltiplos workers puderem
concorrer.

## 21. Falha do Firecrawl

Firecrawl não pode quebrar a coleta principal. Se pesquisa externa
falhar: histórico comercial continua persistido; `CollectionRun` não deve
virar inválida por isso; avaliar política de retry; não disparar falso
alerta baseado em evidência inexistente. Dependendo das regras locais, o
evaluator pode decidir com dados locais ou marcar avaliação como
pendente/insufficient evidence. Decidir no preflight.

## 22. Falha da IA

Mesma filosofia. Firecrawl pode retornar dados e IA falhar. Não perder
evidências. Não repetir imediatamente em loop. Integrar com AI Provider
Manager/fallback existente.

## 23. Custo / token

Objetivo explícito: reduzir chamadas de IA e Firecrawl. Registrar
métricas úteis: assessments criados; assessments reutilizados/cache hit;
Firecrawl calls; AI assessment calls; falhas; classificação final;
alertas suprimidos por cache/last_notified/material improvement. Não
criar observabilidade excessiva.

## 24. Telegram / mensagem

Melhorar também a qualidade textual do aviso. Se evidência externa
sustentar: "Ótimo preço encontrado", "Preço abaixo da faixa atual
observada no mercado". Pode mencionar queda local: "Caiu de R$ 2.599,99
para R$ 2.199,99". Mas não afirmar "menor preço histórico" sem
evidência. Mensagem precisa continuar curta — não despejar análise da IA
inteira no Telegram.

## 25. Privacidade / segurança

Firecrawl pesquisa produto/preço. Não enviar: nome do usuário; Telegram
ID; dados pessoais; conteúdo privado da Mission desnecessário. Enviar
apenas dados comerciais necessários.

## 26. Relação com HIGH_ACTIVITY (TASK-112)

HIGH_ACTIVITY da TASK-112 é por store e controla CADÊNCIA.
`MarketPriceAssessment` controla AVALIAÇÃO DE PREÇO. Não misturar. Mas o
estado HIGH_ACTIVITY/PROMO pode influenciar TTL do assessment e
frequência de refresh externo. Nunca usar avaliação de preço para
alterar `StoreThrottle` diretamente.

## 27. Relação com TASK-093 / DEC-097

Não quebrar `FIRST_OBSERVATION`/`CHANGED`/`UNCHANGED_REUSED`. Pequena
mudança comercial continua podendo gerar `CHANGED`/histórico. O novo
evaluator decide se isso merece alerta. Não alterar dedupe apenas para
reduzir notificação.

## 28. Relação com TASK-112 shared

A nova avaliação deve aproveitar o compartilhamento: coleta comercial
uma vez; `MarketPriceAssessment` preferencialmente uma vez por
identidade válida; fan-out individual por Mission; decisão de
notificação individual. Isso deve continuar compatível com o modelo
shared.

## 29. Relação com target

Não assumir "cruzou target = sempre Firecrawl". Exemplo: target R$
5.000, preço já estava R$ 4.800, cai para R$ 4.799,99 — não precisamos
gastar pesquisa externa por R$ 0,01. Target é um dos sinais; magnitude/
materialidade continua importante.

## 30. Preço à vista / parcelado

Auditar representação comercial atual antes de decidir qual preço entra
no `MarketPriceAssessment`. O projeto já possui regras de preço à
vista/parcelado. Não misturar os conceitos. Pesquisar/avaliar a mesma
base comercial que o alerta está comparando. Parcelamento pode continuar
sendo reforçado quando realmente valer a pena, conforme decisões
existentes.

## 31. Variantes / identidade

Pesquisar a variante correta. Exemplo: "RTX 5070 Ti" não pode coletar
evidência de RTX 5070, RTX 5080, notebook com 5070 Ti e usar como
comparação direta. Firecrawl results precisam passar por validação de
identidade/relevância. Product Identity Engine continua determinístico.
IA pode interpretar evidências, não autorizar merge de identidade.

## 32. Pré-flight obrigatório quando esta TASK for executada

**Esta seção fica salva dentro da TASK.** Quando o usuário futuramente
mandar executar esta TASK, NÃO começar implementando. Primeiro fazer o
seguinte PRE-FLIGHT:

**A.** Auditar fluxo atual completo: Collection → `PriceObservationComparison`
→ evaluator → `MissionOfferRelevance` → alert decision → notification/
outbox → checkpoint.

**B.** Identificar exatamente por que hoje ocorre alerta no cenário: 3900
alertado, 4500, 4199,99 → novo alerta. Mostrar código/estado que causa
isso.

**C.** Auditar todos os checkpoints atuais: previous observation;
historical minimum; last notified; prelist; notification;
`MissionOfferRelevance`.

**D.** Verificar se já existe algum estado que possa representar
`last_notified_best` sem criar nova tabela.

**E.** Auditar integração atual com Firecrawl: cliente; limites;
configuração; uso atual; retry; cache; autenticação; parsing; testes.

**F.** Auditar AI Provider Manager: qual provider/profile deve avaliar
preço; fallback; structured output; custo/tokens; timeout.

**G.** Auditar Product Identity Engine/`MonitoringItem` para definir a
chave correta do `MarketPriceAssessment`.

**H.** Auditar concorrência: dois workers; mesma identidade; mesmo
assessment ausente/expirado.

**I.** Auditar preço à vista/parcelado e qual representação comercial
deve ser comparada externamente.

**J.** Auditar schema e migrations atuais antes de propor novas
entidades.

**K.** Avaliar comportamento de re-alerta após muito tempo.

**L.** Avaliar thresholds de material improvement.

**M.** Verificar como TTL pode reagir a NORMAL/PROMO/HIGH_ACTIVITY.

**N.** Verificar se Firecrawl realmente consegue obter histórico externo
confiável das fontes disponíveis. Não prometer "preço histórico" se as
fontes só fornecerem preço atual.

**O.** Verificar ToS/robots/regras dos provedores utilizados pelo
Firecrawl. Sem bypass de anti-bot.

**P.** Levantar métricas/custo estimado: quantas chamadas Firecrawl/IA
teríamos com as quotas atuais e com cache.

### Resultado do pré-flight futuro

Antes de implementar, quando a task for executada, apresentar:

1. causa raiz dos alertas ruins atuais;
2. modelo atual de checkpoints;
3. arquitetura proposta;
4. schema proposto;
5. estratégia de cache;
6. chave de identidade;
7. regra de material improvement;
8. regra de re-alerta;
9. gatilho Firecrawl;
10. formato de resposta da IA;
11. TTL;
12. single-flight/concorrência;
13. fallback de falha;
14. impacto no Telegram;
15. testes necessários;
16. migrations;
17. riscos reais;
18. o que NÃO precisa ser alterado.

**PARAR depois do preflight e aguardar aprovação explícita do usuário
antes de implementar, A MENOS que o usuário mande explicitamente "faça a
TASK completa sem parar".**

## 33. Desenho final aprovado (fecha o pré-flight, 2026-08-27)

Resultado consolidado após execução do §32 (3 agentes de auditoria
read-only, código real citado por `arquivo:linha`) e duas rodadas de
correção do usuário. **Esta seção é a fonte de verdade para
implementação** — supersede qualquer proposta conflitante em §1-31.

### 33.1 Identidade e chave de cache

Confirmado no modelo real: `Offer.product_id` (FK NOT NULL) sempre
aponta para `Product`; `Product.identity_key` (TASK-097, único quando
não-nulo, `CHECK ck_products_identity_complete`) só é preenchido quando
o produto foi resolvido para uma identidade global COMPLETA e
específica — é a chave "mesmo modelo/variante exata, cross-loja"
(dedupe automático já acontece em `orchestration.py:2259-2261`, que
reaponta `offer.product_id` para o `Product` canônico quando a
identidade resolve). `MonitoringItem.monitoring_key` (TASK-112) é a
identidade da NECESSIDADE de monitoramento, deliberadamente mais ampla
(pode ser `ANY`) — um único `MonitoringItem` pode gerar N `Product`s
diferentes.

**Chave aprovada: `product_id`.** Nunca `Mission`, nunca `Offer`, nunca
`MonitoringItem` genérico/family/`ANY`. Requisitos:
- `Product.identity_key IS NOT NULL` obrigatório para disparar
  `MarketPriceAssessment` — fail-closed enquanto a identidade não
  resolveu (mesmo espírito de todo o Product Identity Engine);
- mesmo `Product` exato entre lojas diferentes reutiliza o mesmo
  assessment;
- variantes comercialmente diferentes (`Product`s diferentes) têm
  assessments independentes, nunca contaminação cruzada;
- Product Identity Engine continua autoridade determinística; IA nunca
  decide merge/equivalência de identidade.

### 33.2 `MarketPriceAssessment` — schema (uma linha corrente por `product_id`)

Sem histórico append-only na V1.2 — uma única linha mutável por
`product_id`, atualizada in-place.

| Campo | Tipo | Observação |
|---|---|---|
| `product_id` | UUID FK `products.id` RESTRICT | único (1 linha por produto) |
| `status` | enum `pending`/`processing`/`ready`/`failed` | ver §33.3 |
| `reference_price` | Numeric(19,4) | preço local que motivou a pesquisa, sempre `PriceObservation.amount` (à vista) |
| `reference_currency` | CHAR(3) | |
| `store_id` | FK `stores.id` RESTRICT, nullable | só auditoria/contexto — nunca parte da chave |
| `classification` | enum `EXCELLENT_DEAL`/`GOOD_DEAL`/`NORMAL_PRICE`/`INSUFFICIENT_EVIDENCE` | |
| `market_low`, `market_high` | Numeric(19,4), nullable | faixa de mercado atual |
| `historical_low_external` | Numeric(19,4), nullable | quase sempre `NULL` — Firecrawl não fornece histórico confiável (§33.15) |
| `confidence` | enum simples (`low`/`medium`/`high`) | ver §33.15 |
| `evidence` | JSONB | resultados Firecrawl usados + resposta estruturada da IA |
| `created_at`, `updated_at` | timestamptz | |
| `expires_at` | timestamptz, nullable | TTL, ver §33.14 |
| `lease_until` | timestamptz, nullable | ver §33.3 |
| `retry_after` | timestamptz, nullable | ver §33.18 |
| `failure_count` | int, default 0 | operacional |
| `last_error` | text, nullable, resumido | operacional, nunca payload bruto |

**Vigência decidida em código, nunca por índice parcial**:
`status == 'ready' AND expires_at > now()`. Não usar `WHERE expires_at >
now()` como predicado de índice — não é uma base válida para constraint/
índice de validade temporal.

### 33.3 Single-flight com lease (crash-safe, ownership antes da chamada externa)

Corrige o buraco do desenho anterior (lock de linha só protege linha que
já existe). Reaproveita o padrão já provado da TASK-112
(`SharedFanOutTask`: claim atômico por `UPDATE`, nunca lock de linha
presumida): a linha nasce via `INSERT ... ON CONFLICT (product_id) DO
UPDATE SET status='processing', lease_until=now()+lease_seconds WHERE
market_price_assessments.status IN ('failed') OR
market_price_assessments.lease_until < now()`, com `RETURNING` indicando
quem ganhou.

```
Worker A: INSERT/UPSERT atômico → ganha o claim → status=processing
  → Firecrawl → IA → persiste resultado → status=ready.
Worker B: mesmo product_id → encontra lease processing válido
  → NÃO chama Firecrawl → NÃO chama IA.
Se A morre: lease expira → outro worker recupera (mesmo espírito de
  `_FAN_OUT_PROCESSING_STALE_AFTER`/`recover_stale_fan_out_tasks`).
```

Config: `market_assessment_lease_seconds = 300` (5 min) — timeouts/
retries externos (Firecrawl + IA) precisam caber confortavelmente dentro
disso. Sem heartbeat por enquanto (só se necessidade real aparecer na
implementação).

### 33.4 `MissionProductAlertState` — checkpoint de alerta por Mission+Product

Tabela nova. PK `(mission_id, product_id)`. Mesmo `Product` em lojas
diferentes (Amazon/KaBuM/Magalu/...) compartilha o checkpoint dentro da
MESMA Mission; outra variante/`Product` ou outra Mission → independente.
Não colocar em `MissionOfferRelevance` (grão de `offer_id`, errado para
este domínio).

| Campo | Tipo | Significado |
|---|---|---|
| `mission_id`, `product_id` | UUID, PK composta | |
| `best_notified_amount` | Numeric(19,4) | melhor preço de TODOS os alertas já enviados para esta Mission+Product |
| `best_notified_currency` | CHAR(3) | |
| `last_notified_amount` | Numeric(19,4) | preço do alerta MAIS RECENTE |
| `last_notified_at` | timestamptz | momento do alerta lógico mais recente |
| `rearmed_at` | timestamptz, nullable | ver §33.8 (REARM) |
| `last_alert_event_id` | UUID, nullable, FK `events.id` | opcional, auditoria/idempotência |
| `updated_at` | timestamptz | |

### 33.5 Concorrência do `MissionProductAlertState` (obrigatório)

Duas lojas podem gerar oportunidade para a mesma `(mission_id,
product_id)` quase simultaneamente — a decisão de alerta precisa
serializar por essa chave: `INSERT ... ON CONFLICT DO NOTHING` (garante
a linha existir) seguido de `SELECT ... FOR UPDATE` (trava ANTES de ler
`best_notified_amount`/`last_notified_amount`/`last_notified_at`/
`rearmed_at` e decidir). Objetivo: 1 oportunidade lógica → 1 evento
durável, nunca dois workers decidindo em paralelo sobre estado obsoleto.

### 33.6 Cinco conceitos distintos — nunca fundir

| Conceito | Papel |
|---|---|
| `previous_price` | detecta mudança comercial recente (já existe) |
| `internal_historical_best` | melhor preço confiável já observado por NÓS para este `product_id` (novo, §33.7) |
| `best_notified_amount` | melhor preço pelo qual ESTA Mission já foi alertada (novo, §33.4) |
| `last_notified_amount`/`last_notified_at` | último alerta enviado a esta Mission (novo, §33.4) |
| `MarketPriceAssessment` | contexto de mercado externo recente, compartilhado (novo, §33.2) |

### 33.7 Internal historical best — critério de comparabilidade

`INTERNAL_HISTORICAL_BEST` = mínimo de `PriceObservation.amount` (nunca
`total_amount`/parcelado) entre `Offer`s do mesmo `product_id` — mas
**não cegamente qualquer `Offer`**. Reaproveitar os critérios de
legitimidade já existentes no projeto antes de aceitar uma observação
como candidata: condição do produto (`OfferCondition`, excluir usado/
recondicionado/open-box), disponibilidade, moeda comparável, relevância
já classificada (`MissionOfferRelevance`/`classify_offer_relevance`),
seller/fulfillment quando aplicável. Nunca deixar um anúncio usado ou de
seller inválido virar o piso histórico e contaminar todos os alertas
futuros.

### 33.8 Re-alert e REARM

**Achado da correção**: "tempo + material improvement vs
`best_notified`" sozinho não resolve o cenário real (preço volta a subir
por meses e depois cai para um valor ainda pior que o melhor histórico,
mas ainda bom hoje) — precisa de um mecanismo de REARM explícito.

Fluxo: após um alerta, `rearmed_at = NULL`. Se o preço depois se afasta
materialmente PARA CIMA do `last_notified_amount` (subida ≥
`rearm_rise_percent`, inicial **5%**, configurável), o estado é
rearmado (`rearmed_at = now()`) — isso NÃO gera alerta, só habilita um
re-alert futuro. Subida pequena não rearma.

### 33.9 Regra final de decisão de alerta

Três caminhos:

- **(A) Primeiro alerta** — nunca houve alerta para esta `(mission,
  product)`. Se as regras já existentes (target, relevância) permitirem
  → pode alertar.
- **(B) Novo melhor preço material** — já houve alerta; `current` é
  materialmente melhor (§33.10) que `best_notified_amount` → candidato
  forte.
- **(C) Re-alert de oportunidade** — `current` não bate `best_notified`,
  mas `rearmed_at IS NOT NULL` **E** passou a janela mínima (§33.11)
  **E** `MarketPriceAssessment` atual sustenta `GOOD_DEAL`/
  `EXCELLENT_DEAL` **E** as regras da Mission/target permitem.

Após qualquer alerta novo: `last_notified_amount = current`,
`last_notified_at = now()`, `best_notified_amount = min(anterior,
current)`, `rearmed_at = NULL`.

Isso resolve o exemplo original (3900 alerta → 4500 rearma → 4199 no
mesmo dia NÃO alerta, janela mínima não venceu → meses depois 3950 pode
alertar se o mercado atual confirmar).

### 33.10 Material improvement (valores iniciais)

`required_improvement = clamp(reference_amount × 1%, R$2,00, R$50,00)`;
material quando `reference_amount − current_amount >= required_improvement`.
Config: `material_improvement_percent = 0.01`,
`material_improvement_min_amount = 2.00`,
`material_improvement_max_amount = 50.00` — nunca espalhar esses números
pelo código, um único ponto de configuração.

### 33.11 Janela de re-alert

`realert_normal_hours = 168` (7 dias); `realert_promo_hours = 48`
(PROMO_CALENDAR/HIGH_ACTIVITY). Nunca decidido por IA (aritmética de
data). A janela sozinha **nunca** gera re-alert — exige também
`rearmed_at` setado e oportunidade comercial válida (assessment GOOD/
EXCELLENT quando o preço não supera `best_notified`) — impede alerta
periódico só porque passou tempo.

### 33.12 Target explícito

Continua sinal forte para o **primeiro** alerta (regra atual
preservada). Depois do primeiro alerta, target sozinho não autoriza
alerta repetitivo a cada centavo — passa pelo `MissionProductAlertState`/
regra de re-alert/material improvement como qualquer outro candidato.

### 33.13 Gatilho para Firecrawl (determinístico, pré-filtro local)

Dispara pesquisa externa quando **qualquer** destes for verdadeiro:
1. queda ≥ `market_research_trigger_drop_percent` (inicial **5%**) vs
   `previous_price`;
2. novo `internal_historical_best` por melhoria material;
3. preço materialmente melhor que `best_notified_amount`;
4. potencial re-alert: `rearmed_at` setado + janela mínima vencida +
   preço numa região potencialmente interessante;
5. cruzamento de target sem assessment recente, quando avaliação externa
   realmente agregar valor.

IA nunca decide se Firecrawl é chamado.

### 33.14 Cache hit e refresh antecipado

Antes de disparar Firecrawl: existe assessment `ready` e válido
(`expires_at > now`) para este `product_id`? → reutiliza, zero chamada
nova. Refresh antecipado permitido (mesmo dentro do TTL) se
`abs(current − reference_price) / reference_price >=
market_assessment_price_refresh_percent` (inicial **5%**) — ex.: R$2199
→ R$2189/R$2190 reaproveita; R$1899 é mudança grande o suficiente para
justificar refresh.

### 33.15 TTL do assessment

`market_assessment_ttl_normal_hours = 24`;
`market_assessment_ttl_promo_hours = 6` (mesmo valor para
PROMO_CALENDAR e HIGH_ACTIVITY). Como o assessment é por `Product`
(cross-loja), o TTL usa o modo **mais agressivo entre as lojas
RELEVANTES/ATIVAS** daquele produto — se PROMO_CALENDAR estiver ativo
(já é global) OU qualquer loja com oferta ativa (não obsoleta/morta)
daquele `product_id` estiver em HIGH_ACTIVITY → 6h; senão 24h. Nunca usa
só "a loja que disparou" como autoridade — reaproveita
`resolve_collection_cadence` já existente, consultada por loja
relevante, sem sistema novo.

### 33.16 Evidência mínima do Firecrawl

Para classificar `GOOD_DEAL`/`EXCELLENT_DEAL`: mínimo **2 evidências de
preço comparáveis em 2 domínios distintos** (nunca 5 páginas do mesmo
domínio contando como 5 mercados independentes). 1 fonte válida →
`INSUFFICIENT_EVIDENCE`. 2 fontes → `confidence` no máximo `MEDIUM`. 3+
fontes consistentes → pode retornar `HIGH`.

### 33.17 O que é "preço comparável"

Antes de aceitar uma evidência externa para `market_low`/`market_high`:
mesma identidade de produto; moeda BRL; produto novo (não refurbished/
open-box/usado); não é acessório/bundle diferente nem categoria
diferente (ex. notebook quando o produto é GPU desktop, salvo bundle
explicitamente comparável); preço monetário realmente extraível do
texto; forma de pagamento comparável à base `PriceObservation.amount`
(à vista). Se a fonte não deixa claro que é comparável → não usar para
`market_low`/`market_high`. IA interpreta evidência; Product Identity
Engine continua decidindo identidade.

### 33.18 Firecrawl: `/v2/search` + `scrape` básico condicional

Endpoint não muda hoje (`/v2/search` continua o primeiro passo), mas o
fluxo ganha um segundo passo condicional:
1. `/v2/search` primeiro.
2. Extrair evidências comparáveis (§33.17) dos resultados.
3. Se ≥2 fontes válidas já apareceram → não precisa scrape adicional.
4. Se <2 → permitir `scrape` **básico, não-stealth**, de no máximo 3 URLs
   promissoras de domínios distintos retornados pela própria busca.

Regras absolutas, sem exceção: sem proxy evasivo, sem stealth, sem
bypass anti-bot, sem CAPTCHA solving, só páginas públicas; 403/429/
bloqueio → respeitar e desistir daquela fonte específica (nunca
contornar). Se mesmo assim restar <2 preços comparáveis →
`INSUFFICIENT_EVIDENCE`.

### 33.19 Firecrawl: retry / circuit breaker

Hoje a chamada Firecrawl não tem nenhum dos dois (achado do pré-flight
original) — entra no escopo desta TASK. Retry limitado + backoff/jitter
para: timeout, 429, 5xx. Nunca retry automático para: 400, 401, 403 de
bloqueio/autorização, payload inválido deterministicamente. Reaproveitar
a infraestrutura de circuit breaker já existente (`app/core/
resilience.py`, `CircuitBreaker`/`CIRCUITS`) se tecnicamente aplicável à
chamada Firecrawl; se isso exigir um framework novo grande, não fazer —
usar em vez disso: retries limitados, `status='failed'`, `retry_after`,
e registrar como dívida separada a adição de circuit breaker completo.
Nunca criar loop de chamadas.

### 33.20 Firecrawl/IA nunca quebram o `CollectionRun`

Regra já aprovada, reforçada: `Offer`/`PriceObservation` persistem
independentemente; falha de Firecrawl ou de IA nunca invalida o
`CollectionRun` comercial. Assessment pode ficar `failed`/
`INSUFFICIENT_EVIDENCE`. Para um candidato muito forte/target explícito
(caminho A/B de §33.9), o evaluator pode decidir com dados locais sem
esperar o assessment. **Para re-alert (caminho C) de preço PIOR que
`best_notified`, é obrigatório um assessment externo `GOOD_DEAL`/
`EXCELLENT_DEAL`** — sem isso, nunca re-alerta, evitando que uma falha
externa produza um re-alert duvidoso.

### 33.21 Atomicidade: alerta + outbox

Confirmado no código real: `_persist_phase_c`
(`backend/app/collection/orchestration.py:2050`) já abre uma única
transação que cobre `evaluate_price_alerts` (puro) e
`publish_event_async` (grava o `Event`, outbox append-only) no mesmo
loop. `last_notified` = **"alerta lógico duravelmente enfileirado"**
(o `Event` já commitado), nunca "Telegram entregue". Na mesma
transação: lock de `MissionProductAlertState` (§33.5), decisão final,
criação do evento, atualização de `best_notified_amount`/
`last_notified_amount`/`last_notified_at`/`rearmed_at`/
`last_alert_event_id` — tudo no mesmo commit. Depois, entrega ao
Telegram/retry é responsabilidade exclusiva do mecanismo de outbox já
existente (`EventConsumptionAttempt`) — falha de transporte nunca gera
nova decisão comercial nem novo alerta lógico.

### 33.22 Assessment não é alerta global

`MarketPriceAssessment` é compartilhado (por `product_id`); a decisão de
alerta continua individual por Mission (target/regras próprias). Nunca
gravar algo como "alert=true" dentro do assessment — fan-out entre
Missions compatíveis continua decidindo cada uma por si.

### 33.23 `HIGH_ACTIVITY` não muda de responsabilidade

Continua controlando CADÊNCIA (TASK-112), nunca decide alerta
diretamente nem altera `StoreThrottleState`. Único efeito permitido
sobre esta TASK: influenciar o TTL do assessment (§33.15), como já
acontecia na proposta original.

### 33.24 Migrations finais propostas

Uma migration aditiva: tabela nova `market_price_assessments` (§33.2,
índice único em `product_id`) + tabela nova `MissionProductAlertState`
(§33.4, PK composta). **Sem** coluna nova em `MissionOfferRelevance`
(revertido de uma proposta intermediária). Upgrade/downgrade completos.
Mission sem `MissionProductAlertState` = "nunca alertado pelo novo
mecanismo".

**Pendência explícita para a implementação**: auditar se dá para
reconstruir determinísticamente parte do estado de alerta antigo a
partir de `Event`s/histórico de notificação já existente, para não
gerar tempestade de alertas repentina após o deploy só porque todo
mundo "nunca foi alertado" pelo mecanismo novo. Se não for possível
reconstruir com segurança, propor política de bootstrap conservadora
(ex.: semear `best_notified_amount` com o preço mais recente conhecido
por Mission+Product antes de ativar o novo evaluator, nunca deixando o
sistema achar que ninguém nunca recebeu nada) — decisão final na
implementação, não fechada aqui.

### 33.25 `.env.example`

Corrigir dentro desta TASK o gap já encontrado no pré-flight original:
documentar `AISHOPPING_FIRECRAWL_API_KEY` em `backend/.env.example`
(sem valor real), seguindo o padrão já usado pelas outras chaves de IA.

### 33.26 Documentação a atualizar durante a implementação

`docs/tasks/TASK-113.md` (este arquivo, manter atualizado com o real
implementado); `docs/architecture/price-alerts.md` (novo modelo de
decisão); documento de arquitetura da integração Firecrawl (criar se
ainda não existir um dedicado); `docs/internal/decision-log.md` (nova
DEC para as decisões arquiteturais desta seção); `docs/internal/
project-context.md` (padrão do projeto). Deixar explícito nesses
documentos: `PriceObservation` ≠ decisão de alerta; `previous` ≠
`best_notified` ≠ `MarketPriceAssessment` — três conceitos que nunca se
substituem (§33.6).

### 33.27 Testes críticos (além dos já listados no pré-flight original)

PostgreSQL real, cobrindo pelo menos: mesmo `Product` em duas lojas
(KaBuM alertado, Amazon depois não re-alerta); sequência 3900→4500→4199,99
sem novo alerta; 3900→3899,99 sem alerta; 3900→3850 com melhoria
material; 3900 alertado→4500 rearma→4199 no mesmo dia sem re-alert→meses
depois 3950 com assessment GOOD/EXCELLENT permite re-alert; preço parado
em 3900 por semanas nunca gera alerta periódico; duas lojas simultâneas
do mesmo Product/Mission produzem só um alerta lógico (§33.5); 10
Missions do mesmo Product compartilham 1 assessment mas decidem alerta
individualmente; dois workers sem assessment produzem só 1 chamada
Firecrawl/IA (§33.3); worker morre com lease e outro recupera após
expiração; 1 fonte de preço vira `INSUFFICIENT_EVIDENCE`; 2 domínios
válidos permitem classificação; evidência usada/refurbished/modelo
errado é descartada; assessment dentro do TTL gera zero chamada nova;
mudança ≥5% do `reference_price` permite refresh antecipado; Firecrawl
429/timeout faz retry limitado sem invalidar o `CollectionRun`; falha do
Telegram após commit do evento nunca gera novo alerta lógico; bootstrap/
migration com alertas antigos não produz tempestade de alertas.

## 34. Fora de escopo inicial

Não incluir automaticamente: machine learning próprio; previsão de preço
futuro; scraping agressivo; bypass anti-bot; browser farm; comparação
internacional; marketplace estrangeiro; modelo estatístico complexo;
plano pago específico; UI grande de histórico de mercado; recomendação
de investimento/revenda.

## 35. Critério de sucesso futuro

A task estará correta quando cenários como estes funcionarem:

```
3900 → alerta.
4500 → silêncio.
4199,99 → silêncio.
4199,98 → silêncio.
3900 novamente em pouco tempo → normalmente silêncio.
```

Queda realmente relevante (`2599 → 2199`) valida mercado usando cache ou
Firecrawl+IA. Se mercado confirmar oportunidade → alerta. Se mercado
mostrar que 2199 é preço normal → não vender isso como "promoção
extraordinária". E a mesma avaliação externa deve ser reutilizada entre
Missions compatíveis em vez de gastar Firecrawl/IA repetidamente.

## 36. Registro do backlog (2026-08-27)

Nesta rodada foi feito **apenas** o registro desta TASK no backlog:
criação deste arquivo, entrada no índice (`docs/tasks/README.md`) e linha
no roadmap (`docs/internal/roadmap.md`), status PLANNED/BACKLOG.

**Explicitamente NÃO feito nesta rodada**: nenhum preflight (§32);
nenhuma implementação; nenhuma migration; nenhuma alteração no evaluator
de alertas; nenhuma chamada a Firecrawl; nenhuma alteração no Telegram;
nenhum commit; nenhum push; nenhum deploy. Nenhum código funcional foi
tocado.

## 37. Registro do fechamento do desenho (2026-08-27, mesma data — rodadas seguintes)

Depois do registro em backlog (§36), o pré-flight (§32) foi executado
via 3 agentes de auditoria read-only (código real, citações
`arquivo:linha`, nenhum arquivo de produção alterado) e passou por duas
rodadas de correção do usuário sobre a síntese arquitetural — resultado
final consolidado em **§33**, que agora é a fonte de verdade para
implementação. Notas de "superseded" foram adicionadas em §4 e §12 onde
a proposta original conflitava com a decisão final (chave de cache e
granularidade do checkpoint de alerta).

**Explicitamente NÃO feito nesta rodada de fechamento**: nenhuma
implementação de código; nenhuma migration criada; nenhuma chamada real
a Firecrawl/IA; nenhuma alteração em `evaluator.py`/Telegram; nenhum
commit; nenhum push; nenhum deploy. Só os documentos (`docs/tasks/
TASK-113.md`) foram alterados.
