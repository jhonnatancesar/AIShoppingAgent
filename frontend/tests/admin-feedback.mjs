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
  const { AdminFeedbackView } = await server.ssrLoadModule('/src/pages/admin/AdminFeedbackPage.tsx')
  const { ToastProvider } = await server.ssrLoadModule('/src/hooks/useToast.tsx')

  const bugItem = {
    id: 'f1', user_id: 'u1', user_display_name: 'Cliente Teste', kind: 'bug', channel: 'web',
    message: 'O botão de pausar não funciona.', store_name: null, store_url: null,
    status: 'new', created_at: '2026-09-04T12:00:00Z',
  }
  const suggestionItem = {
    id: 'f2', user_id: null, user_display_name: null, kind: 'store_suggestion', channel: 'telegram',
    message: 'Sugestão de nova loja.', store_name: 'Loja Nova', store_url: 'https://lojanova.example',
    status: 'reviewed', created_at: '2026-09-03T12:00:00Z',
  }

  function render(result) {
    return renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        null,
        React.createElement(ToastProvider, null, React.createElement(AdminFeedbackView, { result })),
      ),
    )
  }

  // Linha recolhida por padrão (expande ao clicar, "abrir" -- só
  // verificável ao vivo, Radix/estado local não roda em SSR): a mensagem
  // e as ações reviewed/closed só existem depois do clique, nunca no
  // render inicial.
  const html = render({ items: [bugItem, suggestionItem], limit: 20, offset: 0, total: 2 })
  assert.match(html, /2 registro\(s\)/)
  assert.match(html, /1–2 de 2/)
  assert.match(html, /Cliente Teste/)
  assert.match(html, />Novo</)
  assert.match(html, />Bug</)
  assert.match(html, /Usuário anônimo/) // suggestionItem sem user_display_name
  assert.match(html, />Sugestão de loja</)
  assert.match(html, />Revisado</)
  assert.doesNotMatch(html, /O botão de pausar não funciona\./) // só aparece expandido
  assert.doesNotMatch(html, /Marcar revisado/) // idem

  // Paginação real (limit/offset/total) -- nunca "página 1 de X" nem pager fake.
  assert.match(html, />Anterior</)
  assert.match(html, />Próxima</)

  // Vazio real.
  const emptyHtml = render({ items: [], limit: 20, offset: 0, total: 0 })
  assert.match(emptyHtml, /Nenhum registro encontrado/)

  // Nenhum diálogo nativo.
  assert.doesNotMatch(html, /onclick="window\.(confirm|alert|prompt)/)

  console.log('admin feedback render: passed')
} finally {
  await server.close()
}
