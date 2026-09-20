# TASK-123 — Backfill de identidade de produtos existentes via IA (placa-mãe é o caso concreto, mecanismo é genérico)

Status: **Registrada, não iniciada.** Revisão de escopo em 2026-09-20
(decisão do usuário, mesmo dia do registro original): a correção certa
não é um parser determinístico novo por categoria de produto -- é ligar
a máquina de aprendizado assistido por IA que **já existe e já foi
testada** nos checkpoints 3-11 (`v1.3.15`), mas nunca foi conectada a
nada que a acione de verdade contra o backlog de produtos já
coletados. Versão anterior desta TASK (parser `_motherboard` por
regex) descartada nesta revisão -- ver "Objetivo" abaixo para o porquê.

## Preflight (confirmado no código, não suposição)

- `backend/app/products/identity_learning.py:651-691`
  (`reprocess_unresolved_products`): já implementada, já testada
  (checkpoint 7, cobertura), faz exatamente o que foi pedido pelo
  usuário -- (1) busca `Product` com `identity_key IS NULL` (cobre
  placas-mãe e qualquer outra categoria sem extractor determinístico,
  não só placa-mãe); (2) chama a IA (`resolve_or_learn_product_variant`
  -> `identity_ai.py`) que extrai `manufacturer`/`family`/`model_name`/
  `variant`/`store_sku`/`manufacturer_part_number`/`attributes` por
  campo estruturado, com disciplina anti-alucinação (`_is_grounded`,
  cada valor precisa aparecer literalmente no título); (3) faz o
  vínculo no banco sem perder dado já pesquisado:
  `apply_learned_identity` migra TODAS as `Offer`s (e o histórico de
  `PriceObservation` que elas carregam) do `Product` ad-hoc pro
  canônico quando a identidade já existe, ou promove o próprio ad-hoc
  quando é a primeira vez -- nunca cria duplicata, nunca descarta
  pesquisa já feita.
- **Achado real, causa raiz do "por que não está funcionando":**
  `reprocess_unresolved_products` **tem zero chamadores em todo o
  repositório** (confirmado por busca completa em `backend/app/` e
  `backend/scripts/`) -- não existe script, comando administrativo nem
  job agendado que a invoque. A função existe, foi testada
  isoladamente, mas nunca foi ligada a nada que a execute contra o
  banco real.
- **O único ponto onde este mecanismo roda hoje** é
  `backend/app/collection/orchestration.py:2433-2443` -- dentro do
  processamento normal de uma oferta, só quando
  `pending.identity_unresolved` **e**
  `settings.product_identity_learning_enabled` (default `False`,
  confirmado ainda `False` em PROD). Mesmo se essa flag fosse ligada,
  ela só cobre ofertas que passam por uma coleta NOVA a partir dali --
  nunca teria efeito retroativo sobre o que já está parado no banco
  sem coleta futura agendada (missão pausada/cancelada, por exemplo).
- `identity_ai.py:62-80` confirma os campos exatos que a IA preenche
  por extração estruturada: `manufacturer`, `family`, `model_name`,
  `variant`, `store_sku`, `manufacturer_part_number`, `attributes`
  (dict) -- exatamente "família, modelo, marca etc." por campo.
- Mesmo padrão de script de reparo determinístico já existe no repo
  para se espelhar (`backend/scripts/repair_cpu_identity_misclassification.py`,
  `--dry-run`/`--apply`).

## Objetivo

**Não escrever um parser determinístico novo por categoria de produto**
(alternativa considerada nesta TASK antes da revisão de escopo --
descartada: caro de manter categoria por categoria -- placa-mãe, RAM,
fonte, gabinete teriam cada uma seu próprio extractor regex --, e a IA
já resolve isso de forma genérica, sem esse custo de manutenção). Em
vez disso:

1. **Script de backfill** (`backend/scripts/reprocess_unresolved_product_identity.py`,
   mesmo padrão `--dry-run`/`--apply`/paginação por `limit` de
   `scripts/repair_cpu_identity_misclassification.py`) que chama
   `reprocess_unresolved_products` em lotes contra o banco real, até
   esgotar o backlog. Roda com um `AIProviderManager` explícito
   (`build_admin_dev_ai_provider_manager`, mesmo padrão de
   custo/perfil já usado por outros scripts de reparo/validação) --
   **não depende de ligar `product_identity_learning_enabled` em
   produção**, já que `reprocess_unresolved_products` recebe o
   `ai_manager` como parâmetro explícito, nunca lê a flag internamente.
   Isso resolve o backlog atual (placas-mãe incluídas, e qualquer outra
   categoria sem extractor) sem precisar decidir sobre o caminho de
   coleta ao vivo.
2. **Decisão separada, não bloqueia o item 1:** se/quando ligar
   `product_identity_learning_enabled` para o caminho de coleta ao
   vivo (cobre produtos novos dali em diante, sem precisar de backfill
   manual repetido) -- decisão de produto/custo do usuário, registrada
   aqui só como opção complementar, não como parte obrigatória desta
   TASK.

## Escopo

Script de backfill batch usando `reprocess_unresolved_products` já
existente e já testada. Nenhuma mudança em
`identity_learning.py`/`identity_ai.py` (o mecanismo já está correto e
testado) -- só o "fio" que falta ligando ele ao banco real.

## Fora de escopo

Ativar `product_identity_learning_enabled` para o caminho de coleta ao
vivo (decisão separada do usuário, item 2 do Objetivo). Qualquer
mudança na extração/grounding da IA (`identity_ai.py`) ou no algoritmo
de match/vínculo (`identity_learning.py`) -- já funcionam, não é o
achado aqui. Parser determinístico por regex (`_motherboard` ou
similar, por categoria de produto) -- considerado e descartado nesta
revisão de escopo.

## Critério de validação futuro

Rodar o script `--dry-run` contra uma cópia/amostra do banco real,
confirmar que as placas-mãe conhecidas (achado original desta TASK)
aparecem na lista de candidatas e que a extração da IA preenche
`family`/`manufacturer`/`model_name` plausíveis para cada uma, sem
inventar valor ausente do título (grounding). Rodar `--apply` numa
cópia descartável do banco primeiro (nunca direto em PROD na primeira
vez), confirmar por SQL que nenhuma `Offer`/`PriceObservation`
existente foi perdida ou duplicada -- só `Product.category`/
`identity_key` mudaram de `NULL` para um valor resolvido, e o
`Product` ad-hoc antigo (quando já existia um canônico) foi removido
sem deixar `Offer` órfã. Só depois disso, autorização explícita do
usuário para rodar `--apply` contra PROD de verdade.
