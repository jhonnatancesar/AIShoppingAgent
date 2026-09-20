# TASK-123 — Identidade de produtos via IA: backfill do backlog + cobertura de itens novos

Status: **Item 1 implementado e testado (2026-09-20); item 2 não
iniciado.** Duas revisões de escopo no mesmo dia do registro original:
(1) a correção certa não é um parser determinístico novo por categoria
de produto -- é ligar a máquina de aprendizado assistido por IA que
**já existe e já foi testada** nos checkpoints 3-11 (`v1.3.15`), mas
nunca foi conectada a nada que a acione de verdade; (2) o usuário
confirmou explicitamente que quer os **dois lados** cobertos -- o
backlog já coletado (placas-mãe achadas originalmente) **e** os itens
novos que continuam chegando sem identidade a cada coleta, não só um ou
outro. Versão anterior desta TASK (parser `_motherboard` por regex)
descartada -- ver "Objetivo" abaixo para o porquê.

**Item 1 (backfill) concluído nesta sessão:**
`backend/scripts/reprocess_unresolved_product_identity.py` (commit
`bf34f19`, local, não pushado) + `tests/integration/
test_reprocess_unresolved_product_identity.py` (3 testes, Postgres
real, provando dry-run/apply/idempotência e a garantia central de não
perder `Offer`/`PriceObservation` já coletada). Rodado junto com os
testes de identidade já existentes (17 passed, sem interferência).
`ruff check`/`format` limpos em todo o repositório. **Ainda não rodado
contra uma amostra/cópia do banco real de PROD** (só Postgres
descartável de teste) -- esse passo, e a decisão de rodar `--apply`
contra PROD de verdade, seguem pendentes de autorização explícita do
usuário (ver "Critério de validação futuro").

**Item 2 (flag ao vivo) não iniciado** -- depende do item 1 estar
validado contra dado real primeiro, conforme sequência já registrada
abaixo.

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

Os dois itens abaixo **fazem parte do escopo obrigatório desta TASK**
(confirmado pelo usuário -- "eu preciso vincular os itens novos
também", não só o backlog). Ordem sugerida, por controle de risco/custo
(decisão de sequência cabe a quem for implementar, mas a razão fica
registrada aqui): validar o item 1 primeiro (execução em lote,
controlada, `--dry-run` antes de qualquer `--apply`, teto de `limit`
por rodada) antes de ligar o item 2 (que passa a chamar IA
automaticamente, sem teto, a cada oferta não resolvida de cada coleta,
dali em diante -- exposição de custo bem maior e menos controlada que
uma rodada de backfill em lote).

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
   Resolve o backlog atual (placas-mãe incluídas, e qualquer outra
   categoria sem extractor) de uma vez.
2. **Ligar `product_identity_learning_enabled` para o caminho de
   coleta ao vivo** (`orchestration.py:2433-2443`) -- cobre produtos
   NOVOS dali em diante, sem precisar de backfill manual repetido a
   cada rodada. Sem isso, mesmo depois do backfill do item 1 zerar o
   backlog uma vez, qualquer produto novo sem extractor determinístico
   (placa-mãe ou outra categoria) volta a acumular sem identidade a
   partir da primeira coleta depois do backfill -- o problema original
   reaparece aos poucos. Ativar em produção é ação de configuração/deploy
   (variável de ambiente, não mudança de código) -- cabe a esta sessão
   executar quando o item 1 já estiver validado, não a "o dev".

## Escopo

Script de backfill batch usando `reprocess_unresolved_products` já
existente e já testada (item 1) **e** ativação de
`product_identity_learning_enabled` no caminho de coleta ao vivo (item
2) -- os dois juntos, não um dos dois isolado (confirmado pelo
usuário). Nenhuma mudança em `identity_learning.py`/`identity_ai.py`
(o mecanismo já está correto e testado) -- só o "fio" que falta ligando
ele ao banco real (backfill) e ao caminho de produção (flag).

## Fora de escopo

Qualquer mudança na extração/grounding da IA (`identity_ai.py`) ou no
algoritmo de match/vínculo (`identity_learning.py`) -- já funcionam,
não é o achado aqui. Parser determinístico por regex (`_motherboard`
ou similar, por categoria de produto) -- considerado e descartado
nesta revisão de escopo.

## Critério de validação futuro

**Item 1 (backfill):** rodar o script `--dry-run` contra uma
cópia/amostra do banco real, confirmar que as placas-mãe conhecidas
(achado original desta TASK) aparecem na lista de candidatas e que a
extração da IA preenche `family`/`manufacturer`/`model_name`
plausíveis para cada uma, sem inventar valor ausente do título
(grounding). Rodar `--apply` numa cópia descartável do banco primeiro
(nunca direto em PROD na primeira vez), confirmar por SQL que nenhuma
`Offer`/`PriceObservation` existente foi perdida ou duplicada -- só
`Product.category`/`identity_key` mudaram de `NULL` para um valor
resolvido, e o `Product` ad-hoc antigo (quando já existia um canônico)
foi removido sem deixar `Offer` órfã. Só depois disso, autorização
explícita do usuário para rodar `--apply` contra PROD de verdade.

**Item 2 (flag ao vivo):** validado o item 1, ligar
`product_identity_learning_enabled` primeiro em DEV/ambiente de
validação, rodar uma missão real ponta a ponta com um produto sem
extractor determinístico (ex.: uma placa-mãe nova, nunca vista) e
confirmar que a oferta resolve identidade automaticamente na própria
coleta, sem precisar do script de backfill depois. Só então, com os
dois validados, autorização explícita do usuário para ligar a flag em
PROD -- ação de deploy/configuração que cabe a esta sessão executar,
não ao dev que só mexe em código.
