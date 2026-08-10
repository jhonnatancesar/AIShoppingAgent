# TASK-063 — Corrigir relevância dos resultados e apresentação dos alertas

Status: **Concluída** em 2026-08-09 — pipeline oficial, E2E reproduzível e
missão real via Telegram validados; formatação das mensagens principais
revisada; encerramento administrativo aprovado explicitamente pelo usuário.

O diagnóstico de disponibilidade dos provedores de IA (achado durante a
validação real desta TASK) foi desmembrado para a TASK-064
(`docs/tasks/TASK-064.md`, `DEC-049`) — a TASK-054/`v1.0.0` permanece
suspensa como release final até a TASK-064 fechar, não mais por causa
desta TASK.

Dependência: TASK-054 concluída (`v1.0.0`), mas a release deixa de ser
considerada definitiva até esta TASK fechar — ver "Relação com a
TASK-054/`v1.0.0`" no fim deste arquivo.

## Contexto

Antes de considerar a release V1 final, foi identificado no Telegram real
que alguns alertas mostraram preços que não pareciam corresponder ao
produto buscado, usavam o nome da missão em vez do nome real do anúncio e
não mostravam claramente o link direto da oferta.

## Objetivo

Garantir que o alerta represente o anúncio real encontrado e que o
resultado corresponda ao produto pedido pelo usuário.

## Escopo solicitado pelo usuário (2026-08-09)

1. Auditar o fluxo `StoreProvider → Product/Offer → PriceObservation →
   evaluator → evento → telegram_notifier`, confirmando onde título/URL
   reais são salvos, se preço/título/URL pertencem à mesma oferta, por que o
   Telegram usa o nome da missão, e se já existe filtro de relevância entre
   missão e anúncio.
2. Alertas devem mostrar nome real do produto/anúncio, loja, preço e link
   direto; nome da missão só como contexto secundário — nunca como nome do
   produto.
3. Usar o AI Provider Manager existente para limpar/normalizar títulos
   comerciais grandes, identificar marca/modelo/variante e verificar
   correspondência semântica entre anúncio e missão. A IA nunca pode
   alterar/inventar preço, URL, loja, disponibilidade ou moeda — esses dados
   continuam vindo exclusivamente da coleta.
4. Definir saída estruturada de correspondência (`MATCH` / `POSSIBLE_MATCH`
   / `NO_MATCH`). `MATCH` alerta, `NO_MATCH` não alerta. `POSSIBLE_MATCH`
   exige comportamento conservador — a regra deve ser proposta e aprovada
   antes de implementar. Evitar alertar acessórios, peças, categoria
   diferente ou modelo claramente diferente.
5. Revisar a formatação das mensagens principais da V1 para ficarem
   consistentes e legíveis, no formato aproximado:

   ```
   🔥 PREÇO ENCONTRADO

   Logitech G Pro X Superlight 2 — Preto

   🏪 Kabum
   💰 R$ 699,90
   🎯 Alvo: R$ 750,00
   🔎 Missão: Logitech g pro 2

   🔗 Ver anúncio
   <URL>
   ```

   Permitir preview do link quando suportado; não baixar imagem manualmente
   nesta TASK.
6. IA não formata a mensagem inteira — templates continuam no código; IA
   serve só para interpretação/normalização/relevância.
7. Se a IA falhar: usar título bruto quando aplicável, nunca inventar
   dados, não quebrar a coleta, e usar comportamento conservador para
   relevância (equivalente a `NO_MATCH`/sem alerta, a confirmar na regra do
   item 4).
8. Testes cobrindo, no mínimo: título real no alerta; preço e URL da mesma
   oferta; link direto; `MATCH` permite alerta; `NO_MATCH` bloqueia;
   acessório não passa; IA não altera dados factuais; fallback seguro
   quando IA falha; replay continua sem duplicação.
9. Validação: pipeline oficial, E2E reproduzível, uma missão real no
   Telegram, validação visual de nome/preço/loja/link.

A TASK só fecha se o alerta real permitir identificar claramente qual
anúncio gerou aquele preço.

## Fora do escopo desta TASK

