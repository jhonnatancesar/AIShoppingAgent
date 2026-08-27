# Fontes e marketplaces

## Fontes selecionáveis na V1

| Código | Nome | Tipo |
| --- | --- | --- |
| `pichau` | Pichau | `retailer` |
| `terabyte` | Terabyte | `retailer` |
| `amazon` | Amazon | `marketplace` |
| `kabum` | Kabum | `retailer` |
| `magalu` | Magalu | `marketplace` |
| `mercadolivre` | Mercado Livre | `marketplace` |

Uma missão seleciona uma ou mais dessas fontes por `mission_sources`. Amazon,
Magalu e Mercado Livre incluem vendedores terceiros: `Seller` identifica cada
vendedor e `Offer` mantém identidade separada por marketplace e vendedor.

***Futuro***

- Shopee (`shopee`);
- AliExpress (`aliexpress`).

As fontes futuras podem aparecer desabilitadas no bot, mas não possuem Store
Provider nem podem ser persistidas como seleção na V1.

Frete e fulfillment variam no tempo e serão registrados com cada observação de
preço, não na identidade estável da oferta.

A TASK-077 classifica historicamente vendedor e entrega em Amazon e KaBuM! como
`platform`, `marketplace_partner` ou `unknown`, a partir da página individual
de poucos candidatos finais. Cards de busca reais não forneceram evidência
confiável. `NULL` significa não avaliado; ausência/ambiguidade numa página
válida significa `unknown`. A classificação não popula `Seller`, não altera
identidade, preço, relevância ou ranking e nunca assume loja oficial por
omissão.

Pichau, Terabyte, Amazon e Kabum foram implementados na TASK-055; Magalu
(TASK-104A) e Mercado Livre (TASK-104B) vieram depois, na V1.2. O projeto não
tenta ocultar automação nem contornar CAPTCHA/proteções.

Desde a TASK-109, o `collection_worker` roda como processo nativo Windows e
navega através de um Microsoft Edge real via CDP
(`EdgeCdpSupervisor`/`Playwright.chromium.connect_over_cdp()`) — nunca mais
lança Chromium gerenciado (headed ou headless) nem depende de Xvfb/Docker para
navegação em nenhum provider, incluindo Pichau e Terabyte. Ver
[Runtime Windows do collection_worker](windows-collection-worker.md) e
[Playwright: sempre `connect_over_cdp()`, nunca `.launch()`](playwright.md)
para a arquitetura de transporte completa. A validação real usa
`python -m scripts.validate_store_providers <fonte>`.
