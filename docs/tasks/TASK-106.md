# TASK-106 — Pesquisa de cupons

Status: **Auditoria real concluída nas quatro lojas (Amazon, Kabum, Magalu, Mercado Livre). Nenhum código de provider escrito ainda.**

## Objetivo

Apresentar cupons reais (código e/ou desconto promocional) junto às ofertas e
lojas já existentes, sem nunca inventar ou presumir cupom sem evidência.

## Decisão de escopo (preflight, 2026-08-22)

- **Fonte principal:** site oficial de cada loja já integrada (Pichau,
  Terabyte, Amazon, Kabum, Magalu, Mercado Livre) — mesma arquitetura comum
  de Store Providers já usada para oferta, vendedor, condição e avaliação.
  Nenhuma dependência de Firecrawl nem de agregador de terceiro nesta
  versão.
- **Prioridade de evidência** (a mais forte vence; ausência de qualquer uma
  delas não gera cupom):
  1. cupom explícito visível no card ou na página do produto/oferta
     (banner, selo, texto "use o cupom X");
  2. regra promocional oficial publicada pela própria loja (ex.: página de
     cupons/promoções do próprio site, sem ser produto específico);
  3. sem evidência em nenhuma das duas formas acima → nenhum cupom é
     apresentado. Nunca há fallback para "provável" ou "geralmente".
- **Papel da IA:** pode interpretar/estruturar texto já encontrado na
  evidência (ex.: extrair código e percentual de um banner com texto livre).
  **Nunca** inventa, completa ou presume código/desconto ausente da
  evidência. Consistente com o único critério já registrado em `DEC-088`.
- **Firecrawl:** não é dependência principal agora. Fica registrado como
  possível caminho futuro (grounding, já existe cliente ocioso em
  `app/search/firecrawl.py`), não descartado, apenas fora desta versão.
- **Agregadores de cupons de terceiro** (ex.: Cuponomia, Pelando): adiados
  para V2/fallback futuro — fora desta TASK.

## Escopo de lojas para a auditoria (2026-08-22)

Por conhecimento prévio do usuário sobre o comportamento real de cada loja
(a confirmar pela auditoria, não presumido como evidência final):
Pichau raramente expõe cupom e a Terabyte só expõe promoção (não cupom) —
as duas ficam **fora da auditoria inicial**. Amazon, Kabum, Magalu e
Mercado Livre entram primeiro, por relato de cupom real existente nelas.
Pichau/Terabyte podem ser revisitadas depois, sem bloquear o restante.

## Resultado da auditoria real (2026-08-22, Edge/CDP, mesmo transporte já validado)

| Loja | Cupom real encontrado? | Onde | Evidência literal |
|---|---|---|---|
| Amazon | Sim | Card de busca | `"Cupom de R$ 20,00 de desconto aplicado"` / `"Cupom de 5% de desconto aplicado"` — sem código alfanumérico, aplicado automaticamente (clip coupon) |
| Kabum | Sim | Card de busca, mesmo seletor que o `KabumProvider` já lê (`a[href*="/produto/"]`) | `"SELO: CUPOM GAMER10"` — código real (`GAMER10`), precisa ser aplicado pelo comprador |
| Magalu | Sim | **Home**, não na busca — a busca variou entre 0 e 6 itens com cupom em execuções diferentes (parece campanha rotativa/instável, não um campo fixo) | `"Cupom R$ 100 OFF"` nos cards de "Ofertas Relâmpago" da home |
| Mercado Livre | Sim | Home | `"15% OFF com Cupom"` em card da home |

**Consequência prática:** nenhuma das quatro lojas expõe cupom de forma
100% estável/previsível no mesmo lugar — Amazon e Kabum mostraram no card
de busca (evidência mais fácil de reaproveitar, mesma abertura já usada
pela oferta), Magalu e Mercado Livre mostraram na **home**, não na busca
por termo. Isso muda a expectativa original: capturar cupom só durante a
busca normal por produto não cobre Magalu/ML — seria necessário também
auditar/coletar a home dessas duas lojas separadamente, o que é uma
navegação adicional fora do padrão "mesma abertura da oferta" já usado
para vendedor/condição/avaliação.

## Pontos que dependem de auditoria real (ainda não decididos)

Mesmo princípio já usado para a oficialidade da Shopee (TASK-104C) e o
transporte da Terabyte (TASK-105): **nenhum desenho de dado é fixado antes
de evidência real**. Antes de qualquer código:

- auditar, loja a loja, se e como cada uma expõe cupom hoje (card de busca,
  página de produto, página de promoções própria, ou nenhuma das três);
- só depois decidir o vínculo do cupom — por `Offer` específica, por
  `Store` inteira (promocional geral), ou os dois tipos coexistindo — e a
  menor extensão de modelo necessária (nova tabela vs. reaproveitar
  `Offer`/`Store`), com migration apenas se confirmada;
- decidir o gatilho de coleta só depois da auditoria confirmar onde a
  evidência aparece; a expectativa (não confirmada) é reaproveitar a mesma
  abertura/enriquecimento já usada para vendedor/condição/avaliação, sem
  navegação exclusiva nova — mas cupom de página promocional separada da
  loja pode exigir uma abertura própria, a confirmar por loja.

## Integração comum (esperada, sujeita à auditoria)

Cadastro e apresentação reaproveitam o catálogo de Stores e o contrato de
Offer já existentes — Web e Telegram, sem fluxo paralelo. Nenhuma tabela
duplicada de "anúncio" só para cupom.

## Validação mínima futura

- pelo menos uma loja com cupom explícito real encontrado e apresentado
  com evidência (código/fonte visível);
- ausência de evidência não apresenta cupom nenhum (nunca "provável");
- IA interpretando texto real nunca produz código/desconto que a evidência
  não contém;
- falha ao buscar cupom numa loja não deve derrubar oferta nem outras
  lojas.

## Fora de escopo

Firecrawl como fonte principal, agregadores de cupons de terceiro,
aplicação automática de cupom em carrinho/compra, cupons exclusivos de
conta/login, histórico de cupons, AliExpress e demais lojas fora da V1.2.
