import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// `Select`/`Popover` (Radix) renderizam seu conteúdo via Portal, que nunca
// aparece em `renderToStaticMarkup` (só existe depois de montado no DOM
// real) -- por isso este teste cobre a estrutura sempre visível (gatilhos,
// contador de filtros ativos, busca) e a inspeção real do Popover aberto
// fica para a validação visual ao vivo em DEV (Subtask 13).
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { FilterBar } = await server.ssrLoadModule('/src/components/FilterBar.tsx')

  const baseProps = {
    draftQuery: '',
    onDraftQueryChange: () => undefined,
    onSearchSubmit: () => undefined,
    store: '',
    sort: 'recent',
    onChange: () => undefined,
    onClearAdvanced: () => undefined,
  }

  // 1) Sem filtro avançado ativo: sem contador no botão "Mais filtros".
  const emptyHtml = renderToStaticMarkup(React.createElement(FilterBar, { ...baseProps, condition: '', availability: '' }))
  assert.match(emptyHtml, /Buscar produto/)
  assert.match(emptyHtml, /Mais filtros/)
  assert.match(emptyHtml, /aria-label="Loja"/)
  assert.match(emptyHtml, /aria-label="Ordenar"/)
  // O contador só deve aparecer quando há filtro avançado ativo -- aqui não
  // deve haver nenhum dígito solto logo após "Mais filtros".
  assert.doesNotMatch(emptyHtml, />Mais filtros<\/span>\s*<span[^>]*>\d/)

  // 2) Com 1 filtro avançado ativo (condição): contador mostra "1".
  const oneActiveHtml = renderToStaticMarkup(React.createElement(FilterBar, { ...baseProps, condition: 'used', availability: '' }))
  assert.match(oneActiveHtml, />1</)

  // 3) Com os 2 filtros avançados ativos: contador mostra "2".
  const twoActiveHtml = renderToStaticMarkup(React.createElement(FilterBar, { ...baseProps, condition: 'used', availability: 'available' }))
  assert.match(twoActiveHtml, />2</)

  console.log('filter bar render: passed')
} finally {
  await server.close()
}
