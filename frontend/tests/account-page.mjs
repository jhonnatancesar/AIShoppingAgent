import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
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
  const html = renderToStaticMarkup(
    React.createElement(AccountView, {
      account,
      onProfileSaved: () => undefined,
      onNotificationsSaved: () => undefined,
      onTelegramChanged: () => undefined,
    }),
  )

  assert.match(html, /Minha conta/)
  assert.match(html, /Cliente Teste/)
  assert.match(html, /cliente@example.com/)
  assert.match(html, /Telegram/)
  assert.match(html, /Vinculado/)
  assert.match(html, /Desvincular Telegram/)
  assert.match(html, /Quedas de preço/)
  assert.match(html, /Preço-alvo atingido/)
  const pendingHtml = renderToStaticMarkup(
    React.createElement(AccountView, {
      account: { ...account, telegram_linked: false, telegram_link_status: 'pending', telegram_link_expires_at: '2026-08-22T12:10:00+00:00' },
      onProfileSaved: () => undefined,
      onNotificationsSaved: () => undefined,
      onTelegramChanged: () => undefined,
    }),
  )
  assert.match(pendingHtml, /Vinculação pendente/)
  assert.match(pendingHtml, /Gerar novo código/)
  const unlinkedHtml = renderToStaticMarkup(
    React.createElement(AccountView, {
      account: { ...account, telegram_linked: false, telegram_link_status: 'not_linked' },
      onProfileSaved: () => undefined,
      onNotificationsSaved: () => undefined,
      onTelegramChanged: () => undefined,
    }),
  )
  assert.match(unlinkedHtml, /Não vinculado/)
  assert.match(unlinkedHtml, /Vincular Telegram/)
  console.log('account page render: passed')
} finally {
  await server.close()
}
