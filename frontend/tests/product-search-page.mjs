import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'
import { readFile } from 'node:fs/promises'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { ProductSearchPage } = await server.ssrLoadModule(
    '/src/pages/search/ProductSearchPage.tsx',
  )
  const html = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      { initialEntries: ['/app/search'] },
      React.createElement(ProductSearchPage),
    ),
  )
  assert.match(html, /O que você está procurando/)
  assert.match(html, /Samsung Galaxy S24 Ultra 512 GB/)
  assert.match(html, /Amazon/)
  assert.match(html, /KaBuM!/)
  assert.match(html, /Pichau/)
  assert.match(html, /Terabyte/)
  assert.match(html, /Nada entra no seu radar até você escolher Monitorar/)
  assert.match(html, />Pesquisar</)
  const source = await readFile(path.join(root, 'src/pages/search/ProductSearchPage.tsx'), 'utf8')
  const submitBody = source.slice(source.indexOf('function submit'), source.indexOf('function toggleStore'))
  const monitorBody = source.slice(source.indexOf('async function monitor'), source.indexOf('const canMonitor'))
  assert.match(submitBody, /runSearch/)
  assert.doesNotMatch(submitBody, /missionsApi\.create/)
  assert.match(monitorBody, /missionsApi\.create/)
  console.log('product search render: passed')
} finally {
  await server.close()
}
