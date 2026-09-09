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
  const { couponTitle, couponToCardData, resolveCouponsPageState } = await server.ssrLoadModule(
    '/src/pages/couponCardMapping.ts',
  )

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
      ...overrides,
    }
  }

  // -- TASK-121: sem <useEffect> executado (SSR puro), a página SEMPRE
  // renderiza o estado inicial -- prova honesta de "loading", nunca dado
  // artificial (os templates antigos, Subtask 11, saíram de vez).
  const initialHtml = renderToStaticMarkup(React.createElement(CouponsPage))
  assert.match(initialHtml, /Cupons/, 'título da seção deve aparecer')
  assert.match(initialHtml, /Carregando cupons/i, 'estado inicial é o de carregamento')
  assert.doesNotMatch(initialHtml, /modelos de apresentação/i, 'templates hardcoded (Subtask 11) não existem mais')
  assert.doesNotMatch(initialHtml, /Espaço pronto para o próximo cupom/i, 'texto de modelo antigo removido')
  assert.doesNotMatch(initialHtml, /DESCONTO10/i, 'nunca um código de cupom fictício')

  // -- resolveCouponsPageState: lógica pura de qual estado mostrar
  // (extraída do componente exatamente pra ficar testável sem jsdom).
  assert.equal(resolveCouponsPageState(null, null).kind, 'loading')
  const errorState = resolveCouponsPageState(null, 'Falha ao carregar')
  assert.equal(errorState.kind, 'error')
  assert.equal(errorState.message, 'Falha ao carregar')
  assert.equal(resolveCouponsPageState([], null).kind, 'empty')
  const successState = resolveCouponsPageState([coupon()], null)
  assert.equal(successState.kind, 'success')
  assert.equal(successState.cards.length, 1)
  // Erro só vence quando ainda não há cards -- uma vez com dado real em
  // mãos, um erro de uma tentativa posterior nunca apaga o que já apareceu.
  assert.equal(resolveCouponsPageState([coupon()], 'erro tardio').kind, 'success')

  // -- Loja não reconhecida: NUNCA descarta o cupom (achado real corrigido
  // depois da revisão -- a versão anterior retornava `null` e o cupom
  // sumia da aba). `store.name`/`store.code` reais são preservados.
  const unknownStoreCoupon = coupon({ store: { code: 'loja_nova_desconhecida', name: 'Loja Nova Desconhecida' } })
  const unknownCard = couponToCardData(unknownStoreCoupon)
  assert.notEqual(unknownCard, null, 'cupom de loja desconhecida nunca é descartado')
  assert.equal(unknownCard.storeCode, 'loja_nova_desconhecida')
  assert.equal(unknownCard.storeName, 'Loja Nova Desconhecida')

  // -- Renderização real da loja desconhecida: nunca quebra `CouponCard`
  // (visual neutro do próprio design system, nenhuma cor/logo inventada),
  // e o nome real da loja aparece na tela -- nenhum cupom válido some por
  // falta de mapeamento visual.
  const unknownHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [unknownCard] }))
  assert.match(unknownHtml, /Loja Nova Desconhecida/, 'nome real da loja aparece mesmo sem mapeamento visual')
  assert.match(unknownHtml, /bg-muted/, 'cai no fallback visual neutro já existente (bg-muted), não inventa cor nova')
  assert.doesNotMatch(unknownHtml, /undefined|null/i, 'nenhum valor quebrado vaza pro HTML')

  // -- Loja CONHECIDA continua com o mapeamento visual de sempre (logo,
  // cor da marca) -- a correção não alterou o caminho já existente.
  const knownCard = couponToCardData(coupon({ store: { code: 'pichau', name: 'Pichau' } }))
  const knownHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [knownCard] }))
  assert.match(knownHtml, /Pichau/)
  assert.match(knownHtml, /pichau\.png/, 'loja conhecida continua usando a logo real, não o fallback neutro')

  // -- lado a lado, numa mesma coleção: cupom de loja conhecida e de loja
  // desconhecida convivem -- nenhum dos dois some.
  const mixedHtml = renderToStaticMarkup(
    React.createElement(CouponCollection, { coupons: [knownCard, unknownCard] }),
  )
  assert.match(mixedHtml, /Pichau/)
  assert.match(mixedHtml, /Loja Nova Desconhecida/)

  // -- key baseada em `Coupon.id` (nunca um índice, nunca um valor
  // inventado) -- é o mesmo `id` que `CouponCollection` já usa como key.
  const card = couponToCardData(coupon())
  assert.equal(card.id, 'c-real-1')

  // -- título: prioridade 1 -- desconto ESTRUTURADO, valor real só
  // formatado, nunca inventado.
  assert.equal(couponTitle(coupon({ discount_kind: 'fixed_amount', discount_value: '100.00' })), 'R$ 100,00 OFF')
  assert.equal(couponTitle(coupon({ discount_kind: 'percentage', discount_value: '10.00' })), '10% OFF')
  assert.equal(couponTitle(coupon({ discount_kind: 'percentage', discount_value: '12.50' })), '12,5% OFF')

  // -- título: prioridade 2 -- fallback pro texto cru coletado, aparado.
  assert.equal(
    couponTitle(coupon({ discount_kind: null, discount_value: null, raw_rule_text: '  10% OFF com Cupom  ' })),
    '10% OFF com Cupom',
  )

  // -- título: prioridade 3 -- fallback neutro, nenhum valor inventado.
  assert.equal(couponTitle(coupon({ discount_kind: null, discount_value: null, raw_rule_text: null })), 'Cupom disponível')
  assert.equal(couponTitle(coupon({ discount_kind: null, discount_value: null, raw_rule_text: '   ' })), 'Cupom disponível')

  // -- `code` vazio/nulo nunca vira um valor inventado -- passa direto.
  assert.equal(couponToCardData(coupon({ code: null })).code, null)

  // -- `valid_until`: texto cru repassado, nunca formatado como data.
  assert.equal(couponToCardData(coupon({ valid_until: '31/12/2026' })).expiresLabel, 'Válido até 31/12/2026')
  assert.equal(couponToCardData(coupon({ valid_until: null })).expiresLabel, null)

  // -- renderização real: `CouponCollection` com dado mapeado de um cupom
  // de verdade (não mais os 6 templates fixos) mostra loja/código/título.
  const realCard = couponToCardData(
    coupon({
      id: 'c-real-2',
      store: { code: 'kabum', name: 'KaBuM!' },
      code: 'KABUM20',
      discount_kind: 'fixed_amount',
      discount_value: '50.00',
      raw_rule_text: 'Válido só pra placas de vídeo',
      minimum_purchase_amount: '300.00',
    }),
  )
  const realHtml = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [realCard] }))
  assert.match(realHtml, /KaBuM!/)
  assert.match(realHtml, /KABUM20/)
  assert.match(realHtml, /R\$\s*50,00 OFF/)
  assert.match(realHtml, /Válido só pra placas de vídeo/)
  assert.match(realHtml, /Compra mínima de R\$\s*300,00/)

  console.log('coupons page (TASK-121, dados reais): passed')
} finally {
  await server.close()
}
