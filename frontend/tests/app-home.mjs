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
  const { AppHomeView } = await server.ssrLoadModule('/src/pages/AppHome.tsx')

  const baseAccount = {
    id: '11111111-1111-1111-1111-111111111111',
    display_name: 'Cliente Teste',
    username: 'cliente',
    email: 'cliente@example.com',
    email_verified_at: null,
    email_verification_available: false,
    role: 'USER',
    telegram_linked: true,
    telegram_link_status: 'linked',
    telegram_link_expires_at: null,
    created_at: '2026-08-22T12:00:00+00:00',
    favorite_stores: [],
    preferred_categories: [],
    notify_price_decreases: true,
    notify_target_reached: true,
    available_stores: [],
    available_categories: [],
  }
  const okQuota = {
    active_missions: { current: 1, limit: 5, near_limit: false },
    store_slots: { current: 2, limit: 18, near_limit: false },
    daily_searches: { current: 1, limit: 30, near_limit: false },
    daily_searches_reset_at: '2026-08-23T00:00:00+00:00',
  }
  const offer = {
    id: 'offer-1', title: 'RTX 5070 Ti', image_url: null, image_fallback_url: null,
    last_seen_at: '2026-08-22T19:00:00Z', store: { code: 'kabum', name: 'KaBuM!' },
    seller: null, rating: null,
    latest_observation: { amount: '4599.00', total_amount: '4619.00', currency: 'BRL', condition: 'new', availability: 'available', observed_at: '2026-08-22T19:00:00Z' },
  }

  function render({ isAdmin = false, data }) {
    return renderToStaticMarkup(
      React.createElement(MemoryRouter, null, React.createElement(AppHomeView, { displayName: 'Cliente Teste', isAdmin, data })),
    )
  }

  // Usuário realmente sem nenhuma missão (totalMissions === 0) -- estado
  // de "primeira missão", sem os blocos de resumo/ofertas.
  const brandNewHtml = render({
    data: { activeCount: 0, pausedCount: 0, totalMissions: 0, offers: [], account: baseAccount, quota: okQuota },
  })
  assert.match(brandNewHtml, /Olá, Cliente Teste/)
  assert.match(brandNewHtml, /O que você quer colocar no radar/)
  assert.match(brandNewHtml, /Criar minha primeira missão/)
  assert.doesNotMatch(brandNewHtml, /Missões ativas/)
  assert.doesNotMatch(brandNewHtml, /Últimas ofertas relevantes/)

  // Usuário com missões só em outros estados (ex.: só cancelada) --
  // totalMissions > 0 mas active+paused = 0 -- NÃO é "usuário novo",
  // precisa mostrar o resumo real (0 ativas, 0 pausadas), não o CTA de
  // primeira missão.
  const onlyOtherStatesHtml = render({
    data: { activeCount: 0, pausedCount: 0, totalMissions: 1, offers: [], account: baseAccount, quota: okQuota },
  })
  assert.doesNotMatch(onlyOtherStatesHtml, /O que você quer colocar no radar/)
  assert.match(onlyOtherStatesHtml, /Missões ativas/)
  assert.match(onlyOtherStatesHtml, /Missões pausadas/)

  // Usuário com missões ativas/pausadas reais.
  const withMissionsHtml = render({
    data: { activeCount: 2, pausedCount: 1, totalMissions: 4, offers: [offer], account: baseAccount, quota: okQuota },
  })
  assert.match(withMissionsHtml, /Missões ativas/)
  assert.match(withMissionsHtml, />2</)
  assert.match(withMissionsHtml, /Missões pausadas/)
  assert.match(withMissionsHtml, />1</)
  assert.match(withMissionsHtml, /Telegram vinculado/)
  assert.match(withMissionsHtml, /Últimas ofertas relevantes/)
  assert.match(withMissionsHtml, /RTX 5070 Ti/)
  assert.match(withMissionsHtml, /Ver todas/)
  assert.doesNotMatch(withMissionsHtml, /Perto do limite/)

  // Telegram desconectado.
  const noTelegramHtml = render({
    data: { activeCount: 1, pausedCount: 0, totalMissions: 1, offers: [offer], account: { ...baseAccount, telegram_linked: false, telegram_link_status: 'not_linked' }, quota: okQuota },
  })
  assert.match(noTelegramHtml, /Telegram não vinculado/)

  // Quota perto do limite -- aviso deve aparecer, só com os itens
  // realmente near_limit (nunca inventa um aviso para os que estão OK).
  const nearLimitQuota = {
    active_missions: { current: 5, limit: 5, near_limit: true },
    store_slots: { current: 2, limit: 18, near_limit: false },
    daily_searches: { current: 30, limit: 30, near_limit: true },
    daily_searches_reset_at: '2026-08-23T00:00:00+00:00',
  }
  const nearLimitHtml = render({
    data: { activeCount: 5, pausedCount: 0, totalMissions: 5, offers: [offer], account: baseAccount, quota: nearLimitQuota },
  })
  assert.match(nearLimitHtml, /Perto do limite/)
  assert.match(nearLimitHtml, /Missões ativas/)
  assert.match(nearLimitHtml, /Pesquisas hoje/)
  assert.doesNotMatch(nearLimitHtml, /Lojas monitoradas/) // não está near_limit, não aparece no aviso

  // Zero ofertas -- estado vazio útil, nunca card fake.
  const noOffersHtml = render({
    data: { activeCount: 1, pausedCount: 0, totalMissions: 1, offers: [], account: baseAccount, quota: okQuota },
  })
  assert.match(noOffersHtml, /Nada novo por enquanto/)
  assert.doesNotMatch(noOffersHtml, /Ver todas/)

  // ADMIN com acesso real -- link discreto para /admin.
  const adminHtml = render({
    isAdmin: true,
    data: { activeCount: 1, pausedCount: 0, totalMissions: 1, offers: [], account: { ...baseAccount, role: 'ADMIN' }, quota: okQuota },
  })
  assert.match(adminHtml, /acesso administrativo/)
  assert.match(adminHtml, /\/admin/)

  // USER sem acesso administrativo -- nenhum link/menção a /admin.
  const userHtml = render({
    isAdmin: false,
    data: { activeCount: 1, pausedCount: 0, totalMissions: 1, offers: [], account: baseAccount, quota: okQuota },
  })
  assert.doesNotMatch(userHtml, /acesso administrativo/)
  assert.doesNotMatch(userHtml, /\/admin/)

  // Nenhuma métrica falsa: nada de "economia estimada", "%" inventado ou
  // "ofertas encontradas hoje".
  for (const html of [brandNewHtml, withMissionsHtml, nearLimitHtml]) {
    assert.doesNotMatch(html, /economia/i)
    assert.doesNotMatch(html, /encontradas hoje/i)
  }

  console.log('app home render: passed')
} finally {
  await server.close()
}
