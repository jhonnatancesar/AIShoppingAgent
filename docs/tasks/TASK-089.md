# TASK-089 — Suporte a preço à vista e parcelado

Status: **Planejada e não iniciada (2026-08-16).**

## Classificação

**Nova TASK do MVP** (`DEC-068`). O parcelamento público exibido pelas lojas é
informação adicional da oferta e não se confunde com a consulta autenticada de
frete/parcelamento prevista para ADMIN/DEV na V1.2 (`DEC-045`).

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

## Restrições desta abertura

Esta abertura não autoriza código, migration, investigação externa, chamadas
reais, alteração de banco, commit, push, rebuild, deploy ou início da TASK-077
ou da própria TASK-089.
