# TASK-104B — Store Provider Mercado Livre

Status: **Implementada no DEV; aguardando a única validação real final via Edge/CDP.**

## Objetivo

Adicionar o Mercado Livre como fonte pesquisável usando o contrato comum de
Store Providers, sem reconstruir os consumidores da coleta.

## Regra própria da origem

- `seller_kind=platform` somente quando a página declarar explicitamente que
  o produto é **vendido pelo Mercado Livre**;
- vendedor identificado diferente da plataforma é `marketplace_partner`;
- `fulfillment_kind` é independente e só reflete entrega/fulfillment explícito
  do Mercado Livre ou do parceiro;
- selo de loja oficial, reputação ou modalidade logística não transformam um
  parceiro em vendedor da plataforma;
- ausência de evidência permanece `unknown`;
- a extração reutiliza a abertura/enriquecimento já necessária para a oferta,
  sem navegação extra exclusiva para vendedor, entrega ou avaliação.

## Integração comum

Cadastrar a Store/provider no catálogo existente e reutilizar coleta,
normalização, identidade global, relevância, ranking, pré-lista, Web e Telegram.
Código específico do Mercado Livre fica restrito à extração e ao mapeamento de
evidências da origem.

A avaliação deve permanecer vinculada à `Offer`/origem Mercado Livre, sem
agregação global entre lojas. Nota e quantidade só são persistidas com dado
real; ausência permanece `NULL`, nunca zero.

## Transporte operacional

- Playwright normal e headed é o transporte primário da busca;
- Edge normal via CDP loopback é fallback estritamente final, acionado uma
  única vez somente depois de bloqueio ou falha de navegação do primário;
- o fallback reutiliza exatamente `extract()` e não contém regra comercial,
  parser, ranking ou retry próprio;
- o Edge dedicado já supervisionado pela coleta é reaproveitado sem nova
  instância, porta pública ou encerramento por coleta;
- falha dos dois transportes encerra somente a claim Mercado Livre e não
  afeta as demais origens.

## Implementação

- `MercadoLivreProvider` registrado no catálogo comum e em todos os fluxos de
  seleção Web/Telegram;
- cards patrocinados/redirecionados sem identidade canônica de oferta são
  ignorados;
- condição nova é presumida somente na ausência de evidência explícita de
  usado/seminovo/recondicionado, conforme a regra comercial aprovada;
- seller próprio exige o nome explícito Mercado Livre; selo de loja oficial
  não altera `seller_kind`;
- condição, seller, fulfillment, disponibilidade e avaliação de detalhe usam
  uma mesma abertura, limitada pelo enriquecimento comum.

## Validação

- oferta explicitamente vendida pela plataforma;
- oferta vendida por parceiro;
- separação entre vendedor e entrega;
- ausência preserva `unknown`;
- múltiplas ofertas sobrevivem pelo fluxo global na Web e no Telegram.

Os cenários offline e de integração focada estão cobertos. A validação externa
será uma única abertura final pelo fallback Edge/CDP, reunindo todos os campos
necessários para evitar chamadas repetidas à origem.

## Fora de escopo

Cupons, anúncios patrocinados sem oferta válida, autenticação, compra,
histórico/gráficos e demais lojas.
