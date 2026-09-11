import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { CouponsPage } = await server.ssrLoadModule('/src/pages/CouponsPage.tsx')
  const { CouponCollection } = await server.ssrLoadModule('/src/components/CouponCard.tsx')
  const {
    couponTitle, couponScopeLabel, couponMinimumPurchaseLabel, couponActionUrl,
    couponToCardData, groupCouponsByStore, resolveCouponsPageState,
  } = await server.ssrLoadModule('/src/pages/couponCardMapping.ts')

  // Intl.NumberFormat('pt-BR', {style:'currency', currency:'BRL'}) usa
  // espaço FIXO (U+00A0) entre "R$" e o número, não espaço comum.
  const NBSP = String.fromCharCode(160)

  function coupon(overrides = {}) {
    return {
      id: 'c-real-1',
      store: { code: 'amazon', name: 'Amazon' },
      code: 'GAMER10',
      discount_kind: null,
      discount_value: null,
      minimum_purchase_amount: null,
      raw_rule_text: null,
      valid_until: null,
      scope_kind: null,
      source_url: null,
      last_seen_at: '2026-09-10T12:00:00Z',
      ...overrides,
    }
  }

  // -- TASK-121: sem <useEffect> executado (SSR puro), a página SEMPRE
  // renderiza o estado inicial -- prova honesta de "loading".
  const initialHtml = renderToStaticMarkup(React.createElement(CouponsPage))
  assert.match(initialHtml, /Cupons/, 'título da seção deve aparecer')
  assert.match(initialHtml, /Carregando cupons/i, 'estado inicial é o de carregamento')
  assert.doesNotMatch(initialHtml, /modelos de apresentação/i, 'templates hardcoded (Subtask 11) não existem mais')
  assert.doesNotMatch(initialHtml, /DESCONTO10/i, 'nunca um código de cupom fictício')

  // -- resolveCouponsPageState: loading/error/empty/success puros.
  assert.equal(resolveCouponsPageState(null, null).kind, 'loading')
  const errorState = resolveCouponsPageState(null, 'Falha ao carregar')
  assert.equal(errorState.kind, 'error')
  assert.equal(errorState.message, 'Falha ao carregar')
  assert.equal(resolveCouponsPageState([], null).kind, 'empty')
  const successState = resolveCouponsPageState([coupon()], null)
  assert.equal(successState.kind, 'success')
  assert.equal(successState.total, 1)
  assert.equal(successState.groups.length, 1)

  // -- key baseada em `Coupon.id` (nunca um índice, nunca um valor
  // inventado) -- é o mesmo `id` que `CouponCollection` já usa como key.
  const card = couponToCardData(coupon())
  assert.equal(card.id, 'c-real-1')

  // ---------------------------------------------------------------------
  // Título: só 2 prioridades (revisão 2026-09-10) -- raw_rule_text NUNCA
  // vira título, mesmo como fallback.
  // ---------------------------------------------------------------------
  assert.equal(
    couponTitle(coupon({ discount_kind: 'fixed_amount', discount_value: '100.00' })),
    `R$${NBSP}100,00 OFF`,
  )
  assert.equal(couponTitle(coupon({ discount_kind: 'percentage', discount_value: '10.00' })), '10% OFF')
  assert.equal(couponTitle(coupon({ discount_kind: 'percentage', discount_value: '12.50' })), '12,5% OFF')
  assert.equal(
    couponTitle(coupon({ discount_kind: null, discount_value: null, raw_rule_text: 'Você paga R$ 26,91 com o cupom' })),
    'Cupom disponível',
    'raw_rule_text (preço final) NUNCA vira título -- só o rótulo neutro',
  )
  assert.equal(couponTitle(coupon({ discount_kind: null, discount_value: null, raw_rule_text: null })), 'Cupom disponível')

  // -- raw_rule_text vai pra descrição, nunca pro título.
  const cardWithRawText = couponToCardData(
    coupon({ discount_kind: null, discount_value: null, raw_rule_text: 'Você paga R$ 26,91 com o cupom' }),
  )
  assert.equal(cardWithRawText.title, 'Cupom disponível')
  assert.equal(cardWithRawText.description, 'Você paga R$ 26,91 com o cupom')

  // ---------------------------------------------------------------------
  // Abrangência: só quando o backend já decidiu -- nunca inventa
  // "toda a loja" na ausência de informação.
  // ---------------------------------------------------------------------
  assert.equal(couponScopeLabel(coupon({ scope_kind: 'store_wide' })), 'Válido para toda a loja')
  assert.equal(couponScopeLabel(coupon({ scope_kind: 'product' })), 'Válido para este produto')
  assert.equal(couponScopeLabel(coupon({ scope_kind: null })), null, 'scope_kind desconhecido -- nunca afirma nada')

  // -- compra mínima só quando o backend informou.
  assert.equal(
    couponMinimumPurchaseLabel(coupon({ minimum_purchase_amount: '100.00' })),
    `Compra mínima de R$${NBSP}100,00`,
  )
  assert.equal(couponMinimumPurchaseLabel(coupon({ minimum_purchase_amount: null })), null)

  // ---------------------------------------------------------------------
  // Link real: só http(s) válido vira ação -- nunca fabricado.
  // ---------------------------------------------------------------------
  assert.equal(couponActionUrl(coupon({ source_url: 'https://www.kabum.com.br/cupons' })), 'https://www.kabum.com.br/cupons')
  assert.equal(couponActionUrl(coupon({ source_url: null })), null)
  assert.equal(couponActionUrl(coupon({ source_url: 'javascript:alert(1)' })), null, 'esquema não-http(s) nunca vira link')
  assert.equal(couponActionUrl(coupon({ source_url: 'nao-e-uma-url' })), null, 'string inválida nunca vira link')

  // -- `code` vazio/nulo nunca vira um valor inventado -- passa direto.
  assert.equal(couponToCardData(coupon({ code: null })).code, null)

  // -- `valid_until`: já chega do Coupon Worker como a frase completa
  // que a própria loja usou ("Válido até X"/"Vence X" --
  // `_find_validity_hint`, `coupons/evidence.py`) -- repassado tal como
  // veio, NUNCA reformatado nem reprefixado (bug real corrigido na
  // revisão 2026-09-10: prefixar "Válido até " de novo aqui duplicava o
  // prefixo pra todo cupom com validade real do Worker).
  assert.equal(couponToCardData(coupon({ valid_until: 'Válido até 31/12/2026' })).expiresLabel, 'Válido até 31/12/2026')
  assert.equal(couponToCardData(coupon({ valid_until: 'Vence amanhã' })).expiresLabel, 'Vence amanhã')
  assert.equal(couponToCardData(coupon({ valid_until: null })).expiresLabel, null)

  // ---------------------------------------------------------------------
  // Agrupamento por loja -- contagem correta, múltiplos cupons.
  // ---------------------------------------------------------------------
  const mixedStoreCoupons = [
    coupon({ id: 'k1', store: { code: 'kabum', name: 'KaBuM!' } }),
    coupon({ id: 'k2', store: { code: 'kabum', name: 'KaBuM!' } }),
    coupon({ id: 'a1', store: { code: 'amazon', name: 'Amazon' } }),
  ]
  const groups = groupCouponsByStore(mixedStoreCoupons)
  assert.equal(groups.length, 2, 'duas lojas distintas -- dois grupos')
  const kabumGroup = groups.find((g) => g.storeCode === 'kabum')
  assert.equal(kabumGroup.cards.length, 2, 'contagem correta por loja')
  assert.equal(kabumGroup.storeName, 'KaBuM!')
  const amazonGroup = groups.find((g) => g.storeCode === 'amazon')
  assert.equal(amazonGroup.cards.length, 1)

  // -- loja desconhecida continua aparecendo, com nome/código reais.
  const unknownGroups = groupCouponsByStore([
    coupon({ id: 'u1', store: { code: 'loja_nova_desconhecida', name: 'Loja Nova Desconhecida' } }),
  ])
  assert.equal(unknownGroups.length, 1)
  assert.equal(unknownGroups[0].storeName, 'Loja Nova Desconhecida')
  assert.equal(unknownGroups[0].cards[0].storeCode, 'loja_nova_desconhecida')

  // -- muitos cupons (100+) permanecem organizados -- nenhum é perdido no agrupamento.
  const manyCoupons = Array.from({ length: 120 }, (_, i) => coupon({
    id: `many-${i}`,
    store: { code: i % 3 === 0 ? 'kabum' : i % 3 === 1 ? 'amazon' : 'magalu', name: i % 3 === 0 ? 'KaBuM!' : i % 3 === 1 ? 'Amazon' : 'Magalu' },
  }))
  const manyGroups = groupCouponsByStore(manyCoupons)
  const totalCards = manyGroups.reduce((sum, g) => sum + g.cards.length, 0)
  assert.equal(totalCards, 120, 'nenhum cupom perdido no agrupamento de 120 cupons')
  assert.equal(manyGroups.length, 3)

  // ---------------------------------------------------------------------
  // Renderização real: código visível + botão copiar, desconto
  // percentual/monetário, escopo, condição, compra mínima, validade,
  // link real -- hierarquia completa num card real.
  // ---------------------------------------------------------------------
  const fullCard = couponToCardData(
    coupon({
      id: 'c-completo',
      store: { code: 'kabum', name: 'KaBuM!' },
      code: 'KABUM20',
      discount_kind: 'fixed_amount',
      discount_value: '50.00',
      raw_rule_text: 'Válido só pra placas de vídeo',
      minimum_purchase_amount: '300.00',
      valid_until: 'Válido até 31/12/2026',
      scope_kind: 'product',
      source_url: 'https://www.kabum.com.br/produto/123',
    }),
  )
  const fullHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [fullCard] }))
  assert.match(fullHtml, /KaBuM!/)
  assert.match(fullHtml, /R\$.{1,2}50,00 OFF/) // benefício (espaço fixo entre R$ e o valor)
  assert.match(fullHtml, />Código</) // código destacado, rótulo próprio
  assert.match(fullHtml, /KABUM20/)
  assert.match(fullHtml, /Copiar/) // botão copiar
  assert.match(fullHtml, /Válido para este produto/) // abrangência
  assert.match(fullHtml, /Válido só pra placas de vídeo/) // condição/regra
  assert.match(fullHtml, /Compra mínima de R\$.{1,2}300,00/)
  assert.match(fullHtml, /Válido até 31\/12\/2026/)
  assert.match(fullHtml, /href="https:\/\/www\.kabum\.com\.br\/produto\/123"/)
  assert.match(fullHtml, /target="_blank"/)
  assert.match(fullHtml, /Ver na loja/)

  // -- cupom sem código: nenhum bloco de código vazio/null aparece.
  const noCodeCard = couponToCardData(coupon({ code: null, discount_kind: 'percentage', discount_value: '15.00' }))
  const noCodeHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [noCodeCard] }))
  assert.doesNotMatch(noCodeHtml, />Código</, 'sem código real -- nunca mostra o bloco de código vazio')
  assert.doesNotMatch(noCodeHtml, /null/i)
  assert.doesNotMatch(noCodeHtml, /SEM C.DIGO/i, 'nunca inventa rótulo "SEM CÓDIGO"')
  assert.doesNotMatch(noCodeHtml, /autom.tico/i, 'nunca inventa "automático" sem evidência de que é')

  // -- sem source_url: nenhum link/botão "Ver na loja" aparece.
  const noLinkCard = couponToCardData(coupon({ source_url: null }))
  const noLinkHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [noLinkCard] }))
  assert.doesNotMatch(noLinkHtml, /Ver na loja/)

  // ---------------------------------------------------------------------
  // Loja desconhecida: nunca quebra `CouponCard`, cai no fallback neutro
  // já existente no design system, nome real aparece.
  // ---------------------------------------------------------------------
  const unknownCard = couponToCardData(
    coupon({ store: { code: 'loja_nova_desconhecida', name: 'Loja Nova Desconhecida' } }),
  )
  assert.notEqual(unknownCard, null, 'cupom de loja desconhecida nunca é descartado')
  const unknownHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [unknownCard] }))
  assert.match(unknownHtml, /Loja Nova Desconhecida/, 'nome real da loja aparece mesmo sem mapeamento visual')
  assert.match(unknownHtml, /bg-muted/, 'cai no fallback visual neutro já existente (bg-muted), não inventa cor nova')
  assert.doesNotMatch(unknownHtml, /undefined|null/i, 'nenhum valor quebrado vaza pro HTML')

  // -- loja CONHECIDA continua com o mapeamento visual de sempre.
  const knownCard = couponToCardData(coupon({ store: { code: 'pichau', name: 'Pichau' } }))
  const knownHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [knownCard] }))
  assert.match(knownHtml, /Pichau/)
  assert.match(knownHtml, /pichau\.png/, 'loja conhecida continua usando a logo real, não o fallback neutro')

  // -- página completa: resultado de resolveCouponsPageState usado
  // diretamente, provando a integração real entre mapeamento/agrupamento.
  const pageSuccessState = resolveCouponsPageState(
    [
      coupon({ id: 'g1', store: { code: 'kabum', name: 'KaBuM!' }, code: 'K1' }),
      coupon({ id: 'g2', store: { code: 'kabum', name: 'KaBuM!' }, code: 'K2' }),
      coupon({ id: 'g3', store: { code: 'amazon', name: 'Amazon' }, code: null, raw_rule_text: 'Você paga R$ 20 com o cupom' }),
    ],
    null,
  )
  assert.equal(pageSuccessState.groups.length, 2)
  const kabumPageGroup = pageSuccessState.groups.find((g) => g.storeCode === 'kabum')
  assert.equal(kabumPageGroup.cards.length, 2)

  console.log('coupons page (TASK-121, revisão 2026-09-10): passed')
} finally {
  await server.close()
}
