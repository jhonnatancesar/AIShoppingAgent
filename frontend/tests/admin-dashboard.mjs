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
  const { AdminDashboardView } = await server.ssrLoadModule('/src/pages/admin/AdminDashboardPage.tsx')
  const { ToastProvider } = await server.ssrLoadModule('/src/hooks/useToast.tsx')

  const data = {
    dashboard: {
      generated_at: '2026-09-04T12:00:00Z',
      api: 'healthy', postgresql: 'healthy', redis: 'healthy',
      users: { total: 10, active: 8 },
      missions: { total: 20, active: 12 },
      collections: { total: 100, failed_24h: 2 },
      events: { total: 300, failed_24h: 1 },
      stores: [{ id: 's1', code: 'kabum', name: 'KaBuM!', is_active: true, last_run_status: 'succeeded', last_run_at: '2026-09-04T11:00:00Z' }],
      workers: [{ service: 'collection_worker', status: 'running', detail: null }],
      ai_history_available: true, circuit_state_available: true,
    },
    queue: {
      generated_at: '2026-09-04T12:00:00Z',
      config: {
        max_concurrent_user_batches: 2, max_concurrent_user_batches_override: null,
        user_cooldown_min_seconds: 60, user_cooldown_min_seconds_override: null,
        user_cooldown_max_seconds: 180, user_cooldown_max_seconds_override: null,
        store_min_interval_seconds: 10, store_min_interval_seconds_override: 15,
      },
      users: [{ user_id: 'u1', display_name: 'Cliente Teste', is_processing_now: true, queue_position: null, last_processed_at: null, next_eligible_at: null, cooldown_active: false }],
      stores: [{ store_id: 's1', code: 'kabum', name: 'KaBuM!', next_allowed_at: null, throttled: false }],
    },
    apiKeys: { enabled: false, authentication_enabled: false, issuance_enabled: false, message: 'Em breve / desativado' },
  }

  function render(customData) {
    return renderToStaticMarkup(
      React.createElement(MemoryRouter, null, React.createElement(ToastProvider, null, React.createElement(AdminDashboardView, { data: customData, onReload: () => undefined }))),
    )
  }

  const html = render(data)
  assert.match(html, /API: Saudável/)
  assert.match(html, /PostgreSQL: Saudável/)
  assert.match(html, /Redis: Saudável/)
  assert.match(html, /8\/10/)
  assert.match(html, /12\/20/)
  assert.match(html, /100 \/ 2/)
  assert.match(html, /300 \/ 1/)
  assert.match(html, /collection_worker/)
  assert.match(html, />Iniciar</)
  assert.match(html, />Reiniciar</)
  assert.match(html, /KaBuM!/)
  assert.match(html, />Desabilitar</)
  // Fila justa/pacing -- somente leitura, sem controles de edição
  // (PATCH /admin/queue/config fica fora de escopo desta Subtask).
  assert.match(html, /Fila e intervalos/)
  assert.match(html, /Cliente Teste/)
  assert.match(html, /Processando agora/)
  assert.doesNotMatch(html, /Salvar configuração/)
  assert.doesNotMatch(html, /<input[^>]*name="max_concurrent/)
  // API para agentes -- dado real do endpoint, nunca texto fixo.
  assert.match(html, /Em breve \/ desativado/)
  // Nenhum diálogo nativo.
  assert.doesNotMatch(html, /onclick="window\.(confirm|alert|prompt)/)

  // Fila vazia -- estado real (ninguém processando/aguardando agora), não card fake.
  const emptyQueueHtml = render({ ...data, queue: { ...data.queue, users: [] } })
  assert.match(emptyQueueHtml, /Nenhum usuário processando ou aguardando agora/)

  // Loja habilitada -- ação e tom trocam.
  const disabledStoreHtml = render({ ...data, dashboard: { ...data.dashboard, stores: [{ ...data.dashboard.stores[0], is_active: false }] } })
  assert.match(disabledStoreHtml, />Habilitar</)

  console.log('admin dashboard render: passed')
} finally {
  await server.close()
}
