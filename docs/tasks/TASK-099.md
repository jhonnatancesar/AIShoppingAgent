# TASK-099 — Pesquisa de produtos pela Web

Status: **Implementada no DEV, aguardando revisão.**

## Objetivo

Permitir que um USER autenticado pesquise produtos em `/app/search`, escolha
uma ou mais lojas e acompanhe os resultados na própria aplicação Web.

## Decisão arquitetural

A pesquisa é read-only sobre `Product`, `Offer` e a última
`PriceObservation` já persistidos. Ela nunca cria `Mission`, `CollectionRun`,
observação ou nova navegação de loja. A classificação determinística da
TASK-097 define se o pedido é `SPECIFIC_PRODUCT`, `PRODUCT_FAMILY` ou
`GENERIC_CATEGORY` e impede união arriscada.

Somente o botão **Monitorar** chama o endpoint existente
`POST /api/v1/missions`. Para famílias, a escolha inicial de uma, várias ou
todas as variantes é validada pelo mesmo service da TASK-097 contra produtos
reais da família e das lojas escolhidas.

## Escopo implementado

- rota autenticada `/app/search`;
- busca textual com seleção das quatro lojas atuais;
- consulta read-only de até cinco ofertas conhecidas por loja;
- criação de missão exclusivamente após a ação explícita “Monitorar”;
- fluxo de família com escolha determinística de uma, várias ou todas as
  variantes, reutilizando a TASK-097;
- cards de ofertas correspondentes com imagem, loja, preço, total, condição e nota
  somente quando os dados reais existirem;
- link original da oferta para consulta opcional na loja;
- loading, vazio, erro e layout responsivo no design system Web comum.

## Segurança e limites

- pesquisa exige `WebSession`; somente “Monitorar” é mutável e exige CSRF;
- a pesquisa expõe somente o schema público mínimo definido para o resultado;
- posse de missão e detalhe USER da Offer permanecem protegidos como antes;
- o backend de pesquisa não chama Store Provider nem IA;
- nenhuma migration, tabela, coleta exclusiva, comparação ou gráfico;
- a listagem geral de ofertas continua reservada ao item 9 da V1.2.

## Validação mínima

- teste focado do contrato de rota/fluxo da pesquisa;
- frontend lint;
- frontend build;
- `git diff --check`.
