# TASK-106 — Pesquisa de cupons

Status: **Em desenvolvimento, fora deste repositório (`DEC-105`,
2026-08-30, retifica `DEC-093`).** Código real existe e já foi validado
ao vivo (Kabum, Edge/CDP dedicado — cupons reais encontrados e
persistidos), mas vive num repositório totalmente separado
(`https://github.com/jhonnatancesar/AIShoppingAgent-cupom.git`), nunca
como `app/coupons/` aqui — decisão explícita do usuário, para poder
desacoplar/mover para outra máquina sem tocar neste projeto. Nenhum
código deste repositório foi alterado. Ver `DEC-105` para o detalhe
completo da retificação arquitetural.

## Objetivo

Apresentar cupons reais (código e/ou desconto promocional) para ofertas
relevantes das missões, como subsistema próprio — nunca acoplado à busca
normal de oferta nem ao `CollectionOrchestrator`/`collection_worker`. Nunca
inventar ou presumir cupom sem evidência.

## Decisão de escopo — evidência (preflight, 2026-08-22)

- **Fonte principal:** site oficial de cada loja já integrada — mesma
  arquitetura comum de Store Providers, mas reaproveitada só como
  infraestrutura (transporte/parsing), nunca como o mesmo job/orquestração
  da coleta de preço (`DEC-093`). Nenhuma dependência de Firecrawl nem de
  agregador de terceiro nesta versão.
- **Prioridade de evidência** (a mais forte vence; ausência de qualquer uma
  delas não gera cupom):
  1. cupom explícito visível no card ou na página do produto/oferta
     (banner, selo, texto "use o cupom X");
  2. regra promocional oficial publicada pela própria loja (página de
     cupons/promoções/home, sem ser produto específico);
  3. sem evidência em nenhuma das duas formas acima → nenhum cupom é
     persistido nem apresentado. Nunca há fallback para "provável" ou
     "geralmente".
- **Papel da IA:** pode interpretar/estruturar texto já encontrado na
  evidência (ex.: extrair código e percentual de um banner com texto
  livre). **Nunca** inventa, completa ou presume código/desconto ausente
  da evidência (`DEC-088`).
- **Firecrawl:** não é dependência principal agora; cliente ocioso já
  existe (`app/search/firecrawl.py`), não descartado, fora desta versão.
- **Agregadores de cupons de terceiro** (ex.: Cuponomia, Pelando): adiados
  para V2/fallback futuro.

## Resultado da auditoria real (2026-08-22, Edge/CDP, mesmo transporte já validado)

| Loja | Cupom real encontrado? | Onde | Evidência literal |
|---|---|---|---|
| Amazon | Sim | Card de busca | `"Cupom de R$ 20,00 de desconto aplicado"` / `"Cupom de 5% de desconto aplicado"` — sem código alfanumérico, aplicado automaticamente (clip coupon) |
| Kabum | Sim | Card de busca, mesmo seletor que o `KabumProvider` já lê (`a[href*="/produto/"]`) | `"SELO: CUPOM GAMER10"` — código real (`GAMER10`), precisa ser aplicado pelo comprador |
| Magalu | Sim | **Home**, não na busca — a busca variou entre 0 e 6 itens com cupom em execuções diferentes (parece campanha rotativa/instável, não um campo fixo) | `"Cupom R$ 100 OFF"` nos cards de "Ofertas Relâmpago" da home |
| Mercado Livre | Sim | Home | `"15% OFF com Cupom"` em card da home |

Pichau/Terabyte ficaram fora da auditoria (relato prévio: cupom raro ou
inexistente) — podem ser revisitadas depois, sem bloquear o resto.

**Por que isso levou à reorientação (`DEC-093`):** Amazon e Kabum expõem
cupom no mesmo card que a coleta de oferta já abre; Magalu e Mercado Livre
só expõem na home, fora do fluxo de busca por produto. Um Coupon Collector
próprio, com sua própria varredura por loja, resolve isso sem forçar a
coleta normal de oferta a abrir navegação extra.