- Alterar ou recriar a tag `v1.0.0`;
- Tocar em `main`/`origin/main`;
- Baixar/anexar imagem do produto;
- Qualquer implementação antes da autorização explícita do usuário.

## Auditoria do fluxo real (2026-08-09, código em `task-063-alert-relevance-formatting`, herdado de `main`-pendente via `task-054-prepare-release`)

Percorrido `StoreProvider → Product/Offer → PriceObservation → evaluator →
evento → telegram_notifier` linha a linha. Achados:

### 1. Onde o título real é salvo — e onde ele se perde

- `RawCollectedOffer.title` (`backend/app/collection/contracts.py:43`) chega
  intacto de cada `StoreProvider`, com o texto bruto exato do card/página.
- `Offer` (`backend/app/offers/models.py`) **não tem coluna de título** —
  só `product_id`, `store_id`, `seller_id`, `external_id`, `url`.
- O título só é usado em dois lugares:
  - `Product.name = item.raw_offer.title[:300]`
    (`backend/app/collection/orchestration.py:465`), **só na primeira vez**
    que aquela `Offer` é criada (identidade por
    `store_id`+`seller_id`+`external_id` ou `url`,
    `orchestration.py:453-492`). Coletas seguintes da mesma oferta **nunca
    atualizam** `Product.name`, mesmo que o texto do site mude.
  - `raw_evidence["title"] = raw_offer.title[:300]`
    (`orchestration.py:653`, dentro de `_raw_evidence`), gravado por
    observação em `PriceObservation.raw_evidence` (JSONB), mas esse campo
    nunca é lido por `evaluator` nem por `telegram_notifications`.
- `Product.name` nunca passa por limpeza/normalização — é o texto bruto do
  card truncado em 300 caracteres, exatamente o problema descrito no
  objetivo ("título comercial muito grande").

### 2. Onde a URL real é salva — esta parte está correta

- `Offer.url` (`backend/app/offers/models.py:93`) é gravada com a URL real
  do anúncio, vinda de `RawCollectedOffer.url`, e é estável por oferta
  (índices únicos por loja+vendedor+URL/`external_id`,
  `offers/models.py:51-65`). **Não há bug aqui** — a URL certa já existe e
  está a uma junção de distância (`PriceObservation.offer_id → Offer.id`).

### 3. Preço, título e URL pertencem à mesma oferta?

Sim, ao nível de dado: `PriceObservation.offer_id` referencia exatamente a
`Offer` cujo `url` é real e cujo `Product.name` (quando não for o
`raw_evidence` mais recente) é o título da primeira coleta daquela mesma
`Offer`. O problema não é mistura de dados entre ofertas diferentes — é que
**ninguém no caminho do alerta usa esses dados**: ver item 4.

### 4. Por que o Telegram usa o nome da missão

`backend/app/telegram/notifications.py`:

- `_prepare_notification` (linha 218) busca `mission.title` e chama
  `_render_alert(event, mission.title)` (linha 247) — **nunca busca
  `Offer`/`Product`/`Store`** a partir de `event.payload["offer_id"]`,
  embora `PriceTargetReachedPayload`/`PriceDecreasedPayload`
  (`backend/app/events/catalog.py:107-135`) já carreguem `offer_id`.
- `_render_alert` (linha 342) monta a mensagem só com `mission_title` +
  `current_total`/`target_total`/`currency` do payload:
  `f'🎯 Preço-alvo atingido na missão "{mission_title}".\n...'`. Não há
  nome de produto, loja nem link em nenhuma mensagem de alerta hoje.
- Ou seja: o dado certo (`offer_id`) já trafega no evento — falta só o
  notifier buscar `Offer`/`Product`/`Store` e usá-los na mensagem, em vez
  de reaproveitar `mission.title`.

### 5. Existe filtro de relevância entre missão e anúncio?

