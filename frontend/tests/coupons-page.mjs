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
  const html = renderToStaticMarkup(React.createElement(CouponsPage))

  assert.match(html, /Cupons/, 'título da seção deve aparecer')
  assert.match(html, /modelos de apresentação, não cupons ativos/i, 'modelos devem ser identificados com honestidade')
  for (const store of ['Amazon', 'KaBuM!', 'Magalu', 'Mercado Livre', 'Pichau', 'Terabyte']) {
    assert.match(html, new RegExp(store.replace('!', '!')), `modelo de ${store} deve aparecer`)
  }

  // Subtask 11: zero dado artificial apresentado como se fosse real.
  assert.doesNotMatch(html, /DESCONTO10/i, 'nunca um código de cupom fictício')
  assert.doesNotMatch(html, /\d+\s*cupons?\s+dispon[íi]ve(l|is)/i, 'nunca uma contagem de cupom inventada')
  assert.doesNotMatch(html, />\s*\d+\s*%/, 'nenhuma porcentagem de desconto inventada no conteúdo')
  assert.doesNotMatch(html, /(collector|scraper|AIShoppingAgent-cupom|repositório)/i, 'nenhum detalhe técnico interno exposto ao usuário')

  const single = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [{ id: 'c1', storeCode: 'amazon', title: 'Benefício real', code: 'CODIGO_REAL' }] }))
  assert.match(single, /Amazon/)
  assert.match(single, /CODIGO_REAL/)
  assert.match(single, /sm:grid-cols-\[11rem_1fr\]/, 'um cupom usa o layout de destaque')

  const multiple = renderToStaticMarkup(React.createElement(CouponCollection, { coupons: [
    { id: 'c1', storeCode: 'kabum', title: 'Primeiro' },
    { id: 'c2', storeCode: 'magalu', title: 'Segundo' },
  ] }))
  assert.match(multiple, /KaBuM!/)
  assert.match(multiple, /Magalu/)
  assert.match(multiple, /md:grid-cols-2/, 'vários cupons usam grade responsiva')

  console.log('coupons page render: passed')
} finally {
  await server.close()
}
