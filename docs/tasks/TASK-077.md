# TASK-077 — Classificar vendedor e entrega em Amazon e KaBuM!

Status: **Concluída** (2026-08-16)

Versão alvo: `v1.0.6`.

## Objetivo

Persistir em cada nova `PriceObservation` da Amazon e da KaBuM! uma
classificação independente do vendedor e do responsável pela entrega, e
apresentá-la nas mensagens de oferta. Nunca inferir que a loja é responsável
apenas pelo domínio do anúncio ou pela ausência de informação.

## Evidência real aprovada

A investigação foi feita em ambiente isolado, sem banco, missão, secrets ou
persistência e com volume deliberadamente baixo.

### Cards de busca

- Amazon: duas buscas, 24 cards em cada. O seletor histórico
  `[aria-label^="Vendido por"]` foi `null` em todos; nenhum card forneceu
  vendedor e entrega individualmente verificáveis.
- KaBuM!: 24 cards com o facet `kabum_product=true`; os cards não forneceram
  evidência individual verificável. O facet restringe a consulta, mas não
  substitui evidência da oferta.

### Página individual, aprovada pelo usuário após o gate

- Amazon própria, ASIN `B0DKFMSMYK`: o bloco
  `offer-display-feature-name="desktop-merchant-info"`, com rótulo combinado
  `Enviado / Vendido`, retornou `Amazon.com.br`.
- Amazon parceira, ASIN `B0DQ86KCP2`: o mesmo bloco retornou
  `GX Group ⭐⭐⭐⭐⭐`; `#sellerProfileTriggerId` estava presente.
- KaBuM!, produto `662405`: o texto visível retornou
  `Vendido e entregue por: KaBuM!`.
- `#shipsFromSoldBy_feature_div` estava vazio nos dois exemplos Amazon.

Conclusão: a classificação confiável exige a página individual. A coleta deve
consultar somente candidatos finais, sequencialmente, sem retry, no máximo três
por fonte; 401/403/429 interrompe o enriquecimento para não insistir contra
proteção anti-bot. Nenhuma nova investigação ao vivo é necessária.

## Decisões aprovadas

- Enum genérico `MarketplacePartyKind`: `platform`, `marketplace_partner` e
  `unknown`.
- Duas colunas nullable em `PriceObservation`: `seller_kind` e
  `fulfillment_kind`.
- `NULL` significa não avaliado/não aplicável; `unknown` significa página
  individual válida consultada, mas evidência ausente ou ambígua.
- A classificação é histórica: vendedor/entrega podem mudar entre coletas da
  mesma oferta.
- Amazon e KaBuM! usam hoje blocos combinados; os dois campos recebem a mesma
  classificação quando a evidência é combinada.
- Pichau e Terabyte permanecem com `NULL`.
- O enriquecimento ocorre fora de transação, depois do filtro e ranking
  determinísticos e antes da persistência.
- Falha do enriquecimento não apaga a oferta nem inventa classificação.
- `_select_amazon_lowest_price`, identidade/deduplicação de `Offer` e `Seller`
  permanecem inalterados.

## Apresentação

- plataforma Amazon: `📦 Vendido e entregue por: Amazon.com.br`;
- parceiro Amazon: `📦 Vendido e entregue por: Loja parceira Amazon`;
- plataforma KaBuM!: `📦 Vendido e entregue por: KaBuM!`;
- parceiro KaBuM!: `📦 Vendido e entregue por: Loja parceira Kabum`;
- inconclusiva: `📦 Vendedor e entrega não identificados`;
- não avaliado (`NULL`): a linha é omitida.

A informação entra em alertas de queda/alvo, pré-lista e atualização da
pré-lista. Não altera preço, ranking, relevância ou elegibilidade.

## Critérios de aceite

1. Amazon própria, parceira e ausência de evidência viram, respectivamente,
   `platform`, `marketplace_partner` e `unknown`.
2. KaBuM! própria, eventual parceira e ausência seguem a mesma semântica.
3. Somente candidatos finais são consultados, em sequência, limite três, sem
   retry; bloqueio encerra as consultas restantes.
4. `PriceObservation` preserva as duas classificações; histórico e fontes não
   avaliadas permanecem `NULL`.
5. Constraints aceitam somente os três valores aprovados e preservam `NULL`.
6. Mensagens carregam a observação do próprio evento e falham de forma
   controlada se `observation_id` não pertencer à oferta.
7. Menor preço Amazon, ranking, relevância, histórico e identidade não mudam.
8. Migration aceita upgrade, downgrade seguro e novo upgrade em PostgreSQL
   18.4 descartável; `alembic check` permanece limpo.
9. Testes unitários, integração e regressão passam sem novas chamadas reais às
   lojas durante a validação automatizada.

## Fora de escopo

- Identificar ou persistir o nome exato de parceiro.
- Criar/popular `Seller` ou alterar `Offer.seller_id` e seus índices.
- Preferir, ranquear ou filtrar ofertas por vendedor/entrega.
- Preço à vista e parcelado, reservado à TASK-089.
- Novas lojas, IA, métricas ou chamadas paralelas de detalhe.

## Banco e compatibilidade

Migration aditiva, sem default e sem backfill. Bancos existentes preservam
integralmente as observações; as novas colunas ficam `NULL` no histórico. O
downgrade remove apenas as duas constraints e colunas novas, sem tocar preços,
ofertas, missões ou demais dados.