**Não existe nenhum.** Confirmado em `backend/app/collection/orchestration.py`
(`_persist_success`, linha 353+): todo item devolvido pelo
`StoreProvider` na página de busca (18–20 itens por loja, sem limite
configurado) vira sua própria `Offer`/`PriceObservation`, e
`evaluate_price_alerts` (`backend/app/alerts/evaluator.py:42`) roda
independentemente para cada uma, comparando só `current.amount <=
criteria.target_amount` — sem nenhuma verificação de que aquele item seja
de fato o produto pedido. `criteria.search_query`
(`orchestration.py:238`) é repassado literalmente ao site de busca; tudo
que a busca do site devolver é tratado como candidato válido. É por isso
que um acessório, peça ou modelo errado pode disparar alerta hoje — o
sistema nunca verificou correspondência semântica, só preço.

### Achado adicional fora do escopo descrito (registrar, não implementar aqui)

`_persist_success` busca a `previous` observação de uma `Offer` **sem
filtrar por missão** (`orchestration.py:382-387`:
`select(PriceObservation).where(PriceObservation.offer_id ==
offer.id).order_by(observed_at desc).limit(1)`). Como `Offer` é uma
entidade global (por loja), duas missões diferentes que coletem a mesma
`Offer` compartilham o "último preço visto" para fins de
`_target_was_reached`. No E2E da TASK-053, isso explica por que só 11 das
41 observações elegíveis geraram evento novo: as outras 30 já tinham
`PriceObservation` anterior de missões de teste antigas nas mesmas lojas.
Isso não é o mesmo problema do objetivo desta TASK (relevância/título/link),
mas pode causar um alerta de alvo ser suprimido (ou creditado) por causa do
histórico de **outra missão** com critério diferente. Registrado aqui para
decisão do usuário — não faz parte do escopo aprovado até agora.

## Diagnóstico

Os três sintomas relatados têm causas concretas e independentes,
confirmadas no código:

1. **"Preços não pareciam corresponder ao produto"** — não existe filtro de
   relevância; todo resultado da busca do site vira candidato de alerta
   (achado 5).
2. **"Usava o nome da missão em vez do nome real"** — o notifier nunca lê
   `Offer`/`Product` a partir do `offer_id` já disponível no evento; usa
   `mission.title` por omissão, não por decisão de produto (achado 4).
3. **"Não mostrava claramente o link direto"** — a URL real já existe e
   está correta no banco (achado 2); só nunca chega até a mensagem porque o
   notifier não a busca (mesma causa do item 2).

Nenhum dos três exige mudança de schema para os dados que já existem
(`Offer.url`, `Offer.product_id → Product.name`) — só passar a usá-los no
notifier. O que exige desenho novo é: (a) normalização do título via IA
(hoje é texto bruto truncado) e (b) o classificador de relevância
`MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`, que não existe em nenhuma forma hoje.

## Plano proposto (para autorização, nada implementado)

1. **Notifier busca a oferta real.** Em
   `_prepare_notification`/`_render_alert`
   (`backend/app/telegram/notifications.py`), buscar `Offer` → `Product` →
   `Store` a partir de `event.payload["offer_id"]` (já presente nos dois
   payloads de alerta) e usar `Store.name`/`Offer.url` na mensagem. Preço,
   URL, loja e disponibilidade continuam vindo só da coleta — nenhuma
   invenção.
2. **Título de exibição via IA, sem alterar `Product.name` bruto.**
   Introduzir um passo de normalização (novo módulo, chamando
   `AIProviderManager` como toda IA da V1) que recebe o título bruto e
   devolve um título curto de exibição (marca/modelo/variante). Guardar
   esse título normalizado separado do dado bruto (não substituir
   `Product.name`, para preservar auditabilidade e permitir reprocessar),
   com fallback explícito para o título bruto truncado se a IA falhar —
   nunca bloqueando a coleta nem o alerta.
3. **Classificador de correspondência `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`.**
   Novo passo de IA (mesmo `AIProviderManager`) comparando o critério da
   missão (busca/marca/modelo, quando disponíveis) com o título bruto do
   anúncio. Persistir o resultado por observação (auditável, não
   recalculado silenciosamente depois). `NO_MATCH` bloqueia a avaliação de
   alerta daquela observação antes de chegar em `evaluate_price_alerts`.
   `POSSIBLE_MATCH`: **regra a decidir com o usuário antes de implementar**
   — candidatas: (a) tratar como `NO_MATCH` (mais conservador, nunca
   alerta sem confirmação de correspondência) ou (b) alertar com aviso
   textual explícito de "possível correspondência" na própria mensagem. O
   item 4 do pedido original já marcou isso como pendente de proposta.
