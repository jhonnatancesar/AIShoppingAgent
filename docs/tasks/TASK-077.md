# TASK-077 — Distinguir "vendido pela Amazon" de "loja parceira Amazon"

Status: **Planejada e aprovada** (2026-08-12) — decisões arquiteturais
tomadas pelo usuário (ver "Decisões aprovadas" abaixo). Ainda **não
implementada**; a investigação ao vivo obrigatória (ver seção própria)
precisa acontecer antes do código, e seu resultado pode inclusive exigir
voltar ao usuário se a distinção não for confiável a partir da busca.

Dependência: nenhuma migration/código da TASK-075 é alterado; a regra de
menor preço exclusiva da Amazon continua exatamente como está.

Versão alvo: `v1.0.6`.

## Problema

Hoje o `AmazonProvider` (`app/collection/providers/stores.py`) extrai o
nome do vendedor de um único seletor,
`card.querySelector('[aria-label^="Vendido por"]')?.textContent`, e passa
esse texto bruto direto para `RawCollectedOffer.seller_name` — sem
nenhuma classificação de "é a própria Amazon ou é um terceiro".

Mais grave: **nenhum provider do projeto (incluindo Amazon) jamais
preenche `seller_external_id`** — confirmado por leitura de todo
`providers/stores.py`. Como `_resolve_seller`
(`orchestration.py:786-817`) só cria uma linha em `Seller` quando
`item.seller_external_id` existe, **nenhuma linha de `Seller` é criada
hoje, para nenhuma fonte**. Consequência: `Offer.seller_id` é sempre
`NULL`, então toda oferta da Amazon é persistida pelos índices de
"retailer" (`uq_offers_retailer_external_id`, chave `(store_id,
external_id=ASIN)`), exatamente como Pichau/Terabyte/Kabum — mesmo a
Amazon sendo, de fato, um marketplace. Os índices de "marketplace"
(`uq_offers_marketplace_external_id`, que exigiriam `seller_id NOT
NULL`) já existem no schema mas são código morto na prática.

O sistema hoje não distingue, de forma alguma, se uma oferta da Amazon é
vendida pela própria Amazon ou por um terceiro no marketplace dela.

## Objetivo

Para cada oferta coletada na Amazon, classificar de forma confiável e
barata, com rótulo de apresentação (quando exibido ao usuário):

- **`Amazon`** — vendida pela própria Amazon/Amazon.com.br.
- **`Loja parceira Amazon`** — vendida por terceiro no marketplace da
  Amazon (sem precisar identificar o nome exato do terceiro nesta
  versão).
- **`Vendedor não identificado`** — quando não houver evidência
  suficiente na coleta atual para decidir com segurança.

Nunca inferir "Amazon" só porque a oferta veio de `amazon.com.br`.

## Escopo

