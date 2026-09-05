import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { formatDate, formatDateTime } = await server.ssrLoadModule('/src/lib/formatDateTime.ts')

  // UTC -> horário de Brasília (America/Sao_Paulo, -03:00, sem horário de
  // verão desde 2019) -- exemplo do pedido de correção (TASK-120).
  assert.equal(formatDateTime('2026-09-05T14:32:18.123Z'), '05/09/2026 11:32')

  // ISO com offset explícito (não só "Z") também é aceito.
  assert.equal(formatDateTime('2026-09-05T00:00:00-03:00'), '05/09/2026 00:00')

  // Mudança de DATA causada pelo fuso: 02:59 UTC é 23:59 do dia anterior
  // em Brasília -- o dia exibido precisa refletir isso, nunca o dia UTC.
  assert.equal(formatDateTime('2026-09-05T02:59:00Z'), '04/09/2026 23:59')

  // Formatação pt-BR: dd/mm/aaaa HH:mm, sem vírgula entre data e hora
  // (Intl.DateTimeFormat com dateStyle+timeStyle insere vírgula por
  // padrão -- o helper monta a string manualmente para evitar isso).
  assert.equal(formatDateTime('2026-01-15T12:00:00Z'), '15/01/2026 09:00')
  assert.doesNotMatch(formatDateTime('2026-09-05T14:32:18.123Z'), /,/)

  // Meia-noite em Brasília formata como "00:00", nunca "24:00".
  assert.equal(formatDateTime('2026-01-01T02:30:00Z'), '31/12/2025 23:30')

  // Valor ausente (contrato permite null/undefined): placeholder padrão
  // do produto, nunca "Invalid Date".
  assert.equal(formatDateTime(null), '—')
  assert.equal(formatDateTime(undefined), '—')
  assert.equal(formatDateTime(''), '—')

  // Valor malformado: mesmo placeholder, sem tentar "corrigir"
  // silenciosamente nem deixar "Invalid Date" vazar pra tela.
  assert.equal(formatDateTime('nao-e-uma-data'), '—')
  assert.doesNotMatch(formatDateTime('nao-e-uma-data'), /Invalid Date/)

  // formatDate: variante só-data (dd/mm/aaaa), mesmo fuso.
  assert.equal(formatDate('2026-09-05T02:59:00Z'), '04/09/2026')
  assert.equal(formatDate(null), '—')
  assert.equal(formatDate('nao-e-uma-data'), '—')

  console.log('format date time (formatDate/formatDateTime): passed')
} finally {
  await server.close()
}
