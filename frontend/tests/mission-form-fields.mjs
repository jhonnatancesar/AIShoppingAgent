import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { toApiDecimal, formatPriceDisplay } = await server.ssrLoadModule('/src/pages/missions/MissionFormFields.tsx')

  // formatPriceDisplay: padrão oficial da interface -- pt-BR completo
  // (milhar `.`, decimal `,`, sempre 2 casas), usado no prefill do
  // formulário de Editar e no onBlur do campo. Exemplos exatos pedidos
  // na correção de requisito.
  assert.equal(formatPriceDisplay('1999.00000'), '1.999,00')
  assert.equal(formatPriceDisplay('1999'), '1.999,00')
  assert.equal(formatPriceDisplay('1999,5'), '1.999,50')
  assert.equal(formatPriceDisplay('1999,99'), '1.999,99')
  assert.equal(formatPriceDisplay('1.999,99'), '1.999,99')
  // Vazio/não numérico: devolvido sem alteração (não força formato no
  // meio da digitação nem quebra em valor inválido).
  assert.equal(formatPriceDisplay(''), '')
  assert.equal(formatPriceDisplay('abc'), 'abc')

  // toApiDecimal: sempre decimal técnico com ponto para a API, a partir
  // de qualquer um dos formatos acima.
  assert.equal(toApiDecimal('999,90'), '999.90')
  assert.equal(toApiDecimal('1.999,99'), '1999.99')
  assert.equal(toApiDecimal('999.90'), '999.90')
  assert.equal(toApiDecimal('  4500,00  '), '4500.00')
  assert.equal(toApiDecimal('1999.00000'), '1999.00')

  console.log('mission form fields (formatPriceDisplay/toApiDecimal): passed')
} finally {
  await server.close()
}
