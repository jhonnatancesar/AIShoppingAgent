# TASK-097 — Identidade global de produto/variante e resolução de pedidos genéricos

Status: **Concluída, aprovada e publicada em `origin/main` em 2026-08-22
(`eeb2f4a`).**

## Origem e objetivo

Item 7 da V1.2 (`DEC-079`). Corrigir a limitação atual em que cada nova
`Offer` cria seu próprio `Product`, mesmo quando várias lojas anunciam o mesmo
produto e variante. A identidade global será a base obrigatória da futura
TASK-098; esta TASK não implementa histórico nem gráficos.

## Modelo persistente proposto

Evoluir `Product`, hoje já referenciado por `Offer.product_id`, para representar
uma variante global resolvida. Acrescentar campos normalizados para `category`,
`brand`, `family`, `model`, `variant`, `identity_key`, `identity_version` e
atributos extensíveis de identidade. Os atributos são tipados por um registro
de categorias (por exemplo, `storage_gb` para smartphones), serializados de
forma canônica e ordenada; texto livre não participa diretamente da igualdade.

`Offer` continua pertencendo a uma loja/vendedor e passa a apontar para o mesmo
`Product` quando — e somente quando — a chave determinística completa for
idêntica. Não haverá produto global inferido por missão, preço, semelhança de
título ou classificação de relevância.

Uma relação explícita entre missão e variantes selecionadas deverá permitir
zero, uma ou várias escolhas. O estado “todas” precisa ser uma política
deliberada da missão, não a ausência ambígua de linhas de seleção.

`MissionCriteria` persiste também a classificação determinística do pedido:

- `specific_product`: identidade completa; somente a mesma variante pode casar;
- `product_family`: família/modelo conhecidos, mas variante incompleta; oferece
  as variantes encontradas para escolha;
- `generic_category`: categoria ampla sem produto identificável; continua válida
  sem seleção e jamais une produtos distintos sob uma identidade fictícia.

## Chave e equivalência determinísticas

A chave terá versão e representação canônica equivalente a:

```text
v1|category|brand|family|model|variant|attribute_1=value|attribute_2=value
```

- normalização de caixa, acentos, espaços, separadores e unidades é feita por
  funções determinísticas e versionadas;
- cada categoria declara quais atributos distinguem variantes e quais são
  obrigatórios para união entre lojas;
- atributo obrigatório ausente torna a identidade **não resolvida**; nunca é
  wildcard e nunca casa com um valor conhecido;
- `iPhone 17 Pro` e `iPhone 17 Pro Max` têm variantes distintas;
- `iPhone 17 Pro 256 GB` e `iPhone 17 Pro 128/512 GB` têm chaves distintas;
- conflito entre evidências mantém a oferta não resolvida, com diagnóstico
  seguro, em vez de escolher uma identidade por aproximação;
- IA pode interpretar a frase inicial do usuário, mas não calcula a chave, não
  decide equivalência e não escolhe variante.

O registro de categorias deve permitir adicionar novos atributos/normalizadores
sem alterar o núcleo de resolução, as telas ou o fluxo do Telegram.

## Pedidos específicos e genéricos

Pedido `SPECIFIC_PRODUCT` contém os componentes exigidos pela categoria e só
aceita ofertas com a mesma chave completa. Uma variante diferente é `NO_MATCH`,
mesmo que título, família ou missão sejam semelhantes.

Pedido `PRODUCT_FAMILY`, como `iPhone 17`, define apenas o escopo familiar conhecido.
As variantes completas encontradas dentro desse escopo são deduplicadas por
`identity_key`, ordenadas deterministicamente e apresentadas para escolha. A
missão não publica pré-lista nem alertas de ofertas ainda não autorizadas pela
seleção de variantes.

Pedido `GENERIC_CATEGORY`, como `cadeira gamer`, não exige nem bloqueia escolha
de produto/variante. A missão continua válida e pode apresentar refinamentos
opcionais na Web ou uma pergunta opcional no Telegram, mas ausência de resposta
não pausa coleta, pré-lista ou alertas. Produtos diferentes dessa categoria
nunca são consolidados numa identidade global única.

## Fluxo Web

- Após um pedido `PRODUCT_FAMILY` encontrar variantes, a área USER mostra nome canônico
  e atributos diferenciadores de cada uma.
- O usuário escolhe uma, várias ou “todas”; o backend valida ownership e grava a
  política/seleções explicitamente.
- A lista e as ofertas da missão respeitam somente as variantes escolhidas.
- Recarregamento, concorrência e repetição da mesma escolha são idempotentes.
- Para `GENERIC_CATEGORY`, refinamentos são opcionais e não bloqueiam a missão.

## Fluxo Telegram

- Pedido `PRODUCT_FAMILY` recebe lista numerada determinística das variantes encontradas.
- Aceita escolha única, múltipla (`1,3`) ou “todas”, reaproveitando o parser
  numérico existente quando compatível, sem IA na resposta.
- O payload pendente guarda IDs/chaves imutáveis da lista exibida; a posição
  nunca é recalculada silenciosamente entre pergunta e resposta.
- Resposta inválida reapresenta a lista e não altera a missão.
- Para `GENERIC_CATEGORY`, eventual convite para refinar é opcional e nunca cria
  um estado pendente que impeça o uso normal da missão.

## Migração e compatibilidade

- Adicionar a nova estrutura sem unir registros antigos apenas pelo título.
- O migration não tenta unir automaticamente o histórico antigo: critérios
  existentes entram como `generic_category` e produtos antigos ficam sem chave.
  A convergência ocorre de forma gradual quando uma oferta é observada novamente
  e seu título fornece identidade completa e sem conflito.
- Produtos antigos incompletos permanecem `unresolved`/sem `identity_key` e suas
  `Offer` continuam válidas. Não apagar `PriceObservation`, relevância ou
  histórico existente.
- Missões existentes permanecem operacionais. Critérios específicos só ganham
  vínculo automático quando houver correspondência determinística completa;
  critérios genéricos entram no fluxo de escolha antes de novas publicações.
- `MissionCriteria.model` continua sendo entrada da busca e não passa a ser, por
  si só, identidade global.

## Critérios mínimos de aceitação

1. Ofertas equivalentes das quatro lojas convergem para o mesmo `Product`.
2. Pro, Pro Max e capacidades distintas nunca convergem.
3. Atributo obrigatório ausente não une identidades.
4. Pedido específico rejeita variante diferente antes da pré-lista/alerta.
5. `PRODUCT_FAMILY` oferece escolha única, múltipla e todas na Web e Telegram.
6. `GENERIC_CATEGORY` permanece operacional sem seleção e não agrega produtos.
7. Identidade, ordenação e seleção final são determinísticas e independem de IA.
8. Migração preserva dados antigos e mantém casos ambíguos não resolvidos.

## Implementação e validação

- Migration `20260822_0004`, com chave global única parcial e seleção N:N por missão.
- Resolução comum à coleta de todas as lojas; código de loja não participa da chave.
- WebSession/ownership no endpoint de seleção e fluxo numerado determinístico no Telegram.
- `SPECIFIC_PRODUCT` incompatível vira `NO_MATCH` antes da IA; `GENERIC_CATEGORY`
  continua no fluxo normal sem seleção bloqueante.
- PostgreSQL 18.4 descartável aprovou o head; testes focados de domínio, coleta,
  eventos, Web e Telegram, além de Ruff, frontend lint/build e `git diff --check`.

## Fora de escopo

Gráficos, métricas históricas, comparação temporal, reviews, cupons, nova loja,
scraping adicional só para identidade e qualquer implementação da TASK-098.
