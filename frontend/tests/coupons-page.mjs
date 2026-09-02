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
  const html = renderToStaticMarkup(React.createElement(CouponsPage))

  assert.match(html, /Cupons/, 'título da seção deve aparecer')
  assert.match(html, /Nenhum cupom dispon[íi]vel no momento/, 'estado vazio honesto deve aparecer')
  assert.match(
    html,
    /Quando houver cupons dispon[íi]veis no GG Oferta, eles aparecer[ãa]o aqui/,
    'complemento do estado vazio deve aparecer',
  )

  // Subtask 11: zero dado artificial apresentado como se fosse real.
  assert.doesNotMatch(html, /DESCONTO10/i, 'nunca um código de cupom fictício')
  assert.doesNotMatch(html, /\d+\s*cupons?\s+dispon[íi]ve(l|is)/i, 'nunca uma contagem de cupom inventada')
  assert.doesNotMatch(html, /%/, 'nenhuma porcentagem de desconto inventada')
  assert.doesNotMatch(html, /(collector|scraper|AIShoppingAgent-cupom|repositório)/i, 'nenhum detalhe técnico interno exposto ao usuário')

  console.log('coupons page render: passed')
} finally {
  await server.close()
}
