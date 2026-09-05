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
  const { MissionsListView } = await server.ssrLoadModule('/src/pages/missions/MissionsListPage.tsx')

  const result = {
    items: [
      {
        id: 'mission-1', title: 'RTX 5070 Ti', status: 'active', state_version: 1,
        created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-02T12:00:00Z', expires_at: null,
        target_amount: '4500.00', target_currency: 'BRL',
        sources: [{ store_code: 'kabum', store_name: 'KaBuM!' }, { store_code: 'amazon', store_name: 'Amazon' }],
        relevant_offer_count: 3,
      },
      {
        id: 'mission-2', title: 'Cadeira gamer', status: 'paused', state_version: 1,
        created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-02T12:00:00Z', expires_at: null,
        target_amount: null, target_currency: null, sources: [], relevant_offer_count: 0,
      },
    ],
    limit: 20, offset: 0, total: 2,
  }
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(MissionsListView, { result })))

  assert.match(html, /RTX 5070 Ti/)
  assert.match(html, /Ativa/)
  assert.match(html, /R\$\s*4\.500,00/)
  assert.match(html, /KaBuM!/)
  assert.match(html, /Amazon/)
  assert.match(html, /3 ofertas relevantes/)
  assert.match(html, /\/app\/missions\/mission-1/)
  assert.match(html, /Ver detalhes/)

  assert.match(html, /Cadeira gamer/)
  assert.match(html, /Pausada/)
  assert.match(html, /Sem preço-alvo definido/)
  assert.match(html, /Nenhuma loja selecionada/)
  assert.match(html, /Nenhuma oferta relevante ainda/)

  const emptyHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, null, React.createElement(MissionsListView, { result: { items: [], limit: 20, offset: 0, total: 0 } })),
  )
  assert.match(emptyHtml, /Nada neste filtro/)
  assert.match(emptyHtml, /\/app\/missions\/new/)

  console.log('missions list render: passed')
} finally {
  await server.close()
}
