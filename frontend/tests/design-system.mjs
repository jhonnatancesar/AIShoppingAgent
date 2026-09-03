import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// `AlertDialog`/`Tooltip` usam `Portal` do Radix, que nunca aparece no HTML
// de `renderToStaticMarkup` (o portal só existe depois de montado no DOM
// real) -- por isso esses dois são validados na inspeção visual ao vivo em
// DEV (Subtask 12), não aqui. Este arquivo cobre os primitives cujo
// conteúdo é normal JSX renderizado em árvore.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { FormMessage } = await server.ssrLoadModule('/src/components/FormMessage.tsx')
  const { ToggleGroup } = await server.ssrLoadModule('/src/components/ui/toggle-group.tsx')
  const { Toast } = await server.ssrLoadModule('/src/components/ui/toast.tsx')
  const { Badge } = await server.ssrLoadModule('/src/components/ui/badge.tsx')
  const { ConditionBadge } = await server.ssrLoadModule('/src/components/ConditionBadge.tsx')

  // FormMessage: role correto por tom; string vazia/null não renderiza nada.
  const errorHtml = renderToStaticMarkup(React.createElement(FormMessage, { tone: 'error' }, 'Falhou'))
  assert.match(errorHtml, /role="alert"/)
  assert.match(errorHtml, /Falhou/)
  const successHtml = renderToStaticMarkup(React.createElement(FormMessage, { tone: 'success' }, 'Deu certo'))
  assert.match(successHtml, /role="status"/)
  const emptyHtml = renderToStaticMarkup(React.createElement(FormMessage, { tone: 'error' }, null))
  assert.equal(emptyHtml, '')

  // ToggleGroup single: só a opção selecionada tem aria-pressed="true".
  const singleHtml = renderToStaticMarkup(
    React.createElement(ToggleGroup, {
      type: 'single',
      value: 'bug',
      onChange: () => undefined,
      options: [
        { value: 'bug', label: 'Erro/Bug' },
        { value: 'support', label: 'Outro suporte' },
      ],
    }),
  )
  assert.match(singleHtml, /Erro\/Bug<\/button>/)
  const singlePressed = [...singleHtml.matchAll(/aria-pressed="(true|false)"/g)].map((m) => m[1])
  assert.deepEqual(singlePressed, ['true', 'false'])

  // ToggleGroup multiple: mais de uma opção pode estar marcada.
  const multipleHtml = renderToStaticMarkup(
    React.createElement(ToggleGroup, {
      type: 'multiple',
      value: ['amazon', 'kabum'],
      onChange: () => undefined,
      options: [
        { value: 'amazon', label: 'Amazon' },
        { value: 'kabum', label: 'KaBuM!' },
        { value: 'magalu', label: 'Magalu' },
      ],
    }),
  )
  const multiplePressed = [...multipleHtml.matchAll(/aria-pressed="(true|false)"/g)].map((m) => m[1])
  assert.deepEqual(multiplePressed, ['true', 'true', 'false'])

  // Toast: título/descrição presentes, role="status" (não interrompe o
  // leitor de tela como um alert faria).
  const toastHtml = renderToStaticMarkup(
    React.createElement(Toast, {
      toast: { id: '1', title: 'Enviado', description: 'Obrigado!', variant: 'success' },
      onDismiss: () => undefined,
    }),
  )
  assert.match(toastHtml, /role="status"/)
  assert.match(toastHtml, /Enviado/)
  assert.match(toastHtml, /Obrigado!/)

  // Badge: nova variante `warning` existe e `ConditionBadge` a usa para
  // condições notáveis (usado/recondicionado), sem reimplementar a marcação.
  const badgeHtml = renderToStaticMarkup(React.createElement(Badge, { variant: 'warning' }, 'Aviso'))
  assert.match(badgeHtml, /bg-warning/)
  const usedHtml = renderToStaticMarkup(React.createElement(ConditionBadge, { condition: 'used' }))
  assert.match(usedHtml, /bg-warning/)
  assert.match(usedHtml, /Usado/)
  const newHtml = renderToStaticMarkup(React.createElement(ConditionBadge, { condition: 'new' }))
  assert.doesNotMatch(newHtml, /bg-warning/)

  console.log('design system primitives render: passed')
} finally {
  await server.close()
}
