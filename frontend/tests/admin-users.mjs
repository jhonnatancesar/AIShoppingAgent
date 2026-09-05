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
  const { AdminUsersView } = await server.ssrLoadModule('/src/pages/admin/AdminUsersPage.tsx')
  const { ToastProvider } = await server.ssrLoadModule('/src/hooks/useToast.tsx')

  const activeUser = {
    id: 'u1', display_name: 'Cliente Ativo', username: 'cliente_ativo', email: 'a@example.com',
    role: 'USER', lifecycle_status: 'active', is_active: true, mission_count: 3,
    max_active_missions_override: null, max_store_slots_override: 10, max_daily_searches_override: null,
  }
  const blockedUser = {
    id: 'u2', display_name: 'Cliente Bloqueado', username: 'cliente_bloqueado', email: null,
    role: 'ADMIN', lifecycle_status: 'blocked', is_active: false, mission_count: 0,
    max_active_missions_override: null, max_store_slots_override: null, max_daily_searches_override: null,
  }

  function render({ query = '', users = [activeUser, blockedUser], error = null }) {
    return renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        null,
        React.createElement(
          ToastProvider,
          null,
          React.createElement(AdminUsersView, { query, onQueryChange: () => undefined, users, error, onReload: () => undefined }),
        ),
      ),
    )
  }

  const html = render({ query: 'cliente' })
  assert.match(html, /value="cliente"/)
  // Achado real da auditoria: GET /admin/users não tem offset/total -- só
  // corte fixo de 200 no backend. Nunca inventar "página 1 de X".
  assert.match(html, /at[ée] 200 contas/)
  assert.doesNotMatch(html, /página \d+ de \d+/i)
  assert.doesNotMatch(html, />Anterior</)
  assert.doesNotMatch(html, />Próxima</)

  assert.match(html, /Cliente Ativo/)
  assert.match(html, /@cliente_ativo/)
  assert.match(html, /3 missões/)
  assert.match(html, /padrão missões/) // max_active_missions_override null
  assert.match(html, /10 lojas/) // max_store_slots_override = 10
  assert.match(html, />Desativar</)
  assert.match(html, />Bloquear</)
  assert.match(html, />Cotas</)
  assert.match(html, />Remover</)

  assert.match(html, /Cliente Bloqueado/)
  assert.match(html, />Ativar</) // usuário bloqueado oferece reativar, não desativar/bloquear de novo

  assert.match(html, />Adicionar</) // abre o Dialog de criação

  // Estado vazio real.
  const emptyHtml = render({ users: [] })
  assert.match(emptyHtml, /Nenhum usuário encontrado/)

  // Estado de erro real.
  const errorHtml = render({ users: null, error: 'Falha ao consultar.' })
  assert.match(errorHtml, /Não foi possível carregar os usuários/)
  assert.match(errorHtml, /Falha ao consultar\./)

  // Nenhum diálogo nativo em nenhum cenário.
  for (const rendered of [html, emptyHtml, errorHtml]) {
    assert.doesNotMatch(rendered, /onclick="window\.(confirm|alert|prompt)/)
  }

  console.log('admin users render: passed')
} finally {
  await server.close()
}
