# TASK-094 — Pré-lista com múltiplas ofertas relevantes por loja

Status: **Implementada e validada com testes focados em 2026-08-22;
aguardando revisão do usuário.**

## Origem

Caso real em DEV: uma missão específica do Samsung Galaxy S24 Ultra só na
Amazon apresentou uma oferta usada de parceiro porque a coleta específica
colapsava a loja no menor preço absoluto. A auditoria confirmou uma segunda
redução: a pré-lista mantinha uma observação por loja e depois apenas o top 2
global por `amount`.

Esta TASK é o item 4 da V1.2 (`DEC-076`) e antecede a página rica de
produto/oferta, que passa a ser a próxima atividade. A ausência do arquivo
formal `docs/tasks/TASK-093.md` foi registrada, sem correção retroativa nesta
rodada por pedido explícito do usuário.

## Objetivo e regras

- Selecionar no máximo 5 ofertas por loja, nunca por preço isolado.
- Reutilizar a classificação persistida: `MATCH` antes de `POSSIBLE_MATCH`;
  `NO_MATCH` fora; pendência continua bloqueando a primeira pré-lista.
- Ranking determinístico: relevância, condição (`new` > `refurbished` >
  `used` > `unknown`), `seller_kind` (`platform` > `marketplace_partner` >
  `unknown`/não avaliado), disponibilidade, `amount`, `total_amount` e UUID da
  oferta como desempate estável.
- Sem interpretação nova por IA e sem interpretar pedido textual por usado.
- Telegram agrupa normalmente uma mensagem por loja, dividindo somente se o
  texto se aproximar do limite técnico.

## Desenho implementado

### Pool intermediário

`_select_final_candidates` deixou de ter seleção especial da Amazon e aplica
um limite comum de **8 candidatos por loja**. Oito é o menor teto escolhido
para manter três posições de diversidade acima do top 5 final sem voltar aos
até 20 cards/classificações de IA do provider. A pré-seleção já usa condição,
evidência de vendedor disponível no card, disponibilidade, preço/total e
identificador; a relevância continua sendo classificada no ponto existente.

### Condição histórica

`OfferCondition` contém `new`, `refurbished`, `used` e `unknown` e percorre
`RawCollectedOffer` → `NormalizedCollectedOffer` → `PriceObservation`.
Por regra confirmada na validação real, a oferta principal da Amazon é `new`
quando não há marcador de usado/seminovo/recondicionado; demais providers
continuam `unknown` sem evidência. A Amazon
extrai condição quando uma linha isolada do card declara o valor (`Renewed`,
`Seminovo - Excelente` etc.) ou quando o título termina com marcador explícito
entre parênteses, como `(Seminovo)`; `Renewed` é `refurbished` e os marcadores
explícitos de seminovo são `used`;
frases genéricas sobre opções novas e usadas não classificam o item. O card
também pode preencher o `seller_kind` existente quando
declara explicitamente o vendedor. O enriquecimento de página individual
permanece limitado como antes e reaproveita a mesma página já aberta para ler o
campo explícito `Condição` abaixo do vendedor; nenhuma navegação nova foi
adicionada.

A migration `20260822_0002` adiciona `price_observations.condition` não nula,
com default/backfill conservador `unknown` e CHECK fechado. A equivalência
comercial da TASK-093 inclui o campo: qualquer mudança de condição grava nova
observação mesmo com o mesmo preço.

### Seleção final, eventos e Telegram

`rank_prelist_candidates` aplica uma regra comum a todas as lojas, limita cinco
por loja e trabalha sobre a observação mais recente de cada oferta confirmada
na coleta atual da loja. A primeira pré-lista publica
`mission.prelist_ready.v2`, cujo payload contém uma coleção ordenada de
snapshots reais (`offer_id`, `observation_id`, `store_id`, valores,
relevância, condição, `seller_kind` e disponibilidade).

`mission.prelist_errata.v2` referencia o evento original e carrega a seleção
atualizada de uma loja. A errata só ocorre quando o melhor candidato atual
melhora segundo a mesma chave comercial completa; uma oferta usada apenas mais
barata não supera uma nova. Eventos V1 permanecem no catálogo e no renderer,
sem reescrita.

No Telegram, os itens de cada loja viram um único texto sem imagem, com
condição, responsável quando conhecido, disponibilidade, preço/parcelamento e
link. O texto só é repartido acima do limite seguro de 4.000 caracteres. No
cenário normal das quatro lojas, o máximo é quatro mensagens.

## Testes focados

Executados com o Python 3.14.6 oficial da máquina e `pytest --no-cov`, pois a
seleção deliberadamente pequena não satisfaz isoladamente o `fail-under=90`
global:

- Amazon específica preserva múltiplas ofertas;
- pool intermediário e prioridades comerciais;
- limite de cinco por loja, `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`;
- condição e `seller_kind` do card Amazon;
- mudança de condição quebra a deduplicação da TASK-093;
- contrato e serialização aninhada do evento V2;
- renderer V2 agrupado e renderer V1 preservado;
- schema de `PriceObservation`.

Resultado da implementação: **16 passed**, com um aviso de depreciação externo
do Google GenAI. Na validação final, outros **7 testes diretamente afetados**
passaram após reconhecer `(Seminovo)`, `Seminovo - Excelente`, `Renewed` e o
campo explícito `Condição`, sem acrescentar navegação.
As expectativas da suíte de integração foram atualizadas para o payload V2,
mas essa suíte PostgreSQL não foi executada nesta rodada.
Pipeline completo, Docker, commit, push e deploy não foram executados.

Uma busca Amazon real, headless e limitada a três cards por `Samsung Galaxy
S24 Ultra` confirmou três sobreviventes: pela regra final aprovada, a oferta
sem marcador de usado é `new` e duas com `(Seminovo)` ficaram `used`; vendedor ausente no
card permaneceu `unknown`. A migration foi aplicada numa instância PostgreSQL
17 descartável e isolada: head `20260822_0002`, histórico anterior convertido
para `unknown` e inserts aceitos para os quatro valores válidos.

## Fora de escopo

Página rica, reviews, gráficos, comparação entre lojas, cupons, Magalu, painel
administrativo, interpretação textual de preferência de condição, nova IA e
qualquer mudança em produção.

## Riscos reais restantes

- Vendedor continua `unknown` quando o card não traz evidência explícita.
  Condição sem evidência continua `unknown` fora da exceção Amazon aprovada.
- O campo `Condição` da página/card individual foi validado por DOM focado e
  evidência visual fornecida pelo usuário; a busca real não abriu páginas de
  detalhe, respeitando o limite operacional pedido.
