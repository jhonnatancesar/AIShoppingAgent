# TASK-123 — Identidade de produtos via IA: backfill do backlog + cobertura de itens novos

Status: **Concluída nesta sessão de desenvolvimento (2026-09-20).**
Item 1 (backfill) implementado, testado e commitado local. Item 2
(ligar a flag em PROD) fica **explicitamente adiado para a hora do
deploy** -- decisão do usuário -- e deve ser executado junto com o
próprio backfill do backlog real (`--apply` contra PROD), não antes:
ver a seção "Ação no deploy" no fim deste documento. Duas revisões de
escopo no mesmo dia do registro original:
(1) a correção certa não é um parser determinístico novo por categoria
de produto -- é ligar a máquina de aprendizado assistido por IA que
**já existe e já foi testada** nos checkpoints 3-11 (`v1.3.15`), mas
nunca foi conectada a nada que a acione de verdade; (2) o usuário
confirmou explicitamente que quer os **dois lados** cobertos -- o
backlog já coletado (placas-mãe achadas originalmente) **e** os itens
novos que continuam chegando sem identidade a cada coleta, não só um ou
outro. Versão anterior desta TASK (parser `_motherboard` por regex)
descartada -- ver "Objetivo" abaixo para o porquê.

**Item 1 (backfill) concluído nesta sessão** (script publicado
originalmente em `v1.3.18`, ver changelog) + **correção adicional de
custo/robustez 2026-09-20 (`v1.3.19`) + ajuste de escala no mesmo dia
(`v1.3.20`)** -- achados reais ao responder perguntas diretas do
usuário sobre gasto de quota de IA e, depois, sobre o tamanho real do
backlog:

- **`--count-only`**: conta o backlog REAL (sem `limit`, zero chamada
  de IA) antes de qualquer decisão de ritmo.
- **Lote de IA** (`_BATCH_SIZE=10`): `reprocess_unresolved_products`
  ganhou `batch_size`/`extract_product_identities_via_ai_batch`
  (`identity_ai.py`) -- agrupa só os produtos que realmente precisam
  de extração (depois do motor determinístico/cache/reuso sem IA) em
  lotes de títulos por chamada, ~10x menos chamadas que 1-por-produto.
  `--limit`/`--dry-run`/`--apply` têm teto rígido de 100
  (`_MAX_LIMIT`, 10 lotes de 10). **Números ajustados no mesmo dia**:
  a escolha original (lote de 4, teto de 20) supunha um backlog
  pequeno -- o usuário revelou que o backlog real tem **371 Products**,
  o que exigiria ~19 execuções manuais de `--apply` com os números
  originais (inviável de acompanhar de perto); com 10/100, 371 cabe em
  ~4 rodadas.
- **Bug real encontrado e corrigido**: `session.rollback()` (proteção
  contra `idle_in_transaction_session_timeout` antes de cada chamada
  de IA) expira TODAS as instâncias já carregadas da sessão, não só a
  atual -- com 2+ Products precisando de extração de verdade na MESMA
  rodada, isso ou quebrava (lazy-load síncrono fora do greenlet async)
  ou descartava silenciosamente a identidade já aplicada de um Product
  anterior ainda não commitado. Bug PREEXISTENTE (não introduzido pelo
  lote), nunca pego porque nenhum teste anterior cobria 2+ Products
  precisando de IA na mesma chamada. Corrigido: título/id capturados
  como valores simples antes de qualquer rollback, e cada resolução é
  commitada imediatamente quando `--apply` (nunca em `--dry-run`, onde
  perder trabalho intermediário é inofensivo -- nada deveria sobreviver
  mesmo). Regressão coberta por teste de integração real com 2 Products
  distintos (`tests/integration/test_product_identity_learning.py`) e
  por um teste de 13 Products/2 lotes
  (`tests/integration/test_reprocess_unresolved_product_identity.py`).

`tests/integration/test_reprocess_unresolved_product_identity.py` (5
testes) + `test_product_identity_learning.py` (14 testes, 1 novo) --
todos passando contra Postgres real, `ruff check`/`format` limpos.

**`--dry-run --limit 100` rodado contra o banco REAL de PROD nesta
mesma rodada (2026-09-20, `v1.3.20`) -- achou 2 problemas reais,
corrigidos em `v1.3.21`, nenhum deles causado por dado real ruim:**

1. **9 de ~10 lotes falharam na extração**
   (`product_identity_ai_batch_extraction_failed`, log sem causa
   visível -- `exc_info=False` sem mais nada). Causa raiz exata AINDA
   NÃO confirmada (precisa do log melhorado rodando de novo em PROD
   pra saber se é rede/provedor ou resposta fora do contrato) --
   corrigido o que dava pra corrigir sem esse dado: log agora separa
   `stage="generate"` (falha na chamada em si) de `stage="parse"`
   (resposta recebida mas fora do contrato, com preview truncado do
   conteúdo bruto) -- próxima rodada de `--dry-run` em PROD já traz a
   causa real.
