# TASK-104A — Store Provider Magalu

Status: **Implementada e validada no DEV; pronta para revisão/commit.**

## Objetivo

Adicionar a Magalu como fonte pesquisável usando o contrato comum de Store
Providers, sem criar fluxo paralelo para Web, Telegram, missões, ranking ou
persistência.

## Regra própria da origem

- `seller_kind=platform` somente com evidência explícita de **vendido por
  Magalu**;
- vendedor explicitamente diferente da Magalu é
  `marketplace_partner`;
- `fulfillment_kind` é extraído separadamente de **entregue por**;
- ausência de evidência permanece `unknown`, nunca é presumida;
- vendedor e entrega devem ser aproveitados da mesma abertura/enriquecimento
  já necessário para a oferta, sem navegação exclusiva adicional.

## Integração comum

Cadastrar a Store/provider no catálogo existente e fazê-la atravessar a mesma
coleta, normalização, identidade global, relevância, pré-lista multi-oferta,
Web e Telegram das lojas atuais. Condição, preço, parcelamento, avaliação e
disponibilidade seguem os contratos compartilhados e só são preenchidos com
evidência real.

**Correção (`DEC-108`, 2026-08-30, auditoria GG Oferta subtask 3):** a frase
acima sobre **condição** ficou desatualizada frente à regra definitiva
aprovada nesta data — não muda o código (o fallback já era este na prática),
só corrige a especificação escrita. A regra vigente para Magalu é a mesma de
Amazon/Mercado Livre: evidência real de usado/seminovo/recondicionado →
condição correspondente; ausência de evidência → `NEW` (nunca `unknown`).
Vendedor/entrega (linha acima, "Regra própria da origem") não mudam — ausência
de evidência de `seller_kind`/`fulfillment_kind` continua `unknown`, nunca
presumida.

A avaliação continua sendo um snapshot da própria `Offer` na Magalu:
`rating_average` e `review_count` somente são preenchidos juntos quando a
origem os publicar; ausência de evidência permanece `NULL`, nunca zero.

## Estratégia de busca implementada

O parser SSR e o transporte são independentes. Nesta versão, **Edge/CDP é o
único transporte operacional da Magalu**. Quando `AISHOPPING_MAGALU_CDP_URL`
está configurada, o worker inicia e supervisiona automaticamente um Edge
dedicado, com perfil próprio e CDP restrito a loopback. O provider conecta,
obtém o HTML final e entrega o documento ao parser `#__NEXT_DATA__`. Não há
fallback HTTP nem uma segunda navegação Playwright da Magalu.

Conexão CDP, navegação, espera pelo documento SSR e leitura do HTML possuem
timeouts curtos e independentes. Falha ou timeout sobe como erro próprio não
transitório: não entra no retry de navegação comum e a orquestração isola a
Magalu das outras lojas. O supervisor monitora apenas o CDP local e recupera o
Edge se o processo dedicado morrer; ele não faz requisições à loja.

A auditoria da carga inicial não encontrou API pública separada de catálogo. O
endpoint Next derivável do build também respondeu 403 no runtime do backend e
não é assumido como contrato. A busca básica não depende de `enrich()`; falha
ao abrir detalhe conserva todas as ofertas já coletadas.

## Segurança e portabilidade

- CDP aceita somente `http` em `127.0.0.1`, `localhost` ou `::1`, com porta
  explícita; endpoint público/remoto falha na configuração;
- Edge usa perfil dedicado e permanece vivo entre coletas; somente o supervisor
  controla seu ciclo de vida;
- frontend, domínio, parser, ranking e consumidores não conhecem Edge/CDP;
- o adapter atual pode ser substituído por outro transporte no futuro runtime
  Windows sem reconstruir o provider;
- não há CAPTCHA, stealth, spoof de fingerprint, proxy ou bypass.

## Validação real

Edge 151 normal com perfil descartável e CDP em loopback retornou HTTP 200 para
`Samsung Galaxy S24 Ultra`. O `#__NEXT_DATA__` tinha 39 itens; o provider
admitiu 20 pelo limite comum e o validador real retornou 3/3 ofertas pelo seu
limite local, incluindo usadas explícitas, vendedores parceiros, condição,
disponibilidade, preço, URL, imagem, parcelamento e avaliação. O perfil foi
removido após o teste.

O smoke final de supervisão iniciou o Edge ausente, encerrou os 8 processos do
perfil dedicado, recuperou automaticamente com novo PID e uma única busca após
a recuperação retornou 20 ofertas.

PostgreSQL 18.4 descartável aprovou upgrade, downgrade/upgrade, `alembic check`
e os três testes de schema no head `20260822_0007`.

## Validação mínima concluída

- card/oferta vendida pela Magalu;
- card/oferta de parceiro;
- vendedor ou entrega ausente preserva `unknown`;
- múltiplas ofertas chegam ao ranking global;
- busca selecionada funciona na Web e no Telegram sem código consumidor
  exclusivo.

## Fora de escopo

Cupons, autenticação comercial, histórico/gráficos, IA nova, compra e outras
lojas da TASK-104.
