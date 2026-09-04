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
  const { MissionCreatePage } = await server.ssrLoadModule('/src/pages/missions/MissionCreatePage.tsx')
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(MissionCreatePage)))

  assert.match(html, /O que você está procurando\?/)
  assert.match(html, /Modelo\/variante \(opcional\)/)
  assert.match(html, /Preço-alvo \(opcional\)/)
  assert.match(html, />Moeda</)
  assert.match(html, /todas as 6 lojas da V1/)
  for (const label of ['Amazon', 'KaBuM!', 'Magalu', 'Mercado Livre', 'Pichau', 'Terabyte']) {
    assert.match(html, new RegExp(label.replace('!', '!')))
  }
  assert.match(html, />Criar missão</)
  // Criar não tem alvo prévio para remover -- só Editar (MissionDetailPage) usa esse campo.
  assert.doesNotMatch(html, /Remover preço-alvo/)

  console.log('mission create render: passed')
} finally {
  await server.close()
}