2. **Relatório contraditório**: o resumo dizia "Resolvidos nesta
   rodada: 13 de 100", mas o detalhe por produto mostrava 0
   "RESOLVIDO" e 100 "continua sem identidade" -- bug real, não só
   cosmético. Causa: em `--dry-run`, o `session.rollback()` de um lote
   SEGUINTE (mesmo que ele falhe na própria chamada de IA -- o
   rollback roda incondicionalmente ANTES) desfazia da sessão a
   resolução de um lote ANTERIOR nunca commitada; o script reconsultava
   o banco DEPOIS de tudo (`session.get`), então via só o estado final
   pós-rollback. Corrigido com `outcome_sink` -- `reprocess_unresolved_
   products` agora aceita um `dict` opcional que populate no MOMENTO de
   cada resolução, nunca dependendo do estado da sessão sobreviver a um
   rollback posterior. `apply_learned_identity` passou a devolver o
   `Product` canônico resultante (antes `None`) para permitir isso sem
   consulta extra. Regressão coberta por teste de integração real
   reproduzindo exatamente o cenário (lote com sucesso seguido de lote
   com falha, `tests/integration/test_product_identity_learning.py`).

Nada foi aplicado/persistido em PROD nesse `--dry-run` (confirmado) --
o operador corretamente parou antes de qualquer `--apply`, exatamente
como as instruções de deploy pediam. Backlog real confirmado: **371**
Products sem `identity_key`.

**Segunda rodada em PROD sob `v1.3.21`** (mesmo backlog, mesmo
`--dry-run --limit 100`) confirmou o bug 2 corrigido (resumo/detalhe
batendo: 10 merges + 3 resolvidos = 13, igual ao resumo) e achou um
gap real no bug 1: os campos `stage`/`error`/`raw_content_preview`
adicionados ao log NUNCA apareciam na saída, mesmo com o código
correto -- **causa raiz achada pelo próprio operador antes de escalar**:
`backend/scripts/reprocess_unresolved_product_identity.py` nunca
chamava `configure_logging` (`app/core/logging.py`, formatter JSON em
stdout, já usado por `app.main`/`app.collection.worker`/`app.telegram.
worker`) -- sem handler configurado, o `logging` padrão do Python cai
no `lastResort` (só `%(message)s`, descarta qualquer campo de `extra`).
Nenhum script deste diretório chamava isso antes; só importou de
verdade agora que um `extra={}` precisou ser lido de fato. Corrigido em
`v1.3.22`: `main()` chama `configure_logging(settings.log_level,
service_name="aishoppingagent-reprocess-unresolved-product-identity",
environment=settings.environment)` antes de rodar, mesmo padrão dos
outros entry points. Confirmado manualmente que os campos aparecem no
JSON depois da correção.

**Terceira e quarta rodadas em PROD sob `v1.3.22`** (com o log já
legível) revelaram duas falhas DIFERENTES em duas tentativas seguidas
do mesmo lote de 8 títulos -- não um evento isolado repetindo:

- Rodada 3: `stage="generate"`, `error="AIProviderError: cesar_core_
  request_failed"` -- o próprio César Core devolveu `502 Bad Gateway`
  em `POST /v1/ai/generate`, antes de chegar a qualquer modelo.
- Rodada 4 (mesmo lote, nova tentativa): César Core respondeu `200 OK`,
  a cascata passou por Gemini e Groq (nenhum apareceu como `ai_model`)
  e caiu no fallback `openrouter/free`, que devolveu `stage="parse"`,
  `raw_content_preview="User Safety: safe"` -- texto solto, não o array
  JSON pedido.

**Causa raiz real (achado do usuário, não do dev)**: o modo de item
único (`extract_product_identity_via_ai`) manda o título como TEXTO
NATURAL puro na mensagem do usuário; o modo em lote mandava um array
JSON CRU (`json.dumps(payload_in)`) como mensagem do usuário, sem
nenhuma frase em linguagem natural ancorando a tarefa ali do lado dos
dados -- `_FIELD_INSTRUCTIONS` (a complexidade das regras) é IDÊNTICA
nos dois modos, então a diferença real não era "instrução complexa
demais", era "formato de mensagem atípico pra um modelo de chat mais
fraco/gratuito". Isso explica plausivelmente o `"User Safety: safe"`
(um modelo mais fraco recebendo um blob JSON cru como mensagem inteira,
sem frase alguma, pode reagir como se fosse algo pra classificar/
moderar em vez de processar). **Nunca confirmado 100% sem acesso a logs
do lado do provedor** -- é a explicação mais plausível e testável, não
uma certeza absoluta.