## Arquitetura proposta (`DEC-093`)

### 1. Coupon Collector — processo independente

- Módulo próprio (proposta: `app/coupons/`), processo/loop separado do
  `collection_worker` — mesmo padrão de processo dedicado já usado pelo
  `telegram_notifier` (`app/telegram/worker.py`, loop e `poll_seconds`
  próprios, settings próprios).
- Varre **fontes oficiais por loja**, não por missão/produto/oferta —
  nunca dispara por causa de uma missão específica. Loja com cupom vindo
  do card de busca (Amazon, Kabum) varre um termo representativo ou
  agregado; loja com cupom só na home (Magalu, Mercado Livre) varre a
  home diretamente.
- Reaproveita transporte/provider de cada loja **só como infraestrutura**
  (`BrowserSession`, `CdpPageFallback`/Edge-CDP onde já validado,
  parser/HTML utilities) — nunca chama `PlaywrightStoreProvider.collect()`
  nem passa pelo `CollectionOrchestrator`. Falha, bloqueio ou circuito
  aberto do Coupon Collector fica isolado nele mesmo; não deve existir
  nenhum caminho de código onde uma falha aqui derruba ou atrasa a coleta
  de preço.
- **Proposta de frequência:** poucas execuções por dia (ex.: a cada
  2–6 horas, configurável via settings próprios, não os do
  `collection_worker`), não minutos. Campanha de cupom muda bem mais
  devagar que preço, e a lição de hoje (bloqueio da Shopee, verificação de
  conta do Mercado Livre) reforça não bater fontes com frequência alta.
  Número exato fica para quando a TASK for aprovada para implementação.

### 2. Persistência — modelo `Coupon` próprio

Não é campo de `Offer`. Proposta de modelo (`app/coupons/models.py`),
todos os campos abaixo só preenchidos com evidência real — ausência
permanece `NULL`, nunca inventada:

| Campo | Observação |
|---|---|
| `id` | UUID, como os demais modelos |
| `store_id` | FK `stores.id` — cupom sempre pertence a uma loja |
| `code` | `NULL` quando o desconto é automático (Amazon), preenchido quando há código real (Kabum `GAMER10`) |
| `discount_kind` | `fixed_amount` \| `percentage` — só o que a evidência declarar |
| `discount_value` | valor numérico correspondente |
| `minimum_purchase_amount` | valor mínimo de compra, quando declarado |
| `maximum_discount_amount` | teto do desconto, quando declarado |
| `scope_kind` | `store_wide` \| `category` \| `product` \| `seller` — só quando a evidência permitir classificar; sem evidência suficiente fica `unknown`, nunca presumido `store_wide` por padrão |
| `scope_reference` | referência textual/FK conforme `scope_kind` (ex.: `offer_id` quando o cupom é específico de um card) |
| `valid_until` | `NULL` quando a origem não declarar validade — nunca inferida |
| `raw_rule_text` | texto literal da evidência (regra/condição), nunca parafraseado como se fosse texto oficial |
| `source_url` | URL de onde a evidência foi capturada |
| `evidence` | snapshot bruto (mesmo padrão `evidence`/`card_text` já usado em `RawCollectedOffer`) |
| `last_seen_at` | última vez que o Coupon Collector confirmou a evidência |
| `status` | `active` \| `expired` \| `unknown` — `unknown` quando a origem parar de confirmar sem declarar expiração explícita |

Migration só depois de aprovação — nenhuma tabela criada ainda.

### 3. Aplicabilidade — processo separado, determinístico