- Investigação **ao vivo, antes de implementar** (ver "Requisito
  prévio obrigatório" abaixo) de como a Amazon realmente marca "vendido
  pela própria Amazon" vs. terceiro nos cards de busca — a suposição
  inicial (elemento `[aria-label^="Vendido por"]` sempre presente com o
  nome do vendedor) precisa ser confirmada para o caso em que o vendedor
  É a Amazon: é possível que esse elemento simplesmente não apareça
  nesse caso (ausência = Amazon), ou que apareça com o texto literal
  "Amazon.com.br"/"Amazon" — os dois comportamentos exigem uma regra de
  classificação diferente e **não devem ser assumidos sem checagem real**.
- Nova função pura de classificação (mesmo módulo de providers ou
  `orchestration.py`, a decidir na implementação) que recebe o texto
  bruto já capturado hoje e devolve um dos três valores fechados
  (`amazon` / `marketplace_partner` / `unknown`) — sem abrir página
  individual do produto, sem chamada de IA, sem chamada externa nova
  (usa só o que `AmazonProvider.extract` já lê do card).
- Persistência do resultado da classificação em `PriceObservation`
  (decisão aprovada, ver "Decisões aprovadas" abaixo).
- Testes cobrindo a classificação com casos reais observados na
  investigação ao vivo (não só casos hipotéticos).

## Fora de escopo

- Identificar/persistir o nome exato de vendedores terceiros — só a
  classificação binária (Amazon vs. parceiro) é pedida nesta versão.
- Criar/popular `Seller` (linha em `sellers`), `seller_id` em `Offer`, ou
  ativar os índices de identidade "marketplace" já existentes no schema
  — isso mudaria a identidade/deduplicação de `Offer` para a Amazon
  (uma mesma oferta poderia passar a virar múltiplas linhas por
  vendedor), o que é uma mudança estrutural bem maior do que o pedido
  desta TASK e arriscaria continuidade do histórico de preço já
  coletado. Não incluído aqui; se o produto quiser isso no futuro, é
  outra TASK, com sua própria migração de dados existentes.
- Qualquer mudança na regra de menor preço exclusiva da Amazon
  (`_select_amazon_lowest_price`, TASK-075) — permanece exatamente como
  está. A classificação desta TASK não participa do desempate nem do
  filtro determinístico.
- Mudança de ranking, apresentação ou preferência por "vendido pela
  Amazon" na pré-lista/notificação — mencionada pelo usuário como
  possibilidade futura, não implementada aqui.
- Aplicar essa mesma lógica a outras lojas (Kabum também tem um sinal
  "Vendido por" próprio, `providers/stores.py:116`, já usado só para
  regra de terceiro/entrega Kabum, sem relação com isto) — fora de
  escopo, o pedido é especificamente sobre a Amazon.

## Requisito prévio obrigatório (antes de codificar)

Fazer uma busca real na Amazon (mesmo padrão de diagnóstico isolado já
usado nesta sessão para a Pichau — sem persistir dados, sem criar
missão) e descobrir, com evidência real do DOM/dados carregados pela
página, como os cards atuais representam:

1. vendido pela própria Amazon;
2. vendido por terceiro;
3. ausência de informação de vendedor.

Não supor que ausência do elemento significa Amazon — inspecionar pelo
menos um card **genuinamente vendido pela própria Amazon** e um
**genuinamente vendido por terceiro**, capturando o HTML/texto ao redor
de onde `[aria-label^="Vendido por"]` aparece (ou não aparece) em cada
caso.

Ordem de prioridade na obtenção da evidência:
1. informação já presente no card;
2. dados estruturados já carregados pela página;
3. sem abrir página individual;
4. sem IA;
5. sem chamada externa adicional.

A regra de classificação final só deve ser escrita depois dessa
confirmação — não a partir de suposição sobre como a Amazon costuma
exibir isso.

**Se não for possível distinguir Amazon de terceiro de forma confiável
a partir do resultado de busca, PARAR e apresentar o achado ao usuário
antes de propor ou implementar qualquer estratégia alternativa** (ex.:
abrir página individual) — essa decisão não pode ser tomada
unilateralmente durante a implementação.

## Requisitos funcionais

1. Cada oferta da Amazon coletada recebe uma classificação em
   `{amazon, marketplace_partner, unknown}`.
2. A classificação nunca assume "Amazon" por omissão/ausência de
   evidência — ausência de sinal claro deve resultar em `unknown`, a
   menos que a investigação ao vivo confirme que "elemento ausente" é
   precisamente o sinal legítimo de "vendido pela Amazon" (só nesse caso
   a ausência mapeia para `amazon`, e isso precisa estar documentado com
   a evidência que confirmou).
3. A classificação não depende de abrir a página individual do produto,
   nem de chamada de IA, nem de qualquer chamada de rede além da busca
   já realizada hoje.

## Requisitos técnicos

- Enum interno em inglês/snake_case, seguindo o padrão já usado no
  projeto (`OfferRelevance.MATCH = "match"` etc.), não string em
  português armazenada — os rótulos em português (`"Amazon"`, `"Loja
  parceira Amazon"`, `"Vendedor não identificado"`) ficam só na camada
  de apresentação (Telegram), quando/se essa apresentação for
  implementada.
- **Escopo restrito à Amazon**: a função de classificação só roda para
  `source_code == "amazon"`; outras fontes não são tocadas.
- Reaproveitar exatamente o texto já capturado pelo seletor existente
  (`[aria-label^="Vendido por"]`) como entrada — não adicionar novo
  seletor a menos que a investigação ao vivo (seção acima) mostre que é
  necessário.

## Decisões aprovadas (2026-08-12)

**Onde persistir**: nova coluna nullable em `PriceObservation`, não em
`Offer`/`Product` — porque o vendedor "dono do Buy Box" pode mudar entre
uma coleta e outra para o mesmo anúncio, e `PriceObservation` já é o
retrato de um momento específico da coleta, enquanto `Offer` é a
identidade estável do anúncio. Migration simples, sem backfill — mesmo
padrão de baixo risco de `mission_criteria.model` (TASK-075).

**Nome técnico**: `seller_kind`, seguindo o precedente mais próximo já
existente no schema — `Store.source_type` (`StoreSourceType` enum,
`app/stores/models.py`), uma coluna enum que descreve "que tipo de X é
isto". Nome deliberadamente genérico (não `amazon_seller_kind`): o
conceito — "o vendedor é a própria plataforma ou um terceiro no
marketplace dela" — não é exclusivo da Amazon, mesmo que só a Amazon o
popule nesta versão; um nome genérico evita renomear a coluna se o
projeto quiser generalizar para outra loja marketplace no futuro. Novo
enum `SellerKind` (`AMAZON = "amazon"`, `MARKETPLACE_PARTNER =
"marketplace_partner"`, `UNKNOWN = "unknown"`).

**`NULL` vs. `unknown` — distinção obrigatória**: `NULL` significa "não
avaliado/não aplicável" (toda observação de Pichau/Terabyte/Kabum, que
não passam pela classificação nesta TASK). `unknown` é um valor
persistido explicitamente, só para observações da Amazon onde a
classificação rodou mas não teve evidência suficiente — os dois **não
são a mesma coisa** e o código não deve tratá-los como equivalentes.

