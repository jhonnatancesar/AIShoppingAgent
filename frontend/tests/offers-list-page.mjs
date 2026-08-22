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
  const { OffersListView } = await server.ssrLoadModule('/src/pages/offers/OffersListPage.tsx')
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(OffersListView, { result: {
    items: [{
      id: 'offer-1', title: 'Galaxy S24 Ultra 512 GB', image_url: null,
      last_seen_at: '2026-08-22T19:00:00Z', store: { code: 'amazon', name: 'Amazon' },
      seller: { name: 'Amazon.com.br' }, rating: { average: '4.8', review_count: 125, observed_at: '2026-08-22T19:00:00Z' },
      latest_observation: { amount: '4599.00', total_amount: '4619.00', currency: 'BRL', condition: 'new', availability: 'available', observed_at: '2026-08-22T19:00:00Z' },
    }], limit: 24, offset: 0, total: 1,
  } })))
  assert.match(html, /Galaxy S24 Ultra 512 GB/)
  assert.match(html, /Amazon\.com\.br/)
  assert.match(html, /R\$\s*4\.599,00/)
  assert.match(html, /Ver detalhes/)
  assert.match(html, /\/app\/offers\/offer-1/)
  console.log('offers list render: passed')
} finally {
  await server.close()
}
