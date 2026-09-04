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
  const { MissionDetailView } = await server.ssrLoadModule('/src/pages/missions/MissionDetailPage.tsx')
  const { ToastProvider } = await server.ssrLoadModule('/src/hooks/useToast.tsx')

  function render(mission) {
    return renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        null,
        React.createElement(ToastProvider, null, React.createElement(MissionDetailView, { mission, onReload: () => undefined })),
      ),
    )
  }

  const activeMission = {
    id: 'mission-1', title: 'RTX 5070 Ti', status: 'active', state_version: 3,
    created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-02T12:00:00Z', expires_at: null,
    criteria: {
      search_query: 'RTX 5070 Ti', model: '5070 Ti', target_amount: '4500.00', target_currency: 'BRL',
      request_kind: 'specific_product', variant_selection_mode: 'not_required',
    },
    sources: [{ store_code: 'kabum', store_name: 'KaBuM!' }],
    schedule: { interval_minutes: 60, next_run_at: '2026-09-03T12:00:00Z', last_run_at: null, is_enabled: true },
    transitions: [{ from_status: 'draft', to_status: 'active', command: 'activate', actor_type: 'web', reason: null, transitioned_at: '2026-09-01T12:00:00Z' }],
    offers: [{ id: 'offer-1', title: 'Placa de vídeo RTX 5070 Ti', store_code: 'kabum', store_name: 'KaBuM!', last_seen_at: '2026-09-02T12:00:00Z', condition: 'new' }],
    available_variants: [],
  }

  const activeHtml = render(activeMission)
  assert.match(activeHtml, /RTX 5070 Ti/)
  assert.match(activeHtml, />Ativa</)
  assert.match(activeHtml, />Pausar</)
  assert.doesNotMatch(activeHtml, />Retomar</)
  assert.match(activeHtml, />Cancelar</)
  // O conteúdo do AlertDialog (confirmação) fica num Portal Radix, igual
  // Select/Popover (ver filter-bar.mjs) -- invisível via renderToStaticMarkup
  // quando fechado (padrão). Verificado ao vivo na inspeção visual em DEV.
  assert.match(activeHtml, /R\$\s*4\.500,00/)
  assert.match(activeHtml, /KaBuM!/)
  assert.match(activeHtml, /A cada 60 minutos/)
  assert.match(activeHtml, /Placa de vídeo RTX 5070 Ti/)
  assert.match(activeHtml, /\/app\/offers\/offer-1/)
  assert.match(activeHtml, /Ver detalhes/)
  // Ofertas relacionadas nunca mostram preço (MissionOfferLink não carrega esse dado) --
  // OfferCard degrada honestamente para o mesmo texto que já usa em OffersListPage/ProductSearchPage.
  assert.match(activeHtml, /Preço ainda não coletado/)
  assert.doesNotMatch(activeHtml, />Editar critério</)

  const pausedMission = {
    ...activeMission,
    status: 'paused',
    criteria: { ...activeMission.criteria, target_amount: null, target_currency: null },
    transitions: [],
    offers: [],
  }
  const pausedHtml = render(pausedMission)
  assert.match(pausedHtml, />Retomar</)
  assert.doesNotMatch(pausedHtml, />Pausar</)
  assert.match(pausedHtml, /Sem preço-alvo definido/)
  assert.match(pausedHtml, /Nenhuma transição registrada ainda/)
  assert.match(pausedHtml, /Nenhuma oferta relevante ainda/)
  assert.match(pausedHtml, />Editar critério</)
  assert.match(pausedHtml, />Salvar alterações</)
  assert.match(pausedHtml, /Remover preço-alvo/)

  // Regressão: a API manda target_amount com a precisão bruta do Numeric(19,4)
  // (ex.: "4200.000000") -- o campo editável tem que mostrar o padrão
  // oficial pt-BR completo "4.200,00" (milhar + vírgula, 2 casas), nunca
  // o valor bruto com ponto/zeros extras.
  const pausedWithTarget = {
    ...pausedMission,
    criteria: { ...activeMission.criteria, target_amount: '4200.000000', target_currency: 'BRL' },
  }
  const pausedWithTargetHtml = render(pausedWithTarget)
  assert.match(pausedWithTargetHtml, /value="4\.200,00"/)
  assert.doesNotMatch(pausedWithTargetHtml, /4200\.000000/)
  assert.doesNotMatch(pausedWithTargetHtml, /value="4200,00"/)
  assert.doesNotMatch(pausedWithTargetHtml, /value="4200\.00"/)

  const familyMission = {
    ...activeMission,
    status: 'paused',
    criteria: { ...activeMission.criteria, request_kind: 'product_family', variant_selection_mode: 'pending' },
    available_variants: [
      { product_id: 'p1', label: '5070 Ti 16GB', attributes: {}, selected: false },
      { product_id: 'p2', label: '5070 Ti 8GB', attributes: {}, selected: true },
    ],
  }
  const familyHtml = render(familyMission)
  assert.match(familyHtml, /Variantes encontradas/)
  assert.match(familyHtml, /5070 Ti 16GB/)
  assert.match(familyHtml, /Todas as variantes desta família/)

  const noCriteriaMission = { ...activeMission, criteria: null }
  const noCriteriaHtml = render(noCriteriaMission)
  assert.match(noCriteriaHtml, />—</)

  console.log('mission detail render: passed')
} finally {
  await server.close()
}
