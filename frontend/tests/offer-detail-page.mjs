import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { OfferDetailView } = await server.ssrLoadModule(
    '/src/pages/offers/OfferDetailPage.tsx',
  )
  const html = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(OfferDetailView, {
        offer: {
          id: 'offer-1',
          title: 'Galaxy S24 Ultra',
          image_url: 'https://images.example/galaxy.jpg',
          original_url: 'https://amazon.com.br/dp/example',
          last_seen_at: '2026-08-22T15:00:00Z',
          store: { code: 'amazon', name: 'Amazon' },
          seller: { name: 'Amazon.com.br' },
          rating: {
            average: '4.80',
            review_count: 2256,
            observed_at: '2026-08-22T15:00:00Z',
          },
          latest_observation: {
            amount: '4599.00',
            currency: 'BRL',
            shipping_amount: '20.00',
            total_amount: '4619.00',
            fulfillment: 'Amazon.com.br',
            seller_kind: 'platform',
            fulfillment_kind: 'platform',
            condition: 'new',
            availability: 'available',
            observed_at: '2026-08-22T15:00:00Z',
            installments: [{
              installment_count: 12,
              installment_amount: '383.25',
              installment_total_amount: null,
              discount_percent: null,
              interest_kind: 'interest_free',
              is_highlighted: true,
            }],
          },
        },
        comparison: {
          product_id: 'product-1', title: 'Galaxy S24 Ultra 512 GB',
          variant: '512 GB', attributes: { storage_gb: '512' }, comparable: true,
          offers: [
            {
              id: 'offer-1', original_url: 'https://amazon.com.br/dp/example', image_url: null,
              store: { code: 'amazon', name: 'Amazon' }, seller: { name: 'Amazon.com.br' },
              rating: { average: '4.80', review_count: 2256, observed_at: '2026-08-22T15:00:00Z' },
              latest_observation: {
                amount: '4599.00', currency: 'BRL', shipping_amount: '20.00', total_amount: '4619.00',
                fulfillment: 'Amazon.com.br', seller_kind: 'platform', fulfillment_kind: 'platform',
                condition: 'new', availability: 'available', observed_at: '2026-08-22T15:00:00Z', installments: [],
              },
            },
            {
              id: 'offer-2', original_url: 'https://kabum.com.br/produto/example', image_url: null,
              store: { code: 'kabum', name: 'KaBuM!' }, seller: null, rating: null,
              latest_observation: {
                amount: '4499.00', currency: 'BRL', shipping_amount: null, total_amount: '4499.00',
                fulfillment: null, seller_kind: 'platform', fulfillment_kind: 'platform',
                condition: 'new', availability: 'available', observed_at: '2026-08-22T15:00:00Z', installments: [],
              },
            },
          ],
        },
      }),
    ),
  )
  assert.match(html, /Galaxy S24 Ultra/)
  assert.match(html, /Amazon\.com\.br/)
  assert.match(html, /4,8.*2\.256 avaliações/)
  assert.match(html, /R\$\s*4\.599,00/)
  assert.match(html, /12x de/)
  // Parcela marcada `is_highlighted` pelo backend ganha destaque visual real
  // (Subtask 13) -- nunca um algoritmo novo decidindo "a melhor parcela".
  assert.match(html, />Destaque</)
  // CTA externo padronizado (Subtask 13): "Ver na loja" em todos os pontos
  // que abrem a página real da loja (aqui e na comparação entre lojas),
  // nunca "Ver anúncio"/"Loja"/"Abrir oferta na loja" (textos antigos).
  assert.match(html, /Ver na loja/)
  const externalCtaCount = (html.match(/Ver na loja/g) || []).length
  assert.ok(externalCtaCount >= 2, 'CTA "Ver na loja" deve aparecer no preço principal e na comparação')
  assert.doesNotMatch(html, /Ver anúncio/)
  assert.doesNotMatch(html, /Abrir oferta na loja/)
  assert.match(html, /Comparar entre lojas/)
  assert.match(html, /Galaxy S24 Ultra 512 GB/)
  assert.match(html, /KaBuM!/)
  assert.match(html, /Variantes diferentes nunca entram/)
  // Diferença de preço na comparação (Subtask 13): só quando os dois lados
  // têm preço real (aqui KaBuM! R$4.499,00 vs. Amazon R$4.619,00 -- R$120
  // mais barato), nunca uma porcentagem inventada.
  assert.match(html, /R\$\s*120,00 mais barato/)
  assert.doesNotMatch(html, /%\s*mais (barato|caro)/)
  console.log('offer detail render: passed')
} finally {
  await server.close()
}
