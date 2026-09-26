# TASK-128 — Produto nunca fica sem vínculo + "não entendi, descreva melhor" para pedido vago

Status: **Etapa 1 pronta e validada (2026-09-26) — aguardando
autorização de commit.** Integração 370/370 (migration upgrade →
downgrade → upgrade → `alembic check` ok), unitária 2726 passando / 0
falhas, cobertura 90,40%, `ruff` limpo. Etapas 2, 3 e 4 não iniciadas.

Achado da suíte de integração (corrigido): o vínculo parcial saía com a
marca/família do jeito que a IA escreveu ("HyperX") na primeira
resolução e em slug ("hyperx") quando lido do cache `pending_review`.
Agora categoria/marca/família do vínculo parcial usam o MESMO slug da
identidade exata (`_slug`: "cadeira-gamer", "hyperx", "tuf-gaming") —
o mesmo título dá sempre o mesmo vínculo, e um parcial "cpu/amd" casa
com os produtos de identidade exata da mesma marca.

### Checkpoint forçado (2026-09-26, limite de cota da sessão)

> ✅ **RESOLVIDO na retomada de 2026-09-26** (texto original mantido
> como histórico abaixo) — a Fase C já despacha o vínculo parcial.
>
> **Atenção ao retomar:** o working tree está no meio da etapa 1. A
> coleta ao vivo ainda não sabe tratar o vínculo parcial e quebra se
> rodar agora. O primeiro passo é corrigir isso na Fase C da
> orquestração, antes de rodar testes ou coleta. Tudo que estava verde
> (126, 127, cotas, flag) já está commitado local, nos commits
> `a1cbb9b` → `38fcc0a`.

Feito (só no working tree, nada commitado; último commit `38fcc0a`):
- Migration `20260926_0001_identity_candidate_partial_link` (status
  `partial`/`awaiting_page`, colunas de identidade nullable só nesses
  estados, CHECKs condicionais) + modelo `identity_candidates.py`.
- `identity_ai.py`: `AIPartialExtraction`, `AIUnrecognizedTitle`,
  `PartialProductLink`, `build_partial_link`; item único e lote devolvem
  parcial/não-entendido em vez de `None` (`None` = só falha passageira).
- `identity_learning.py`: `_link_from_candidate`, `_record_uncertain_
  extraction`, `_resolve_from_extraction`, `apply_partial_link`,
  `_apply_resolution` (unifica os 3 pontos do backfill); backlog =
  `identity_key IS NULL AND category IS NULL`.

**Retomada (2026-09-26) — itens da etapa 1:**
1. ✅ `collection/orchestration.py` Fase C: despacha por tipo —
   `PartialProductLink` → `apply_partial_link` (só campos vazios, nunca
   `identity_key`), identidade exata → `apply_learned_identity` como
   antes; anotações de `_AIOutcome`/`_classify` atualizadas. A coleta ao
   vivo não quebra mais com o vínculo parcial.
2. ✅ Critério do backlog num ponto só:
   `identity_learning.unlinked_product_criteria()` (`identity_key IS
   NULL AND category IS NULL`), usado por `reprocess_unresolved_products`
   e pelo script de backfill (`--count-only` e lista de candidatos) —
   contagem e processamento nunca mais divergem.
3. ✅ Os 2 testes que mudaram de comportamento em
   `tests/test_product_identity_ai.py` agora esperam parcial /
   "não entendi".
4. Achado e corrigido na retomada: no backfill item a item
   (`batch_size=1`), o `awaiting_page` não era commitado e o `rollback`
   antes da IA do produto seguinte apagava o cache (o título pagaria IA
   de novo toda rodada); agora commita como o caminho em lote.
5. Testes novos: `tests/test_product_identity_learning_partial.py`
   (unitário, 17), `tests/integration/test_product_identity_partial_link.py`
   (integração com a migration), teste da Fase C (unitário) e pelo
   orquestrador real (integração), `--count-only` do script (integração).
   Suítes completas verdes (ver Status no topo); falta só o commit (com
   autorização).

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
     TASK, **nos dois canais: site do GG e Telegram** (confirmado pelo
     usuário em 2026-09-26). Hoje não existe esse fluxo; o único "Não
     entendi" atual é o dos menus numéricos do Telegram.
  4. Se a IA falhar por **cota esgotada** (nenhum fallback da cascata
     resolveu), o GG avisa o usuário para **tentar abrir a missão mais
     tarde**, porque a cota de IA foi excedida ("se falhar por cota o gg
     deve retornar mensagem ao usuário para tentar abrir uma missão mais
     tarde... se nem um fallback resolver") — **nos dois canais, site
     do GG e Telegram** ("o gg e o telegram").

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
   (Telegram + web) + aviso "cota de IA excedida, tente abrir a missão
   mais tarde" quando nenhum fallback da cascata responder.
4. DEV vê o nível "parcial" (detalhe da oferta).

## Fora de escopo

Agrupar/exibir cards ou gráficos por vínculo parcial ("gráficos e
vincular cards") — o dado passa a existir no banco, o uso fica para uma
TASK futura.
