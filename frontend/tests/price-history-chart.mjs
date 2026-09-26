import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// TASK-125: o ponto do dia fica no preço COM cupom ("tudo na mesma
// linha"); o tooltip mostra preço normal, preço com cupom e o cupom usado.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { PriceHistoryTooltipContent } = await server.ssrLoadModule('/src/components/PriceHistoryChart.tsx')
  const { buildChartData, couponLabel } = await server.ssrLoadModule('/src/components/priceHistoryChartData.ts')

  const history = {
    product_id: 'p1',
    comparable: true,
    reason: null,
    period: '1m',
    currency: 'BRL',
    period_from: null,
    period_to: '2026-09-26T00:00:00Z',
    series: [
      {
        store_id: 's1',
        store_code: 'kabum',
        store_name: 'KaBuM!',
        points: [
          { date: '2026-09-25', amount: '2499.00', original_amount: null, coupon_code: null },
          { date: '2026-09-26', amount: '2249.00', original_amount: '2499.00', coupon_code: 'CPUPROMO' },
        ],
      },
      {
        store_id: 's2',
        store_code: 'amazon',
        store_name: 'Amazon',
        points: [{ date: '2026-09-26', amount: '2399.00', original_amount: '2499.00', coupon_code: '' }],
      },
    ],
    metrics: null,
  }

  // 1) Dados: uma linha por dia, preço do ponto = preço com cupom; preço
  // normal/cupom só nas chaves auxiliares, e só quando houve cupom.
  const rows = buildChartData(history)
  assert.deepEqual(rows.map((row) => row.date), ['2026-09-25', '2026-09-26'])
  assert.equal(rows[0].kabum, 2499)
  assert.equal(rows[0]['kabum__coupon'], undefined)
  assert.equal(rows[1].kabum, 2249)
  assert.equal(rows[1]['kabum__original'], 2499)
  assert.equal(rows[1]['kabum__coupon'], 'CPUPROMO')
  assert.equal(rows[1]['amazon__coupon'], '')

  // 2) Tooltip com cupom: preço normal, preço com cupom e qual cupom.
  const html = renderToStaticMarkup(
    React.createElement(PriceHistoryTooltipContent, {
      active: true,
      label: '2026-09-26',
      history,
      payload: [
        { dataKey: 'kabum', value: 2249, color: 'red', payload: rows[1] },
        { dataKey: 'amazon', value: 2399, color: 'blue', payload: rows[1] },
      ],
    }),
  )
  assert.match(html, /26\/09/)
  assert.match(html, /KaBuM!:/)
  assert.match(html, /Preço normal: R\$\s*2\.499,00/)
  assert.match(html, /Com cupom: R\$\s*2\.249,00/)
  assert.match(html, /Cupom: CPUPROMO/)
  assert.match(html, /Cupom: automático \(sem código\)/)

  // 3) Dia sem cupom: só o preço, sem "Preço normal"/"Cupom".
  const plainHtml = renderToStaticMarkup(
    React.createElement(PriceHistoryTooltipContent, {
      active: true,
      label: '2026-09-25',
      history,
      payload: [{ dataKey: 'kabum', value: 2499, color: 'red', payload: rows[0] }],
    }),
  )
  assert.match(plainHtml, /R\$\s*2\.499,00/)
  assert.doesNotMatch(plainHtml, /Preço normal|Cupom:/)

  // 4) Fora do hover, nada.
  assert.equal(
    renderToStaticMarkup(React.createElement(PriceHistoryTooltipContent, { active: false, history, payload: [] })),
    '',
  )
  assert.equal(couponLabel('  '), 'automático (sem código)')

  console.log('price-history-chart: ok')
} finally {
  await server.close()
}
