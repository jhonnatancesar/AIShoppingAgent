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
      }),
    ),
  )
  assert.match(html, /Galaxy S24 Ultra/)
  assert.match(html, /Amazon\.com\.br/)
  assert.match(html, /R\$\s*4\.599,00/)
  assert.match(html, /12x de/)
  assert.match(html, /Abrir oferta na loja/)
  console.log('offer detail render: passed')
} finally {
  await server.close()
}
