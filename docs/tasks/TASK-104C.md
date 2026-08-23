# TASK-104C — Store Provider Shopee

Status: **Adiada — bloqueio anti-bot confirmado mesmo autenticado (`DEC-092`). Nenhum código de provider foi escrito.**

## Diagnóstico do bloqueio (2026-08-22)

Antes de qualquer código de provider, a busca real da Shopee foi testada sem
técnica de evasão, em três configurações: (1) busca pública sem login —
bloqueada, API `search_items` respondeu `error: 90309999`; (2) Edge/CDP
normal sem login (mesmo padrão validado para Magalu/Terabyte) — mesmo
bloqueio; (3) Edge/CDP com perfil persistente e login real via Google —
login funcionou e a sessão **persistiu de fato** entre fechar/reabrir o
Edge, mas tanto a busca quanto uma repetição isolada foram redirecionadas
para `shopee.com.br/verify/captcha?...scene=crawler_item...` (CAPTCHA de
arrastar peça). Diferente da Terabyte — cujo bloqueio era específico do
Chromium gerenciado pelo Playwright e foi resolvido trocando para Edge/CDP
(`DEC-070`/TASK-105) — a Shopee bloqueia mesmo com Edge normal via CDP e
sessão autenticada. Não há, hoje, um transporte já validado no projeto capaz
de contornar isso sem stealth, CAPTCHA solver, spoof de fingerprint ou
proxy — nenhuma dessas técnicas foi tentada. Ver `DEC-092`.

**Consequência:** TASK-104C fica formalmente adiada até haver uma abordagem
sem evasão (ex.: API oficial/parceria, ou mudança futura do comportamento da
própria Shopee). Tudo abaixo permanece como especificação para quando isso
acontecer — nada foi implementado.

## Objetivo

Adicionar a Shopee como fonte pesquisável usando o contrato comum de Store
Providers, preservando a distinção entre vendedor oficial e não oficial.

## Regra própria da origem

- ofertas pertencem ao vendedor identificado na Shopee; não presumir
  `seller_kind=platform`;
- selo explícito de loja oficial deve ser persistido como atributo próprio do
  `Seller`, separado de `seller_kind` e de `fulfillment_kind`;
- o estado de oficialidade é tri-state: `true`, `false` somente quando a origem
  comprovar, e `NULL/unknown` quando não houver evidência suficiente;
- selo oficial não significa **vendido pela Shopee** nem **entregue pela
  Shopee**;
- vendedor, selo, entrega e avaliação reutilizam a mesma abertura/enriquecimento
  da oferta; nenhuma navegação exclusiva adicional;
- antes da implementação, auditar e adotar a menor extensão comum do domínio
  `Seller` necessária para oficialidade, com migration apenas se confirmada.

## Integração comum

Cadastrar a Store/provider no catálogo existente e reutilizar coleta,
normalização, identidade global, relevância, ranking, pré-lista, Web e Telegram.
Somente a extração do selo e dos campos próprios da página fica no provider da
Shopee; consumidores usam o contrato comum.

A avaliação deve permanecer vinculada à `Offer`/vendedor na Shopee, sem
agregação global entre lojas. Nota e quantidade só são persistidas com dado
real; ausência permanece `NULL`, nunca zero.

## Validação mínima futura

- vendedor com selo oficial explícito;
- vendedor sem selo com evidência suficiente;
- estado desconhecido quando a página não comprovar;
- selo não altera indevidamente `seller_kind`/entrega;
- múltiplas ofertas sobrevivem pelo fluxo global na Web e no Telegram.

## Fora de escopo

Shopee Live, ofertas em lives, autenticação, compra, cupons,
histórico/gráficos e demais lojas.