4. **Template de alerta revisado**, no formato pedido (🔥/🏪/💰/🎯/🔎/🔗),
   como string formatada no código (não gerada por IA), usando os dados
   reais buscados no passo 1 e o título normalizado do passo 2.
5. **Fallback seguro.** Se a chamada de IA (normalização ou
   correspondência) falhar: usar título bruto truncado como exibição, tratar
   relevância como `NO_MATCH` (conservador, sem alertar) e nunca derrubar a
   coleta nem o consumo do evento — mesmo padrão de resiliência já usado em
   outras integrações de IA da V1 (`AIProviderManager`).
6. **Testes** cobrindo exatamente a lista do item 8 do pedido original,
   mais os casos de fallback e replay/idempotência já exigidos pelo padrão
   do projeto.
7. **Achado adicional (previous global entre missões)**: decisão separada
   do usuário sobre se entra no escopo desta TASK ou vira TASK própria —
   não implementar sem essa decisão.

## Perguntas para o usuário antes de implementar

1. Regra para `POSSIBLE_MATCH`: tratar como `NO_MATCH` (nunca alerta) ou
   alertar com aviso textual de baixa confiança?
2. O achado adicional do "previous" compartilhado entre missões diferentes
   na mesma oferta entra nesta TASK ou fica registrado para TASK própria?
3. O título normalizado por IA deve ser persistido (nova coluna/tabela,
   auditável e reaproveitável entre observações da mesma oferta) ou
   recalculado a cada alerta sem persistir? Persistir evita custo de IA
   repetido por alerta da mesma oferta, mas é mudança de schema.

## Decisões do usuário (2026-08-09)

1. **`POSSIBLE_MATCH` nunca alerta** — só `MATCH` é elegível para
   avaliação/alerta. `POSSIBLE_MATCH` e `NO_MATCH` têm o mesmo efeito
   prático (sem alerta); comportamento conservador, sem "possível
   promoção" nesta versão.
2. **Bug do `previous` compartilhado incluído nesta TASK**, tratado como
   correção funcional (não melhoria opcional), com a menor alteração
   possível e testes específicos para duas missões na mesma oferta.
3. **Título normalizado é persistido**, mantendo bruto e normalizado
   separados e fallback para o bruto quando a IA falhar. Granularidade da
   relevância ajustada para `(mission_id, offer_id)` — não por
   `PriceObservation` — porque a mesma oferta pode ser `MATCH` para uma
   missão e `NO_MATCH` para outra; reclassificar a cada coleta seria
   desnecessário. O título normalizado fica associado a `Product` (hoje
   1:1 com `Offer`, sem deduplicação entre lojas), porque não depende da
   missão.

## Modelagem final

- `products.display_name` (`varchar(300)`, opcional): título normalizado
  por IA, nunca substitui `products.name` (título bruto da primeira
  coleta). `NULL` até a normalização ter sucesso pela primeira vez —
  nunca recalculado depois (os insumos não mudam:
  `RawCollectedOffer.title` de uma oferta já criada é fixo). Notifier usa
  `products.name` como alternativa enquanto `display_name` for `NULL`.
- `mission_offer_relevance` (`mission_id`, `offer_id` — PK composta,
  ambas `RESTRICT`): `classification` (`offer_relevance`: `match` /
  `possible_match` / `no_match`), `classified_at`, `created_at`. Índice em
  `offer_id`. Classificada uma única vez por par — os insumos (busca da
  missão, título bruto) são imutáveis depois que ambos existem, então uma
  linha nunca precisa ser reclassificada. Só é criada quando a IA devolve
  uma resposta válida: falha ou resposta fora do contrato não é
  persistida, para que a próxima coleta tente de novo (mesmo padrão de
  "ausência de classificação válida" pedido no item 7 do escopo original).
- Migration `20260809_0004`.

