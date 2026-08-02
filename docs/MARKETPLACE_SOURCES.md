# Fontes e marketplaces

## Fontes selecionáveis na V1

| Código | Nome | Tipo |
| --- | --- | --- |
| `pichau` | Pichau | `retailer` |
| `terabyte` | Terabyte | `retailer` |
| `amazon` | Amazon | `marketplace` |
| `kabum` | Kabum | `retailer` |

Uma missão seleciona uma ou mais dessas fontes por `mission_sources`. A Amazon
inclui vendedores terceiros: `Seller` identifica cada vendedor e `Offer` mantém
identidade separada por marketplace e vendedor.

***Futuro***

- Mercado Livre (`mercado_livre`);
- Shopee (`shopee`);
- AliExpress (`aliexpress`).

As fontes futuras podem aparecer desabilitadas no bot, mas não possuem Store
Provider nem podem ser persistidas como seleção na V1.

Frete e fulfillment variam no tempo e serão registrados com cada observação de
preço, não na identidade estável da oferta.

Os quatro providers foram implementados na TASK-055. Pichau e Terabyte podem
exigir Chromium headed (com Xvfb no Ubuntu Server) por bloquearem execução
headless. O projeto não tenta ocultar automação nem contornar CAPTCHA/proteções.
