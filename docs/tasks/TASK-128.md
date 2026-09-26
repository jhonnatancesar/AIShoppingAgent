# TASK-128 — Produto nunca fica sem vínculo + "não entendi, descreva melhor" para pedido vago

Status: **Registrada (2026-09-26), implementação iniciando.**

## Problema real (confirmado no código)

- Quando a extração por IA não fecha marca + família + modelo (caso
  típico de produto genérico — "cadeira gamer", "mouse gamer"),
  `resolve_or_learn_product_variant` faz `if extraction is None: return
  None` e **não grava nada**: na coleta seguinte o mesmo título chama a
  IA de novo, a cada ciclo, para sempre. Com
  `product_identity_learning_enabled` nascendo `true` (2026-09-25), isso
  vira gasto recorrente de IA.
- O backfill da TASK-123 seleciona `identity_key IS NULL` — produtos
  genéricos nunca saem do backlog e cada `--apply` paga IA por eles de
  novo.
- `identity_key` tem índice ÚNICO (`uq_products_identity_key`) e é a
  chave da fusão de duplicatas (`apply_learned_identity`) — um vínculo
  genérico ali (ex.: `cpu:amd`) fundiria todos os processadores AMD não
  resolvidos num único produto, misturando preços e históricos. O
  vínculo genérico precisa morar em outro lugar.

## Decisões do usuário

- **2026-09-25**: "ela não pode não resolver, ela sempre vai ter que
  criar um vínculo de alguma coisa, seja vincular mais de uma
  informação, tipo modelo e família, tipo um processador que não
  resolveu ele vincula a CPU, AMD" — principalmente para produtos
  genéricos (cadeira gamer, mouse gamer), a não ser que o título
  especifique.
- **2026-09-26**:
  1. O vínculo parcial fica no banco; **DEV vê que é parcial**; usuário
     comum não vê ("isso só vai ser mais para gráficos e vincular
     cards").
  2. Título de loja que a IA não consegue nem categorizar: o GG
     registra que não entendeu, **abre o worker, lê a página do produto e
     passa mais detalhes para a IA**. "Não existe" a IA não resolver.
  3. Pedido vago digitado pelo **usuário** (criar missão/pesquisa): o GG
     responde que não entendeu e **pede mais descrição** — junto nesta
     TASK (hoje não existe esse fluxo; o único "Não entendi" atual é o
     dos menus numéricos do Telegram).

## Desenho técnico (proposto)

- **Níveis de vínculo** do produto: `exato` (como hoje, `identity_key`
  completo) / `parcial` (categoria + marca/família quando houver, SEM
  `identity_key` — nunca funde produtos, nunca habilita comparação entre
  lojas, gráfico comparável nem busca de preço histórico) / `aguardando
  página` (título não bastou; o worker vai ler a página).
- **Anti-invenção continua**: marca e família do vínculo parcial só
  entram se aparecerem no título/página.
- **Cache por título** para todos os níveis — o mesmo título nunca chama
  a IA de novo.
- **Backlog** passa a ser só "produto sem vínculo nenhum".
- **Salvaguarda técnica**: se nem com a página a IA categorizar, o
  resultado é registrado como terminal (nunca um laço de chamadas de IA).

## Etapas

1. Vínculo parcial + cache + backlog (backend).
2. Leitura da página pelo worker quando o título não basta.
3. "Não entendi, descreva melhor" para pedido vago do usuário
   (Telegram + web).
4. DEV vê o nível "parcial" (detalhe da oferta).

## Fora de escopo

Agrupar/exibir cards ou gráficos por vínculo parcial ("gráficos e
vincular cards") — o dado passa a existir no banco, o uso fica para uma
TASK futura.