- Processo próprio (pode ser uma fase dentro do mesmo worker do Coupon
  Collector, ou um terceiro processo — decisão em aberto, ver "Pontos em
  aberto" abaixo) cruza cupons `active` com ofertas relevantes das
  missões via `MissionOfferRelevance` (já existente, TASK-063/TASK-094) —
  nunca com todas as ofertas do banco, só as que já têm relevância
  `MATCH`/`POSSIBLE_MATCH` para alguma missão do usuário.
- Regra determinística, sem IA decidindo aplicabilidade: cruza
  `scope_kind`/`scope_reference` do cupom com `store_id`/`seller_id`/
  categoria da oferta. IA só pode ter sido usada antes, para estruturar o
  texto já capturado pelo Coupon Collector — nunca para decidir se o
  cupom "parece" aplicável.
- Resultado tri-state: **aplica** (evidência suficiente: escopo bate
  exatamente), **possivelmente aplicável** (evidência insuficiente pra
  certeza — ex.: cupom `store_wide` com regra de categoria não
  totalmente clara), **não aplica** (não entra em nenhuma notificação).
  Nunca afirma aplicação com regra ambígua.
- Preço estimado = preço atual da oferta (`PriceObservation` mais
  recente) menos o desconto do cupom, respeitando `minimum_purchase_amount`/
  `maximum_discount_amount` quando existirem. Sempre rotulado como
  estimativa.

### 4. Notificação

- Só dispara quando o cupom muda a oferta para uma oportunidade relevante
  (não para todo cupom ativo de toda loja).
- Conteúdo obrigatório: preço atual, desconto, preço estimado, código
  (quando existir), regra relevante (texto literal, resumido, nunca
  reescrito como afirmação mais forte que a evidência), link da oferta,
  evidência/origem do cupom.
- Deixa explícito que o preço é estimado e depende da aplicação real do
  cupom no checkout — nunca apresenta como preço final confirmado.
- Reaproveita o canal de notificação já existente (Telegram/Web) e a
  mesma disciplina de idempotência/checkpoint já usada para entrega de
  ofertas (TASK-084) — decisão de reaproveitar exatamente qual mecanismo
  fica para a implementação, não deste documento.

### 5. Separação — não-negociável

- Coupon Collector **não** roda durante a coleta de produto/oferta;
- coleta de oferta **não** abre home nem página de cupom por `Offer`;
- Coupon Collector **não** é registrado no `CollectionOrchestrator` nem no
  `collection_worker`;
- falha do Coupon Collector nunca derruba, atrasa ou marca como falha a
  coleta de preço — circuito e retry são próprios e isolados.

## Pontos em aberto (não implementar sem decidir)

- Aplicabilidade como fase do mesmo worker do Coupon Collector, ou
  processo terceiro separado;
- mecanismo exato de idempotência de notificação (reaproveitar
  infraestrutura de evento/checkpoint existente vs. tabela própria de
  "cupom já notificado para esta oferta/missão");
- frequência exata do Coupon Collector (proposta: 2–6h, número final em
  aberto);
- se Magalu/Mercado Livre entram já na primeira versão (home separada) ou
  só Amazon/Kabum primeiro (reaproveitam o card já conhecido, menor
  esforço) — proposta: Amazon/Kabum primeiro, Magalu/ML como segunda
  fase, mas é decisão do usuário.

## Validação mínima futura

- pelo menos um cupom real persistido com evidência completa (código
  quando existir, fonte, texto da regra);
- ausência de evidência não persiste nem apresenta cupom (nunca
  "provável" nem `store_wide` presumido);
- aplicabilidade nunca afirma aplicação com regra ambígua — usa
  "possivelmente aplicável";
- falha isolada do Coupon Collector não afeta `collection_worker`/coleta
  de preço (teste explícito de isolamento);
- notificação só dispara para oportunidade real, sempre com preço
  estimado rotulado como estimativa.

## Fora de escopo

Firecrawl como fonte principal, agregadores de cupons de terceiro,
aplicação automática de cupom em carrinho/compra, cupons exclusivos de
conta/login, histórico de cupons, AliExpress e demais lojas fora da V1.2,
qualquer técnica de evasão anti-bot, implementação de código nesta rodada.