## Implementação real (o que mudou, não o que foi planejado)

1. **`app/collection/relevance.py` (novo)** — `OfferRelevance` (StrEnum) e
   duas funções assíncronas via `AIProviderManager`,
   `normalize_offer_title` e `classify_offer_relevance`; qualquer falha
   (rede, provedor, JSON fora do contrato, campo fora do vocabulário
   fechado) devolve `None` e loga um aviso sanitizado — nunca lança.
2. **`app/collection/orchestration.py`** — dentro da mesma transação curta
   de `_persist_success` (não em duas fases): `_resolve_offer_relevance`
   consulta o cache e só chama a IA na primeira vez que o par
   `(mission, offer)` aparece; `evaluate_price_alerts` só roda quando a
   classificação é `MATCH`. `_ensure_display_name` normaliza
   `Product.display_name` uma única vez, mesma lógica de cache. **Trade-off
   assumido conscientemente**: isso mantém uma transação aberta um pouco
   além do puramente síncrono para ofertas novas (documentado em
   docstring) — aceito porque a chamada de IA já é protegida por
   timeout/circuit breaker (bem mais curta e previsível que Playwright,
   motivo original do princípio "sem transação durante acesso externo"
   deste módulo) e só acontece na primeira vez por par, nunca em coletas
   repetidas da mesma oferta/missão. A alternativa (duas fases, IA fora da
   transação) adiaria o primeiro alerta possível de uma oferta nova para o
   ciclo seguinte e exigiria reestruturar o fluxo de claim/persist — mais
   complexo sem benefício claro para o volume atual da V1.
3. **Correção do bug do `previous` compartilhado** — `_persist_success`
   passou a filtrar a consulta de `previous` por
   `CollectionRun.mission_id == claim.mission_id` (join com
   `collection_runs`), em vez de pegar a última observação daquela
   `Offer` em qualquer missão. Mudança de uma consulta, sem schema novo.
4. **`app/telegram/notifications.py`** — `_resolve_offer_context` busca
   `Offer`/`Product`/`Store` a partir de `event.payload["offer_id"]`
   (já existia no payload, nunca era usado); `_render_alert` reescrito
   para o formato pedido (🔥/🏪/💰/🎯/🔎/🔗 para preço-alvo, 📉 análogo para
   queda), usando `product.display_name or product.name`, `store.name` e
   `offer.url` — nunca o nome da missão como nome do produto. Template
   continua string fixa no código, não gerada por IA.
5. **Infra** — `collection_worker` ganhou acesso ao secret
   `gemini_api_key_admin_dev` (+ `groq_api_key` opcional, mesma cascata da
   `api`) em `compose.yaml`/`docs/SECRETS.md`, perfil `ADMIN` fixo, nunca a
   chave/cota do perfil `USER`. Não recebe token do bot nem segredo do
   webhook.
6. **Achado adicional não implementado**: nenhum outro ajuste além do
   listado — nada além do escopo aprovado foi tocado.

## Validação real (2026-08-09)

- **Pipeline oficial**: aprovado — 753 testes rápidos (18 novos:
  `test_offer_relevance.py`, `test_mission_offer_relevance.py`, mais casos
  em `test_collection_orchestration.py`/`test_products.py`), 90,59% de
  cobertura, 14 integrações PostgreSQL reais (incluindo o teste dedicado
  de isolamento entre duas missões na mesma oferta), migration head
  `20260809_0004`.
- **E2E reproduzível**: 2/2 aprovados, com a fronteira de IA controlada
  (sem chamar Gemini/Groq reais), consistente com `docs/E2E_TESTS.md`.