**Regra de segurança semântica**: `unknown` nunca deve ser interpretado
como Amazon oficial. Se uma TASK futura vier a usar este campo em
ranking/preferência: `amazon` pode ser considerado vendedor oficial
Amazon; `marketplace_partner` é terceiro; `unknown` permanece
neutro/não confiável — nunca tratado como `amazon` nem como
`marketplace_partner`. Esta TASK não implementa nenhum uso de
ranking/preferência (fora de escopo, explícito) — a regra acima é
registrada agora só para não precisar ser revisitada quando uma TASK
futura for consumir o campo.

## Segurança

- Nenhum dado sensível envolvido — a classificação usa só texto de
  vendedor já publicamente exibido na página de busca da Amazon, que já
  é coletado hoje sem tratamento especial de sigilo.
- Nenhuma chamada nova a serviço externo, nenhum novo dado pessoal
  coletado.

## Critérios de aceite

1. Investigação ao vivo (seção "Requisito prévio obrigatório")
   documentada com evidência real de como a Amazon marca "vendido pela
   Amazon" vs. terceiro, antes de qualquer código escrito — ou, se a
   distinção não for confiável a partir da busca, o achado é apresentado
   ao usuário antes de qualquer implementação alternativa.
2. Observações de preço da Amazon coletadas depois da implementação
   carregam `PriceObservation.seller_kind` em `{amazon,
   marketplace_partner, unknown}`, nunca assumido por padrão; outras
   lojas continuam com `NULL`.
3. `unknown` nunca é tratado, em nenhum lugar do código, como
   equivalente a `amazon`.
4. `_select_amazon_lowest_price` (TASK-075) continua se comportando
   exatamente igual — mesmo teste de regressão da TASK-075 passa sem
   alteração.
5. Nenhuma nova chamada de rede, scraping de página individual ou
   chamada de IA introduzida.
6. Pipeline oficial completo aprovado, incluindo a migration nova
   validada contra PostgreSQL real.

## Testes esperados

- Testes determinísticos da função de classificação cobrindo pelo menos:
  texto explícito "Amazon.com.br"/"Amazon" → `amazon`; texto de
  terceiro → `marketplace_partner`; ausência do elemento → o valor que a
  investigação ao vivo confirmar como correto (documentado); texto vazio
  ou ambíguo → `unknown`.
- Teste confirmando que `PriceObservation.seller_kind` fica `NULL` para
  Pichau/Terabyte/Kabum (não avaliado) e nunca `unknown` nessas fontes —
  a distinção `NULL` vs. `unknown` é semântica, não cosmética.
- Teste de integração confirmando que a regra de menor preço da Amazon
  (TASK-075) não muda de comportamento com o campo novo presente.
- Teste de migration real (mesmo padrão da suíte de integração
  PostgreSQL usada em `20260811_0001`): coluna nova aplicada sem afetar
  `PriceObservation` existentes.

## Impacto em banco/migration

Uma migration nova: coluna `seller_kind` nullable em `PriceObservation`
(enum `SellerKind`, sem valor default, sem backfill) — mesmo padrão de
risco baixo já usado em `20260811_0001` (`mission_criteria.model`).
Nenhuma outra tabela ou coluna afetada.
