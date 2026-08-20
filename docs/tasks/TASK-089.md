# TASK-089 — Suporte a preço à vista e parcelado

Status: **Implementada e testada, incluindo apresentação Telegram
(2026-08-17)**. Suíte não-integração verde (1264 passed, 1 skipped, 90,14%
cobertura), integração real 14 passed em PostgreSQL 18 descartável, ruff
limpo, `alembic check` sem drift. Mensagens de alerta/pré-lista agora
mostram `💰 À vista`/`💳 Parcelado` dinamicamente (ver "Extensão de
apresentação Telegram" abaixo). Interpretação de pedido de compra
parcelada pelo usuário ("quero em 6x") foi explicitamente adiada para
V2 -- ver seção correspondente.

## Correção arquitetural (DEC-069, 2026-08-17)

A investigação real de campo (ver seção original abaixo) revelou que uma
oferta pode ter **várias** condições de parcelamento simultâneas -- o
desenho original desta TASK (três campos escalares em `Offer`/
`PriceObservation`) foi **abandonado antes de qualquer código ser
consolidado** e substituído por uma relação 1:N (`OfferInstallmentOption`).
Ver `DEC-069` em `docs/DECISION_LOG.md` para a decisão completa; a seção
"Investigação real obrigatória" e as regras originais abaixo continuam
válidas como registro histórico do que motivou a correção -- não foram
apagadas.

## Implementação (resumo objetivo)

- **Modelo**: `OfferInstallmentOption` (`backend/app/collection/models.py`)
  -- `id`, `price_observation_id` (FK para `price_observations.id`, não
  para `offers.id`), `installment_count`, `installment_amount`,
  `installment_total_amount` (nullable), `discount_percent` (nullable),
  `interest_kind` (`InstallmentInterestKind`: `interest_free`/
  `with_interest`/`unknown`, mesmo padrão de `MarketplacePartyKind`).
  Vinculada à observação (não à oferta) para herdar de graça a semântica
  "estado atual = opções da observação mais recente", sem UPDATE/DELETE/
  flag -- consistente com o restante do projeto ser append-only.
  `UniqueConstraint(price_observation_id, installment_count)` impede
  duplicata da mesma quantidade de parcelas na mesma observação.
- **Migration**: `20260817_0001_add_offer_installment_options.py` -- cria a
  tabela nova, nenhuma coluna alterada em tabelas existentes, downgrade
  remove a tabela inteira. Validada em PostgreSQL 18 descartável: upgrade
  completo do baseline até o head, `alembic check` limpo, downgrade -1 e
  upgrade novamente sem erro.
- **Contratos** (`backend/app/collection/contracts.py`): `RawInstallmentOption`
  (uma condição de parcelamento crua: `installment_count`, `raw_amount`,
  `raw_total_amount` opcional, `discount_percent` opcional, `interest_kind`)
  e `InstallmentInterestKind`. `RawCollectedOffer.installment_options`
  (tupla, default vazia, nunca com `installment_count` repetido).
- **Normalização** (`backend/app/collection/normalization.py`):
  `NormalizedInstallmentOption` com valores já em `Decimal`, reaproveitando
  `PriceNormalizer._amount` (mesmo parser de dinheiro já usado para
  preço/frete, nenhum parser novo). `NormalizedCollectedOffer.installment_options`
  é uma `@property` recalculada a partir de `raw_offer` a cada acesso
  (mesmo padrão de `seller_kind`/`fulfillment_kind`) -- necessário porque
  `enrich_installment_options` só atualiza `raw_offer` depois que a oferta
  já foi normalizada uma vez; um campo congelado ficaria desatualizado.
  Uma opção malformada (ex.: valor não numérico) é descartada e logada
  (`installment_option_normalization_failed`), nunca derruba a oferta
  inteira.
- **Providers** (`backend/app/collection/providers/`):
  - **Card (as 4 lojas, `stores.py`)**: cada `extract()` captura o resumo
    de parcelamento já visível no card de busca (schema comum via
    `_apply_installment_summary`/`_installment_options_from_row` em
    `base.py`) -- Pichau (`price_parcelado_text`/`price_total`, com total
    explícito), Terabyte (`.product-item__juros`, sem total), Amazon
    (bloco `.a-row.a-size-base.a-color-base`, contagem **variável** --
    confirmado 10x e 12x na mesma busca, nunca assumida fixa), KaBuM!
    (span com "PIX"/"R$", nunca menciona "sem juros" -- `interest_kind`
    fica `unknown`, nunca assumido `interest_free`).
  - **Página individual (só Pichau e Terabyte, novo hook
    `resolve_installment_options` + `enrich_installment_options` em
    `base.py`, mesma disciplina de `enrich_marketplace_parties`: limitado,
    ordenado por preço, sequencial, sem retry, para no primeiro
    401/403/429)**: Pichau lê a tabela "PARCELAMENTO" (seletor por
    substring `[class*="installment"]`, robusto a classes MUI hasheadas)
    -- percentual de desconto por linha lido do texto atual, nunca
    hardcoded (a mesma investigação achou 15% fixo num produto com selo
    promocional e uma escala 10%→5%→5%→3%→3%→3%→sem-juros noutro sem
    selo). Terabyte lê o painel "VER PARCELAMENTO" (já vem no HTML,
    `id="detalheparcelamento"`, só visualmente recolhido -- lido via
    `textContent`, sem precisar clicar) -- faixas de desconto/juros
    também lidas do texto, incluindo a faixa real "com juros" a partir de
    certa quantidade. Amazon e KaBuM! **não** implementam o hook -- a
    investigação confirmou que não existe tabela equivalente (cliquei em
    "Ver opções de pagamento"/"Ver detalhes de parcelamento" nos dois,
    nada novo apareceu); `enrich_installment_options` nunca abre
    `BrowserSession` para eles.
  - **Merge card + página** (`_merge_installment_options`, `base.py`):
    campo a campo, nunca substitui a opção inteira -- o card às vezes é a
    única fonte do total (Pichau) para a mesma quantidade que a página
    detalha melhor (desconto/juros); perder o total do card ao "vencer"
    com a página destruiria informação real sem necessidade. Nunca
    duplica `installment_count`.
- **Persistência** (`backend/app/collection/orchestration.py`):
  `_process_claim` chama `enrich_installment_options` logo após
  `enrich_marketplace_parties` (mesmo ponto, já sobre o conjunto pequeno
  e final de candidatos pós-filtro -- nunca a lista bruta do card),
  tolerante a falha (loga e segue com o que já tinha, nunca derruba o
  batch). `_persist_phase_a` grava um `OfferInstallmentOption` por opção
  logo após o `flush()` da `PriceObservation`, usando o `id` dela recém
  gerado.

## Modelos e opções por loja (confirmado na investigação real)

| Loja | Total explícito por opção | Quantidade de parcelas | Percentual de desconto | "Sem juros" declarado |
| --- | --- | --- | --- | --- |
| Pichau (card) | Sim (`price_total`) | Fixa no card (ex. 12x) | Não no card (só no PIX à vista) | Sim |
| Pichau (página individual) | Não | 1x até a maior faixa (ex. 6x ou 12x) | Sim, variável por produto/parcela | Sim, por linha |
| Terabyte (card) | Não | Fixa no card (ex. 12x) | Não | Sim |
| Terabyte (página individual) | Não | 1x até 18x | Sim, variável (10%/7%/7%/0%...) | Sim, inclusive **"com juros"** a partir de certa faixa |
| Amazon (card) | Não | Variável (10x ou 12x confirmados) | Não | Sim, quando presente |
| KaBuM! (card) | Não | Variável (10x confirmado) | Não | **Nunca declarado** -- fica `unknown` |

## Objetivo

Permitir que cada observação de preço preserve e apresente separadamente:

- preço à vista;
- preço total parcelado;
- quantidade de parcelas;
- valor de cada parcela.

O preço-alvo da missão continua sendo comparado exclusivamente com o preço à
vista. Parcelamento é informativo nesta TASK e não cria novo tipo de missão.

## Estado atual auditado

- `RawCollectedOffer` possui somente `raw_price`; os quatro providers devolvem
  um único preço por card.
- `NormalizedOffer` e `PriceObservation` possuem `amount`, hoje definido como
  preço anunciado do produto. `shipping_amount` e `total_amount` tratam frete,
  não parcelamento.
- `evaluate_price_alerts` compara `PriceObservation.amount`, deliberadamente,
  tanto para queda quanto para preço-alvo. Os nomes históricos
  `current_total`/`target_total` no payload dos eventos carregam `amount`, não
  `PriceObservation.total_amount`.
- Não existem campos persistentes para total parcelado, quantidade de parcelas
  ou valor da parcela.
- A apresentação da TASK-084 mostra um preço por oferta e precisa evoluir sem
  reverter imagem, link curto, uma oferta por mensagem ou checkpoints.

## Regras aprovadas

1. Preço à vista e parcelado permanecem em campos semanticamente separados.
2. Quantidade e valor das parcelas só são registrados quando explicitamente
   informados pela loja.
3. O sistema nunca calcula, estima, divide ou infere parcelas ou total
   parcelado.
4. Todos os novos campos são opcionais quando a loja não fornece a informação.
5. Dados e histórico existentes são preservados integralmente, sem backfill
   inventado.
6. `PriceObservation.amount` continua representando o preço à vista usado por
   comparação, ranking, queda e preço-alvo.
7. Preço parcelado é apenas informação adicional; nenhuma missão, preferência,
   ranking ou alerta baseado em parcelamento será criado.
8. A apresentação completa, quando todos os componentes estiverem explícitos,
   segue:

```text
💰 À vista: R$ 3.499,90
💳 Parcelado: 10x de R$ 379,99 — total R$ 3.799,90
```

9. Informação parcial nunca autoriza completar a frase por cálculo. A
   investigação deve definir como preservar e apresentar somente os campos
   realmente fornecidos, sem produzir uma equivalência financeira implícita.

## Investigação real obrigatória

Antes de definir seletores ou alterar contratos, investigar isoladamente os
cards/resultados reais de:

1. Pichau;
2. Terabyte;
3. Amazon.com.br;
4. KaBuM!.

Para cada fonte, registrar com evidência sanitizada:

- onde aparece o preço à vista e como ele é rotulado;
- se o total parcelado é explícito ou se existe apenas `Nx de valor`;
- onde aparecem quantidade e valor da parcela;
- diferenças entre PIX, boleto, cartão e promoções condicionais;
- comportamento quando só um dos preços existe;
- se os dados estão no card, em estado estruturado já carregado ou somente na
  página individual.

Priorizar informação já presente no card ou em dados estruturados da mesma
página de busca. Se uma fonte exigir abrir cada página individual, parar e
apresentar custo, latência e risco operacional antes de ampliar a coleta. Não
assumir que as quatro lojas usam a mesma estrutura. Não tocar dados de produção
e limpar containers, caches e artefatos temporários da investigação.

## Modelo e persistência previstos

- O preço continua pertencendo ao retrato temporal `PriceObservation`, nunca à
  identidade estável `Offer`.
- `PriceObservation.amount` é preservado como preço à vista e não é renomeado
  nem reinterpretado para observações históricas.
- A migration deverá adicionar campos nullable equivalentes a:
  `installment_total_amount`, `installment_count` e `installment_amount`.
- Nomes finais serão confirmados na implementação após comparar os padrões de
  nomenclatura existentes; não editar migration histórica.
- Valores monetários usam a mesma precisão de `amount`; contagem de parcelas é
  inteira positiva. Valores informados devem ser não negativos.
- Ausência permanece `NULL`; não usar zero como sentinela e não criar default.
- Nenhum backfill calcula dados parcelados de observações antigas.
- Contratos bruto e normalizado devem carregar cada evidência separadamente e
  rejeitar valores inválidos sem transformar outro campo válido em inválido.
- A evidência pública limitada deve permitir auditar a origem sem armazenar
  conteúdo excessivo ou sensível.

## Escopo de implementação

1. Documentar a investigação real das quatro lojas antes de congelar seletores.
2. Evoluir `RawCollectedOffer`, normalização e persistência histórica.
3. Adaptar Pichau, Terabyte, Amazon e Kabum individualmente.
4. Criar migration compatível, upgrade/downgrade seguro e constraints.
5. Preservar `amount` como base de comparação e preço-alvo.
6. Propagar os campos parcelados nos eventos apenas quando necessários à
   apresentação, mantendo compatibilidade com eventos antigos.
7. Adaptar pré-listas, atualizações e alertas da TASK-084 para mostrar preço à
   vista e, quando houver evidência suficiente, a linha parcelada.
8. Preservar imagem, link curto, uma oferta por mensagem, fallback textual e
   checkpoint por evento/oferta/parte.
9. Atualizar documentação de banco, coleta, preços, eventos e Telegram.

## Comparação, ranking e alertas

- Queda de preço e preço-alvo continuam comparando somente
  `PriceObservation.amount` (à vista).
- Ranking e seleção das melhores ofertas continuam usando a base vigente; o
  preço parcelado não desempata nem reordena resultados.
- `installment_total_amount`, `installment_count` e `installment_amount` não
  participam de `evaluate_price_alerts`.
- Alertas podem apresentar a informação parcelada da observação atual, mas sua
  emissão nunca é causada por mudança no parcelamento.
- Eventos e consumidores já persistidos devem continuar processáveis quando os
  novos campos estiverem ausentes.

## Apresentação

Quando o preço à vista existir:

```text
💰 À vista: {{preco_a_vista}}
```

Quando total, quantidade e valor da parcela forem todos explicitamente
informados:

```text
💳 Parcelado: {{parcelas}}x de {{valor_parcela}} — total {{total_parcelado}}
```

Campos parcelados ausentes não geram linha vazia, `N/A`, zero, cálculo ou
estimativa. Casos parciais devem seguir a política definida e testada após a
investigação, sempre exibindo somente evidência explícita.

## Relação com a TASK-077

- **TASK-077:** vendedor e responsável pela entrega na Amazon e na KaBuM!.
- **TASK-089:** preços à vista e parcelado nos quatro providers.

Não há dependência funcional rígida entre elas. A TASK-089 está formalmente
registrada antes do início da TASK-077, como solicitado, mas não precisa ser
implementada primeiro. Como ambas alteram providers, `PriceObservation`,
orquestração e mensagens, não devem ser implementadas em paralelo. Ordem
recomendada: TASK-077 e depois TASK-089; se a ordem for invertida, a segunda
TASK deve revisar o head Alembic e integrar as mudanças já presentes.

## Fora de escopo

- missão, alerta, filtro, ranking ou preço-alvo baseado em parcelamento;
- cálculo de juros, CET, desconto percentual ou equivalência entre modalidades;
- inferir total por `quantidade × parcela` ou parcela por divisão;
- credenciais, login ou consulta autenticada nas lojas;
- frete/parcelamento autenticado da V1.2 (`DEC-045`);
- mudanças de vendedor/fulfillment da TASK-077;
- novas lojas ou providers;
- alterar o histórico existente para preencher campos desconhecidos.

## Testes obrigatórios

1. Casos reais sanitizados de cada provider com preço à vista e parcelado.
2. Provider com somente preço à vista mantém campos parcelados `NULL`.
3. Dados parciais não são completados por cálculo ou inferência.
4. Normalização rejeita valores inválidos e preserva os válidos independentes.
5. Persistência mantém cada observação append-only com os campos corretos.
6. Observações antigas continuam legíveis e semanticamente inalteradas.
7. Migration em PostgreSQL 18.4 descartável: upgrade, constraints, downgrade e
   novo upgrade quando seguro; `alembic check` sem drift.
8. Preço-alvo e queda continuam usando exclusivamente `amount` à vista.
9. Ranking e seleção das melhores ofertas não mudam por parcelamento.
10. Eventos antigos sem novos campos continuam consumíveis.
11. Mensagem completa segue exatamente o formato aprovado.
12. Ausência de parcelamento não produz linha vazia nem impede o envio.
13. Uma oferta por mensagem, imagem/fallback, link curto e checkpoints da
    TASK-084 permanecem íntegros.
14. Runner oficial de integração, suíte não-integração, Ruff e
    `git diff --check` aprovados.

## Critérios de aceite

1. Evidência real das quatro lojas documentada antes dos seletores finais.
2. Preço à vista permanece em `PriceObservation.amount` e continua sendo a
   única base de preço-alvo, queda e ranking.
3. Campos parcelados são opcionais, históricos e nunca inferidos.
4. Histórico anterior à migration permanece intacto e válido.
5. Os quatro providers preservam corretamente os campos que cada loja expõe.
6. Mensagens mostram o formato aprovado quando a evidência completa existe e
   degradam por ausência sem inventar informação.
7. Nenhum comportamento da TASK-077 é implementado ou alterado.
8. PostgreSQL 18.4 aceita upgrade/downgrade/upgrade seguro; head único e
   `alembic check` limpo.
9. Testes unitários, integração e regressão passam integralmente.
10. Nenhuma alteração é aplicada automaticamente ao banco ativo antes de
    revisão, rebuild e deploy explicitamente autorizados.

## Documentação afetada na futura implementação

- `docs/COLLECTION_ADAPTER.md`;
- `docs/DATABASE.md`;
- `docs/PRICE_ENGINE.md`;
- `docs/MISSION_CRITERIA.md`;
- `docs/EVENTS.md` e contratos de eventos afetados;
- documentação das mensagens Telegram;
- `docs/PROJECT_CONTEXT.md`, `docs/CHANGELOG.md`, `docs/DECISION_LOG.md`,
  `docs/ROADMAP.md`, `docs/BACKLOG.md` e índice de TASKs.

## Testes adicionados (implementação real, 2026-08-17)

- `tests/test_store_provider_installments.py` (novo): card de cada uma das
  quatro lojas (com/sem total, contagem variável na Amazon, ausência de
  "sem juros" na KaBuM!, NuPay/"até 36x" nunca virando opção inventada);
  `resolve_installment_options` da Pichau (tabela real com desconto
  variável por linha, incluindo o produto sem selo promocional que a
  investigação encontrou com escala 10%→5%→5%→3%→3%→3%→sem juros) e da
  Terabyte (painel 1x-18x com valores DIFERENTES dos originalmente
  encontrados, provando que o parser lê o texto atual, nunca uma tabela
  fixa; sufixo "+ Frete Grátis" ignorado sem quebrar o parsing); merge
  card+página preservando o total do card ao enriquecer com o detalhe da
  página, nunca duplicando `installment_count`.
- `tests/test_price_normalization.py`: normalização para `Decimal`, total
  nunca calculado, opção malformada descartada sem derrubar a oferta,
  `installment_options` recalculada de `raw_offer` após `enrich_*`
  (prova a necessidade da `@property`, não um campo congelado).
- `tests/test_collection_adapter.py`: `enrich_installment_options`
  repassa sem alteração quando o provider não implementa a extensão,
  delega quando implementa, rejeita mudança de quantidade/ordem e fonte
  não registrada.
- `tests/test_store_providers.py`: `enrich_installment_options` limitado/
  ordenado/sequencial, para no primeiro bloqueio (403), nunca abre
  `BrowserSession` para providers sem o hook (Amazon/KaBuM!).
- `tests/test_collection_orchestration_async.py`: `_persist_phase_a` grava
  `OfferInstallmentOption` vinculada ao `id` da `PriceObservation` recém
  criada; oferta sem opções não gera nenhuma linha extra.
- `tests/test_database.py`: `offer_installment_options` adicionada à lista
  de tabelas implementadas.

Suíte completa (primeira rodada, antes da auditoria abaixo): 1245 passed,
1 skipped, 90,12% cobertura. Ruff limpo. `alembic check` sem drift em
PostgreSQL 18 descartável (upgrade completo do baseline, downgrade -1,
upgrade novamente). Nenhuma chamada real ao Gemini; nenhuma missão/teste
real de loja além da investigação read-only via navegador (Pichau,
Terabyte, Amazon, KaBuM! reais, sem autenticação, sem tocar dados de
produção).

## Auditoria técnica crítica (segunda rodada, 2026-08-17)

Após a primeira implementação, foi feita uma auditoria cética -- "não
assumir que o desenho está correto só porque os testes passam" -- cobrindo
10 pontos, cada um com evidência real, não suposição.

1. **`discount_percent` nunca inferido**: releitura de
   `_parse_pichau_installment_row`, `_apply_installment_summary` e
   `PriceNormalizer._installment_options` confirma que o único caminho de
   escrita é a captura literal de dígitos de um texto de desconto já
   emitido pela própria loja (ex.: `15% de desconto no PIX`); não existe
   nenhuma subtração/divisão de preços no pipeline. Prova adversarial:
   `test_pichau_discount_percent_absent_without_explicit_percent_text_even_when_math_would_suggest_one`
   e `test_terabyte_discount_percent_reads_literal_digit_not_derived_from_price_diff`
   constroem cenários onde a matemática sugeriria um percentual diferente
   do texto (ou nenhum texto) e provam que o campo fica `None`/lê o dígito
   literal, nunca calcula.
2. **`interest_kind` nunca inferido**: mesma releitura confirma que o
   único caminho de escrita é regex sobre texto explícito
   (`sem juros`/`s/juros` → `interest_free`; `com juros`/`c/juros` →
   `with_interest`); ausência de texto → `unknown`, nunca um cálculo de
   juros implícito por diferença de total. Prova adversarial:
   `test_terabyte_interest_kind_never_derived_from_installment_total_comparison`
   monta uma opção "6x s/juros" com total MAIOR que uma opção "3x c/juros"
   (o oposto do que a matemática de juros sugeriria) e confirma que a
   classificação segue o texto, não a comparação de totais.
3. **Constraints monetárias reavaliadas**: `installment_amount` e
   `installment_total_amount` mudaram de `>= 0` para `> 0` -- divergência
   deliberada de `price_observations.amount` (`>= 0`), justificada porque
   um preço-base pode ser R$0,00 em um cenário promocional/gratuito real,
   mas uma parcela ou total parcelado de R$0,00 nunca representa uma
   condição comercial genuína, só um bug de parsing. `discount_percent`
   ganhou teto explícito `<= 100` (não existia precedente de campo
   "percentual" no schema para comparar; o limite protege contra valores
   sem sentido produzidos por um parser quebrado). Migration e modelo
   atualizados em conjunto; `test_check_constraints_accept_boundary_values`
   prova que `0.01` e `100.00` são aceitos nas bordas (sem off-by-one).
4. **`UNIQUE(price_observation_id, installment_count)` validada contra DOM
   real**: reinvestigação ao vivo (Pichau -- Palit RTX 5050; Terabyte --
   Zotac RTX 5090) confirmou que a linha do card
   (`Em até 12x de R$ 186,27 Sem juros no cartão` /
   `12x de R$ 2.450,98 sem juros no cartão`) é byte-idêntica em contagem,
   valor e texto de juros à linha correspondente da tabela da página
   individual (`12x de R$186,27 (sem juros)` /
   `12x de R$ 2.450,98 s/juros*`). Varredura completa do DOM de cada
   página (`document.querySelectorAll` sobre toda a página) não encontrou
   nenhuma segunda tabela/painel de parcelamento para uma modalidade de
   pagamento alternativa que pudesse colidir na mesma contagem. Não foi
   adicionado `payment_method` especulativo -- o comportamento real das
   duas lojas hoje não exige essa dimensão.
5. **`_merge_installment_options` auditada**: a mesma evidência do ponto 4
   mostra que card e página individual descrevem a MESMA condição
   comercial para a mesma contagem em Pichau e Terabyte, então o merge por
   `installment_count` é semanticamente correto para o escopo atual. O
   merge é campo-a-campo (`dataclasses.replace`), nunca substitui a opção
   inteira -- bug pego pelo próprio teste
   `test_merge_installment_options_page_enriches_without_losing_card_total`
   antes desta auditoria (a primeira versão descartava o
   `raw_total_amount` do card ao enriquecer com a página).
6. **Teste de integração real em PostgreSQL**: `tests/integration/test_offer_installment_options.py`
   (14 testes) roda contra um container PostgreSQL 18.4-alpine
   descartável via `scripts/run_integration_tests.py` (nunca toca
   produção) e prova: criação da `PriceObservation`; persistência de
   múltiplas `OfferInstallmentOption`; leitura de volta com valores
   corretos; FK rejeita `price_observation_id` órfão; UNIQUE rejeita
   contagem duplicada na mesma observação; CHECK rejeita valores
   inválidos (incluindo os novos limites `> 0`/`<= 100`, parametrizado);
   CHECK aceita os valores de borda; rollback transacional descarta o
   lote inteiro quando uma opção é inválida; duas observações sucessivas
   da mesma oferta mantêm conjuntos de opções independentes (a mais
   antiga não é alterada). Resultado: **14 passed**.
7. **Relação append-only (`→ PriceObservation`, não `→ Offer`) verificada,
   não só assumida**: o teste
   `test_successive_observations_keep_independent_option_sets` (parte do
   arquivo de integração acima) executa DUAS capturas reais via
   `CollectionOrchestrator.run_batch`, a segunda com opções diferentes da
   primeira (12x → 6x), e confirma no banco real que a observação antiga
   continua com suas opções originais (12x) e a nova tem só as suas (6x)
   -- nenhum UPDATE/DELETE ocorreu na primeira; "condição atual" é
   definida por qual observação é mais recente, nunca por mutação.
8. **Auditoria de performance/navegação**: `enrich_installment_options`
   roda em `_process_claim` sobre `enriched_raw`, que deriva de
   `selected_raw` -- o conjunto FINAL já filtrado pelo matching da
   TASK-075 e pelo teto de 3 candidatos genéricos da TASK-082, não os
   ~20 cards brutos de busca. Está limitado a
   `installment_option_max_candidates=3` por fonte, sequencial (sem
   concorrência interna), com timeouts de `BrowserSettings`
   (`navigation_timeout_ms=30_000`, `action_timeout_ms=10_000`, mesma
   configuração usada por `enrich_marketplace_parties`), parando no
   primeiro 401/403/429 e nunca derrubando a oferta principal em caso de
   falha (`try/except` por candidato). Amazon/KaBuM! nunca abrem
   `BrowserSession` para isso (`resolve_installment_options` não
   sobrescrito, confirmado por
   `test_installment_enrichment_skips_navigation_when_provider_has_no_hook`).
   Estimativa concreta: uma busca típica com Pichau e Terabyte
   selecionados gera no máximo `3 + 3 = 6` navegações adicionais por
   execução de missão (não centenas); Amazon/KaBuM! contribuem zero.
   `CollectionOrchestrator.max_concurrency` (1–4) limita quantas
   `_process_claim` -- e portanto quantas `BrowserSession` de
   enriquecimento -- rodam ao mesmo tempo.
9. **Fidelidade dos fixtures ao DOM real**: os HTML usados em
   `tests/test_store_provider_installments.py` reproduzem as classes,
   estrutura e texto literalmente observados na investigação (Pichau:
   `mui-12athy2-price_vista`/`price_parcelado_text`/tabela
   `installmentsWrapper`; Terabyte: `.product-item__juros`/
   `#detalheparcelamento`; Amazon: `.a-row.a-size-base.a-color-base` com
   a cadeia `à vista no Pix ou NuPay ... em até Nx de ... sem juros`;
   KaBuM!: span folha contendo `PIX` e `R$`), incluindo variações
   realmente vistas (escala de desconto 10%→5%→3%→sem juros na Pichau;
   valores diferentes dos originais na Terabyte, provando leitura ao
   vivo e não tabela fixa).
10. **Categorias de teste executadas nesta rodada** (explícito, sem
    ambiguidade): suíte não-integração completa via
    `pytest --ignore=tests/integration --ignore=tests/e2e --cov=app --cov-fail-under=90`
    → **1251 passed, 1 skipped**, cobertura 90,12%; integração real via
    `python scripts/run_integration_tests.py tests/integration/test_offer_installment_options.py`
    → **14 passed** contra PostgreSQL 18.4-alpine descartável;
    `alembic upgrade head` → `downgrade -1` → `upgrade head` → `alembic check`
    → sem drift, contra banco descartável dedicado (não o de integração);
    `ruff check .` → limpo; `ruff format --check .` → 5 arquivos
    pré-existentes fora do escopo desta TASK precisariam de reformatação
    (`backend/app/offers/models.py`,
    `backend/migrations/versions/20260816_0001_add_offer_media_delivery.py`,
    `docs/tasks/TASK-080.md`, `tests/test_task084_offer_delivery.py`,
    `tests/test_telegram_router.py` -- nenhum arquivo tocado por esta
    TASK); `git diff --check` → limpo. **`tests/e2e` não foi executado**
    (não requerido pelos critérios de aceite desta TASK e não há
    suíte e2e cobrindo parcelamento).

## Extensão de apresentação Telegram (terceira rodada, 2026-08-17)

As mensagens de alerta (`_render_alert`) e de pré-lista
(`_render_prelist_block_async`/`_render_prelist_errata_async`, em
`backend/app/telegram/notifications.py`) passaram a mostrar duas linhas
sempre que houver dado real: `💰 À vista: {preço}` (sempre) e
`💳 Parcelado: {resumo}` (só quando existe ao menos uma
`OfferInstallmentOption` persistida para a `PriceObservation` exata
sendo apresentada -- nunca a mais recente da oferta, a mesma observação
do evento/payload). Nenhum valor é hardcoded; todas as linhas são
compostas a partir de dados carregados via `session.scalars(select(...))`
por observação.

**Campo novo (`is_highlighted`)**: como uma oferta pode ter várias opções
de parcelamento persistidas (Pichau/Terabyte), foi preciso saber, no
momento de renderizar, qual delas a própria loja destacou no card da
busca -- essa informação se perdia depois do merge card+página
individual (`_merge_installment_options`). Solução mínima: um booleano
`is_highlighted` em `RawInstallmentOption`/`NormalizedInstallmentOption`/
`OfferInstallmentOption` (contrato → normalização → modelo → migration
`20260817_0001`, editada em vez de nova revisão pois ainda não fora
aplicada em produção), carimbado exclusivamente em
`_installment_options_from_row` (a única origem possível, já que o card
nunca expõe mais de uma linha) e preservado (nunca recalculado) por
`_merge_installment_options`.

**Regra de seleção do resumo** (`_select_installment_summary_option`,
determinística, sem IA, sem cálculo de vantagem financeira):
1. opção com `is_highlighted=True`;
2. sem destaque conhecido, maior `installment_count` entre as
   `interest_free`;
3. sem nenhuma `interest_free`, maior `installment_count` disponível;
4. sem nenhuma opção persistida, a linha `💳` não aparece.

`interest_kind=unknown` nunca vira texto literal (sufixo vazio).
`installment_total_amount` só aparece (`— total {valor}`) quando a loja
declarou explicitamente; nunca `count × amount`. `discount_percent`
permanece no banco para consulta futura, mas não entra nesta linha
resumida (decisão explícita do usuário, para não poluir o alerta).

**Testes**: 13 novos em `tests/test_telegram_notifications.py` --
`_installment_line`/`_select_installment_summary_option` isolados
(vazio sem opção, `interest_free`, `with_interest`, `unknown` sem a
palavra aparecer, total explícito, total ausente, destaque do card
vence, fallback para maior `interest_free`, fallback para maior geral
sem `interest_free`, `None` sem opções) e end-to-end via
`process_telegram_notifications`/`process_telegram_prelist_notifications`
com `session.scalars` mockado (linha completa com destaque+total, oferta
sem parcelamento nunca mostra `💳`, múltiplas opções persistidas mas só a
destacada aparece na pré-lista, sem linha em branco extra entre
`💰 À vista` e `🔎 Missão`). `_fake_session_factory()` ganhou um default
`session.scalars = AsyncMock(return_value=[])` para as chamadas
existentes que não testam parcelamento. Teste de integração real
(`tests/integration/test_offer_installment_options.py`) estendido para
gravar e ler `is_highlighted` de volta contra PostgreSQL descartável.

## V2 (explicitamente adiado pelo usuário em 2026-08-17)

Depois de uma auditoria própria revelar que não existe, em lugar nenhum
do projeto, rastreamento de "oferta apresentada" para uma resposta em
texto livre resolver contra (nem `callback_query`/`InlineKeyboard`, nem
ligação entre o fluxo de confirmação de compra já existente --
`backend/app/purchase/confirmation.py`, TASK-040/041 -- e o Telegram), o
usuário decidiu reduzir o escopo desta TASK de volta para só apresentação.
Ficam para uma V2 futura, fora desta TASK:
- novo `IntentKind` para parcelamento (`SELECT_PURCHASE_OPTION` ou
  equivalente);
- interpretação de frases como "quero em 6x"/"quero parcelado";
- escolha de quantidade de parcelas pelo usuário;
- mecanismo de rastreamento de "oferta apresentada" por usuário/chat/
  missão;
- vínculo entre a resposta do usuário e a `PriceObservation` correta;
- qualquer alteração no `IntentInterpreter` ou no fluxo de compra/
  confirmação;
- IA analisando se um parcelamento "vale a pena", comparação entre
  opções, cálculo de acréscimo percentual ou ranking de condições.

## Limitações reais conhecidas (não resolvidas nesta rodada)

- Não foi confirmado se KaBuM!/Amazon têm alguma tabela de parcelamento
  atrás de login/cupom específico -- só o que é publicamente visível sem
  autenticação foi investigado, conforme escopo (nunca usar credenciais
  nas lojas).
- O modelo não tem `payment_method` -- correto para o comportamento real
  hoje (ponto 4/5 da auditoria), mas se Pichau/Terabyte no futuro
  exibirem duas condições simultâneas para a mesma contagem em
  modalidades diferentes, o `UNIQUE` atual rejeitaria a segunda até uma
  nova investigação e decisão de produto.

## Terabyte desativada e simplificada (2026-08-20, `DEC-070`)

**Estado atual: Terabyte temporariamente desabilitada** devido a bloqueio
persistente do Cloudflare Bot Management -- não removida, não apagada do
banco, só `stores.is_active = false` (mecanismo já existente, respeitado
em `orchestration.py` na seleção de fontes elegíveis para nova coleta).
Reversível com um `UPDATE` de volta a `true`; nenhum histórico, missão ou
`mission_sources` foi alterado ou apagado.

**Diagnóstico que motivou a mudança**: investigação dedicada (evidência
real, sem tentativa de evasão) confirmou `server: cloudflare`,
`cf-mitigated: challenge`, cookie `__cf_bm` (Bot Management, não WAF
genérico) em homepage/busca/produto igualmente, com Chrome comum
carregando o site normalmente no mesmo IP onde o Playwright do coletor
recebe 403 -- aponta para característica do navegador automatizado, não
do IP isolado. Volume de requisições à Terabyte cresceu 2-7x entre
17-19/08 (mesma janela em que a TASK-089 passou a abrir até 3 páginas
individuais por busca para o parcelamento detalhado), mas o bloqueio só
começou em 20/08 05:00 -- mais de dois dias depois do pico, então a
correlação de volume é registrada como fator agravante possível, não
causa direta comprovada.

**Mudança de estratégia (V1)**: `TerabyteProvider.resolve_installment_options`
foi removido -- a Terabyte não abre mais página individual de produto só
para capturar a tabela detalhada de parcelamento (1x-18x, faixas de
desconto/juros variáveis). O parcelamento desta loja passa a vir
exclusivamente do que o card da busca já expõe (`.product-item__juros`),
pelo mesmo caminho comum já usado por Amazon/KaBuM! -- no máximo uma
condição por oferta, sempre com `is_highlighted=true` (é a única origem
possível dessa flag). `installment_total_amount`/`discount_percent`
continuam `NULL` quando o card não os declara explicitamente; nunca
calculados. Reduz de até 4 navegações (1 busca + até 3 páginas) para
exatamente 1 por execução.

**Pichau, Amazon e KaBuM! não foram alterados** -- Pichau continua com
enriquecimento individual completo (não apresentou o problema observado
na Terabyte); Amazon/KaBuM! já usavam só o card, sem mudança.

**Nenhuma técnica de evasão foi considerada ou implementada** (sem
stealth, spoof de fingerprint, proxy, rotação de IP, CAPTCHA solver,
cookies humanos ou login) -- fora de escopo por decisão explícita do
usuário.

**V2 (registrado, não implementado)**: investigação detalhada de faixas
de parcelamento da Terabyte (1x-18x, desconto/juros variáveis por
quantidade) fica como evolução futura, condicionada a uma solução para o
bloqueio Cloudflare que não envolva evasão -- ex.: parceria/API oficial
com a loja, decisão de produto fora do escopo técnico desta TASK.

**Limitação conhecida, não corrigida nesta rodada**: a seleção de lojas
na criação de missão (`MISSION_SOURCE_CODES` em `intent/contracts.py`) é
uma lista fixa, independente de `Store.is_active` -- um usuário ainda
consegue selecionar "Terabyte" ao criar uma missão nova, e o
`MissionSource` é criado normalmente, só nunca chega a coletar de fato
(filtrado silenciosamente em `orchestration.py`). Não há aviso ao usuário
nesse fluxo. Registrado como gap de UX, não corrigido por estar fora do
escopo pedido nesta TASK.

## Restrições desta abertura (histórico -- já superadas pela implementação)

Esta abertura não autorizava código, migration, investigação externa,
chamadas reais, alteração de banco, commit, push, rebuild, deploy ou início
da TASK-077 ou da própria TASK-089. TASK-077 foi concluída antes desta
(commit `9de77d6`); esta implementação da TASK-089 foi autorizada
explicitamente pelo usuário em 2026-08-17, após a investigação real ter
corrigido o desenho original (`DEC-069`). Commit, push, tag e deploy
autorizados explicitamente pelo usuário em 2026-08-17, fechando a
release `v1.0.7` -- ver `docs/CHANGELOG.md` para o registro oficial.