- **Missão real via Telegram**: stack Docker reconstruído com o código
  atual, migration aplicada, `collection_worker` com o novo secret. Missão
  "mouse Logitech g pro 2" (alvo R$ 999.999,99, Kabum) processada pelo
  worker real. Achado relevante: sob carga real (~20 classificações numa
  única coleta), a maioria das chamadas de IA falhou
  (`unavailable` nas três camadas da cascata premium/Groq/gratuito) — o
  sistema reagiu exatamente como projetado, sem persistir classificação
  inválida e sem bloquear a coleta. As classificações que tiveram sucesso
  foram corretas, inclusive distinguindo dois modelos textualmente
  parecidos: "Logitech G PRO 2" (pedido) → `match`; "Logitech G Pro X
  Superlight 2" (modelo diferente, nome parecido) → `no_match`. Dois
  eventos `price.target_reached.v1` reais, 2 consumos `succeeded`, 0
  duplicados. **Confirmado visualmente pelo usuário**: as mensagens
  recebidas mostraram o nome real do anúncio, a loja Kabum, preço e link
  clicável — nunca o nome da missão como nome do produto.

## Formatação das mensagens principais do Telegram (2026-08-09, revisão adicional)

Depois da validação acima, o usuário pediu explicitamente que o item 5 do
escopo original ("revisar a apresentação das mensagens principais")
recebesse uma passada própria, além dos alertas de preço já corrigidos.

- **Auditoria completa** de toda mensagem enviada ao usuário
  (`router.py`, `confirmation.py`, `preferences.py`, `registration.py`,
  `privacy/notice.py`, `notifications.py`). O que já lia bem (prompts de
  cadastro, lista de ajuda do `/preferencias`, sufixo de confirmação
  sim/não, aviso de rate limit, `_UNKNOWN_REPLY`, `privacy_notice()`) foi
  mantido intacto — nenhuma mudança "só por mudar".
- **Achado objetivo, não estético**: `MissionStatus.value` (enum bruto em
  inglês/`snake_case`) vazava direto em duas frases em português — lista de
  missões (`"... — active"`) e resultado de comando
  (`'"..." agora está paused.'`). Um teste existente chegava a afirmar essa
  saída como esperada (`assert "paused" in ...`), confirmando que era um
  defeito real, não só uma opinião de estilo.
- **Novo módulo `app/telegram/formatting.py`** (templates fixos, nenhuma
  IA): `MISSION_STATUS_LABELS`/`MISSION_STATUS_ICONS` (rótulo/ícone em
  português por status), `format_money` (promovido de
  `telegram/notifications.py`, agora compartilhado) e `format_store_list`
  (nomes de loja capitalizados). Usado por `router.py`, `confirmation.py`
  e `notifications.py` para manter o mesmo formato em toda mensagem que
  envolve dinheiro, loja ou status de missão.
- **Hierarquia visual moderada** (quebras de linha, um emoji por mensagem
  quando fizer sentido, nunca por linha indiscriminadamente) aplicada à
  lista priorizada pelo usuário: cadastro concluído + link de senha, as
  quatro confirmações de ação de credencial, aviso de sessão
  expirando/expirada, confirmação e execução de criação de missão, lista
  de missões, resultado de comando, preferências e a mensagem de sessão
  não ativa.
- **Validação**: pipeline oficial (753 testes, 90,64% cobertura, 14
  integrações PostgreSQL reais), E2E reproduzível 2/2. Exemplos antes/depois
  apresentados e aprovados pelo usuário.

## Encerramento

Aprovado explicitamente pelo usuário em 2026-08-09: relevância
(`MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`, só `MATCH` alerta), correção do bug
do `previous` compartilhado entre missões, `products.display_name`, título
real do anúncio/loja/URL no alerta, dados factuais nunca vindos da IA,
migration, testes, E2E reproduzível, validação real no Telegram e a
formatação revisada das mensagens principais — tudo aprovado sem
pendências dentro do escopo desta TASK. **TASK-063 concluída.**

O achado sobre disponibilidade da cascata de IA (premium com 0% de sucesso
sob carga real) foi desmembrado para a TASK-064 — não é uma pendência desta
TASK, é uma nova TASK própria.

## Relação com a TASK-054/`v1.0.0`

Por instrução explícita do usuário: a tag `v1.0.0` **não foi alterada nem
recriada**, e `main`/`origin/main` não foram tocados nesta TASK. A
TASK-054 continua **suspensa como release final** — não mais por causa
desta TASK (concluída), mas até a TASK-064 (`docs/tasks/TASK-064.md`)
fechar. O tag existe e continua publicado tal como está.
