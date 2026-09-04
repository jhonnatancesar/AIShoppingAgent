import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'

const account = {
  id: '11111111-1111-1111-1111-111111111111',
  display_name: 'Cliente Teste',
  username: 'cliente',
  email: 'cliente@example.com',
  role: 'USER',
  telegram_linked: true,
  telegram_link_status: 'linked',
  telegram_link_expires_at: null,
  created_at: '2026-08-22T12:00:00+00:00',
  favorite_stores: ['amazon'],
  preferred_categories: ['celulares'],
  notify_price_decreases: true,
  notify_target_reached: false,
  available_stores: [{ code: 'amazon', label: 'Amazon' }],
  available_categories: [{ code: 'celulares', label: 'Celulares' }],
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { AccountView } = await server.ssrLoadModule('/src/pages/account/AccountPage.tsx')
  const { ToastProvider } = await server.ssrLoadModule('/src/hooks/useToast.tsx')

  function render(props) {
    return renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        null,
        React.createElement(ToastProvider, null, React.createElement(AccountView, props)),
      ),
    )
  }

  const html = render({
    account,
    onProfileSaved: () => undefined,
    onNotificationsSaved: () => undefined,
    onTelegramChanged: () => undefined,
  })

  assert.match(html, /Minha conta/)
  assert.match(html, /Cliente Teste/)
  assert.match(html, /cliente@example.com/)
  assert.match(html, /Telegram/)
  assert.match(html, /Vinculado/)
  assert.match(html, /Desvincular Telegram/)
  assert.match(html, /Quedas de preço/)
  assert.match(html, /Preço-alvo atingido/)
  // Subtask 15: unlink não é mais window.confirm -- o botão agora abre um
  // AlertDialog com confirmação explícita ("Desvincular" separado do
  // trigger "Desvincular Telegram"). O conteúdo do diálogo em si fica num
  // Portal Radix, invisível via renderToStaticMarkup quando fechado (mesmo
  // padrão já documentado em filter-bar.mjs/mission-detail-page.mjs) --
  // verificado ao vivo na inspeção visual.

  const pendingHtml = render({
    account: { ...account, telegram_linked: false, telegram_link_status: 'pending', telegram_link_expires_at: '2026-08-22T12:10:00+00:00' },
    onProfileSaved: () => undefined,
    onNotificationsSaved: () => undefined,
    onTelegramChanged: () => undefined,
  })
  assert.match(pendingHtml, /Vinculação pendente/)
  assert.match(pendingHtml, /Gerar novo código/)
  // Subtask 15: retokenizado de amber-500 cru para o token --warning.
  assert.match(pendingHtml, /border-warning\/30/)
  assert.doesNotMatch(pendingHtml, /amber/)

  const unlinkedHtml = render({
    account: { ...account, telegram_linked: false, telegram_link_status: 'not_linked' },
    onProfileSaved: () => undefined,
    onNotificationsSaved: () => undefined,
    onTelegramChanged: () => undefined,
  })
  assert.match(unlinkedHtml, /Não vinculado/)
  assert.match(unlinkedHtml, /Vincular Telegram/)

  console.log('account page render: passed')
} finally {
  await server.close()
}
