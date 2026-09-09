# TASK-121 — Ligar a aba "Cupons" a dados reais (listagem geral, não só aplicabilidade)

Status: **Planejada, não iniciada.** Registrada em 2026-09-09 a partir de
achado real do pós-deploy `v1.3.9` (investigação de "por que cupons não
aparecem no site").

## Problema

`frontend/src/pages/CouponsPage.tsx` existe e está na navegação
autenticada (`aba Cupons`), mas renderiza só 6 cards de **modelo**
(`COUPON_TEMPLATES`, hardcoded), um por loja, com texto fixo "Espaço
pronto para o próximo cupom". O próprio comentário no arquivo já
documentava a lacuna: *"Subtask 11: só a área 'Cupons' dentro do shell
autenticado -- sem backend, sem integração com o Coupon Collector
(repositório separado)"* — mesma numeração usada em
`SUBTASK-010-landing-publica-ggoferta.md` ("não iniciar a Subtask 11 a
partir desta subtask"), nunca formalizada como TASK própria até agora.

Não existe hoje nenhum endpoint HTTP que liste cupons ativos de forma
geral. O único caminho de leitura existente,
`app.coupons.service.get_active_coupons_by_store`, é interno ao cálculo
de `applied_coupon` em `list_offers` (`DEC-129`, `v1.3.9`) — só retorna
cupom quando ele bate com uma `Offer` específica já rastreada por
alguma missão do usuário (`is_coupon_applicable`, `pricing.py`). Isso é
correto para o badge "🏷️ Cupom X" dentro do card de oferta (mostra
economia real numa oferta que o usuário já acompanha), mas **não é o
mesmo objetivo de uma aba "Cupons"**, que existe para listar os cupons
que o Coupon Worker coletou, independente de o usuário já rastrear
aquele produto específico — hoje, na prática, quase nunca há
sobreposição entre os dois universos (auditado ao vivo em PROD em
2026-09-08: 0/56 cupons ativos com `scope_kind='product'` batiam com
alguma `Offer` existente).

## Objetivo

1. **Backend:** endpoint novo (ex. `GET /api/coupons`, novo
   `app/coupons/coupons_router.py`, registrado como os demais routers
   autenticados) que lista cupons `status='active'`, com os dados já
   existentes no modelo `Coupon` (loja, código, `raw_rule_text`/regra,
   `valid_until`, `scope_kind`/`scope_reference` quando fizer sentido
   exibir) — nunca inventar título/desconto que não esteja no dado
   coletado. Nova função de leitura em `app/coupons/service.py`
   (distinta de `get_active_coupons_by_store`, que continua exclusiva
   do caminho de aplicabilidade — não reaproveitar/alterar essa função
   para este uso, são consultas com propósito diferente).
2. **Frontend:** `CouponsPage.tsx` busca o endpoint novo (novo
   `frontend/src/api/coupons.ts`, seguindo o padrão de `offers.ts`) e
   renderiza via `CouponCollection`/`CouponCardData` já existentes
   (`components/CouponCard.tsx`) — sem criar um componente visual
   paralelo. Estado vazio honesto quando não houver cupom ativo (o
   componente já foi desenhado para isso). Remove `COUPON_TEMPLATES`.

## Escopo

Só a listagem geral de cupons ativos coletados. Não mexe no cálculo de
`applied_coupon`/badge de oferta (`DEC-129`), não mexe no Coupon Worker,
não mexe em Telegram, não mexe em `HIGH_ACTIVITY`/Job Object (`DEC-130`/
`DEC-131`).

## Fora de escopo

Filtro/busca por loja ou por produto na aba Cupons (pode ser uma TASK
futura separada, se pedido); paginação (avaliar se o volume real de
cupons ativos justifica, no momento da execução); qualquer nova regra de
aplicabilidade cupom↔oferta (`is_coupon_applicable` não muda); qualquer
alteração em `DEC-093` (cupom continua subsistema independente da
coleta).

## Critério de validação futuro

Endpoint com teste de integração (PostgreSQL real) cobrindo: cupons
ativos aparecem, cupons expirados/inativos não aparecem, nenhum dado
inventado quando um campo opcional está ausente. Validação visual da
aba Cupons no dev server mostrando cupons reais (não mais os 6
templates) quando houver dado no banco, e estado vazio honesto quando
não houver.
