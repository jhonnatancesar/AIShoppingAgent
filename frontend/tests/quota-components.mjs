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
  const { QuotaSummaryCard } = await server.ssrLoadModule('/src/components/QuotaSummary.tsx')
  const { QuotaExceededNotice } = await server.ssrLoadModule(
    '/src/components/QuotaExceededNotice.tsx',
  )

  // TASK-107: render do uso (missões ativas / lojas / pesquisas hoje).
  const quota = {
    active_missions: { current: 3, limit: 5, near_limit: false },
    store_slots: { current: 17, limit: 18, near_limit: true },
    daily_searches: { current: 30, limit: 30, near_limit: true },
    daily_searches_reset_at: '2026-08-23T00:00:00+00:00',
  }
  const summaryHtml = renderToStaticMarkup(React.createElement(QuotaSummaryCard, { quota }))
  assert.match(summaryHtml, /Missões ativas/)
  assert.match(summaryHtml, /3\/5/)
  assert.match(summaryHtml, /Lojas monitoradas/)
  assert.match(summaryHtml, /17\/18/)
  assert.match(summaryHtml, /Pesquisas hoje/)
  assert.match(summaryHtml, /30\/30/)
  // TASK-107: aviso perto do limite aparece para os itens near_limit.
  const nearLimitCount = (summaryHtml.match(/Perto do limite/g) || []).length
  assert.equal(nearLimitCount, 2)

  // TASK-107: quota excedida nunca mostra só erro genérico -- sempre
  // mensagem real + ações estruturadas do backend.
  const storeSlotsHtml = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(QuotaExceededNotice, {
        message: 'Você usa 18/18 lojas de monitoramento.',
        details: {
          kind: 'store_slots',
          limit: 18,
          current: 18,
          actions: ['reduce_mission_stores', 'pause_mission', 'cancel_mission', 'manage_missions'],
        },
      }),
    ),
  )
  assert.match(storeSlotsHtml, /Você usa 18\/18 lojas de monitoramento\./)
  assert.match(storeSlotsHtml, /Reduzir lojas de uma missão/)
  assert.match(storeSlotsHtml, /Pausar uma missão/)
  assert.match(storeSlotsHtml, /Cancelar uma missão/)
  assert.match(storeSlotsHtml, /Gerenciar missões/)

  // TASK-107: cota de pesquisa diária não tem ação de link (só esperar) --
  // não deve renderizar nenhum botão de ação.
  const dailySearchesHtml = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(QuotaExceededNotice, {
        message: 'Você já fez 30/30 pesquisas hoje.',
        details: { kind: 'daily_searches', limit: 30, current: 30, actions: ['wait_for_daily_reset'] },
      }),
    ),
  )
  assert.match(dailySearchesHtml, /Você já fez 30\/30 pesquisas hoje\./)
  assert.doesNotMatch(dailySearchesHtml, /<a /)

  console.log('quota components render: passed')
} finally {
  await server.close()
}