**Corrigido em `v1.3.23`**: a mensagem do usuário no modo lote agora
ancora a tarefa em linguagem natural imediatamente antes do JSON
(`"Processe os N títulos abaixo, um por \"id\", seguindo exatamente as
instruções acima:\n\n" + json.dumps(...)`) -- mesma técnica de
prompt engineering já validada implicitamente pelo modo de item único
(que sempre manda texto natural, nunca JSON cru). Testes ajustados
(os fakes de IA agora extraem o array a partir do primeiro `[`, não
fazem mais `json.loads` da mensagem inteira), suíte completa passando.

**Quinta rodada em PROD sob `v1.3.23`**: falha DIFERENTE de novo --
`stage="parse"`, `error="JSONDecodeError: Expecting value: line 1
column 1 (char 0)"`, `raw_content_preview` mostrando o modelo narrando
o próprio raciocínio em texto corrido ("We need to produce JSON array
with 8 objects...") e sendo **cortado no meio da frase**, nunca
chegando a emitir o JSON. A correção de mensagem não resolveu -- só
trocou o sintoma.

**Causa raiz real, nº 2 (achado do usuário de novo, ao questionar minha
suposição de "infraestrutura indisponível"):**
`Settings.cesar_core_max_tokens` (`backend/app/core/config.py:56`,
default **1024**, teto 4096) é um valor ÚNICO e FIXO aplicado a TODA
chamada de IA do app inteiro (`CesarCoreAIProvider.__init__`,
`manager.py:85`) -- sem exceção por propósito/tamanho de tarefa. Um
modelo de "reasoning" gratuito (fallback da cascata, alcançado porque
Gemini/Groq não estão respondendo) narra o raciocínio ANTES de
responder -- 1024 tokens bastam pra 1 item, mas não pra narrar +
produzir um array de 8 objetos estruturados, cortando a resposta no
meio. Essa causa explica plausivelmente as TRÊS falhas observadas sob
uma única teoria coerente (502 à parte, que é transporte, não
geração): tanto o `"User Safety: safe"` (possível cabeçalho de
segurança emitido antes do corte) quanto o raciocínio cortado batem
com "a resposta terminou antes da hora por falta de espaço".

**Corrigido em `v1.3.24`**: `AIRequest` ganhou `max_tokens: int | None
= None` (validado `1 <= max_tokens <= 4096` quando informado, mesmo
teto de `Settings.cesar_core_max_tokens`) -- `None` (todo chamador
existente, sem exceção) preserva o comportamento de sempre;
`CesarCoreAIProvider.generate` usa
`request.max_tokens or self._max_tokens` tanto no payload enviado
quanto na validação de `completion_tokens` da resposta.
`extract_product_identities_via_ai_batch` passa `max_tokens=4096`
(o teto já permitido globalmente pelo app, nunca mais que isso) só
para a chamada em lote. Mudança de contrato compartilhado
(`app/ai_provider/contracts.py`/`cesar_core.py`) -- verificados os 6
chamadores de `AIRequest` no app inteiro; só `identity_ai.py` passa
`max_tokens` explícito, os outros 5 continuam com `None` (idênticos a
antes). 54 testes de contrato de IA + 24 de `identity_ai` + 20 de
integração de identidade, todos passando (a suíte de contrato de IA só
roda localmente com `--basetemp` por causa do bloqueio de ACL do
diretório temp do Windows já documentado -- não é regressão).

**Sexta rodada em PROD sob `v1.3.24` -- SUCESSO, causa raiz confirmada
de verdade:** `--dry-run --limit 10`, mesmo lote de sempre, sem
`product_identity_ai_batch_extraction_failed`. A cascata alcançou
`openai/gpt-oss-120b` (Groq, não mais o fallback de reasoning
gratuito) e completou normalmente com `max_tokens=4096`. 9 de 10
candidatos resolvidos (2 por reaproveitamento sem custo de IA + 7
extrações reais, todas placas-mãe X870E de marcas/variantes
diferentes, plausíveis contra os títulos reais -- `MSI`/`NZXT`/
`ASRock`/`ASUS`, wifi/preto/branca corretos por item). O único não
resolvido (uma memória RAM) não teve falha de lote -- caiu no caminho
normal de "não passou grounding/foi para revisão humana", comportamento
esperado, não bug.

**Sétima rodada em PROD -- primeiro `--apply --limit 100` REAL,
autorizado pelo usuário, crashou de verdade (2026-09-20):** o
mecanismo de EXTRAÇÃO estava correto (validado na rodada 6), mas o
mecanismo de MERGE de duplicatas tinha um bug real, preexistente,
nunca antes exercitado contra Product com histórico de uso:

```
sqlalchemy.exc.IntegrityError: (psycopg.errors.RestrictViolation) update or delete on table "products"
violates RESTRICT setting of foreign key constraint "fk_mission_product_alert_state_product_id_products"
```

`apply_learned_identity` só migrava `Offer` antes de apagar o Product
ad-hoc fundido -- `products.id` também é referenciado com `ON DELETE
RESTRICT` por outras 6 tabelas. Investigação completa (mapeamento de
todas as FKs, semântica de merge de cada tabela, confirmação de que o
caminho AO VIVO do orquestrador tem o MESMO risco) mostrou:
`market_price_assessments`/`historical_bootstraps`/
`external_price_references`/`mission_product_selections` nunca têm
linha para um ad-hoc (protegidas por `identity_key IS NOT NULL` no
próprio ponto de inserção) -- só `mission_product_alert_state` (não
tem essa proteção, alertas disparam por relevância de oferta) e
`purchase_confirmations` precisavam de tratamento real.

**Corrigido em `v1.3.25`:**
- `mission_product_alert_state`: `_merge_mission_product_alert_state`
  funde de verdade quando a MESMA Mission já tem checkpoint nos dois
  Products (mesma PK composta `(mission_id, product_id)`, colisão real
  -- é o que crashava) -- preserva o MENOR `best_notified_amount`
  (nunca esquece um preço mais baixo já alertado, é a garantia que
  impede alerta duplicado) e os campos do alerta MAIS RECENTE
  (`last_notified_at` maior). Sem conflito, só reaponta a linha.
- `purchase_confirmations`: achado um SEGUNDO crash ao tentar corrigir
  como as demais -- a tabela é IMUTÁVEL por trigger de banco
  (`block_purchase_trail_mutation`, "evidência permanente de compra
  confirmada"), nem um `UPDATE` de `product_id` é aceito. Decisão do
  usuário: sem ocorrência real hoje (a Mission encerra e para de
  coletar assim que a compra é confirmada) -- em vez de tentar migrar
  (impossível), `apply_learned_identity` agora devolve `None` (nunca
  crasha) quando o ad-hoc tem `PurchaseConfirmation`; o chamador trata
  como "não resolvido nesta rodada", registra `outcome_sink` com uma
  mensagem `BLOQUEADO` explícita, nunca conta como resolvido nem
  propaga exceção.

Regressão coberta por 2 testes de integração reais em
`tests/integration/test_apply_learned_identity_merge.py`: o cenário
exato do primeiro crash (merge com conflito de alerta em 2 Missions,
uma delas colidindo) e do segundo (bloqueio seguro sem alterar nada).
70 testes de integração relevantes passando.

**Item 1 (backfill) pronto para `--apply` real em PROD de novo** --
mecanismo de extração (rodada 6) E de merge (esta correção) validados.
PROD permanece parado/intocado desde o crash da rodada 7 -- nada foi
perdido (a transação reverteu limpo, backlog confirmado intacto em 372
pelo próprio operador antes de reportar). Decisão de retomar `--apply`
(backlog de ~372, em rodadas de `--limit 100`) e de ligar
`product_identity_learning_enabled` (item 2) seguem pendentes de nova
autorização explícita do usuário.

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

## Ação no deploy (decisão do usuário, 2026-09-20)

Na próxima rodada de deploy em PROD, fazer os dois passos abaixo
juntos, nesta ordem, como parte do mesmo prompt/sessão de deploy (não
como TASKs separadas):

1. **Ligar `product_identity_learning_enabled=true`** em PROD (variável
   de ambiente do `collection_worker` nativo) -- cobre itens novos a
   partir da próxima coleta.
2. **Rodar `python -m scripts.reprocess_unresolved_product_identity
   --count-only`** primeiro (zero custo de IA) para saber o tamanho
   real do backlog antes de decidir o ritmo.
3. **Rodar `python -m scripts.reprocess_unresolved_product_identity
   --apply --limit 100`** contra o banco real de PROD (repetir em
   rodadas de até 100 -- teto do script -- até reportar 0 candidatos)
   -- pega o backlog que já existe hoje (placas-mãe incluídas, ~371
   Products conhecidos nesta rodada), não só o que chegar depois da
   flag ligada. Lote de 10 títulos por chamada de IA (~10x menos
   chamadas que 1-por-produto).

Sem o passo 3, ligar só a flag NÃO resolve o backlog já parado no banco
(ela só afeta coleta nova, ver "Preflight" acima) -- os dois precisam
acontecer juntos pra cobrir "itens novos" e "itens já pesquisados" ao
mesmo tempo, conforme pedido explícito do usuário.
