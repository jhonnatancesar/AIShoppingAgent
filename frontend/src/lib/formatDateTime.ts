/** Formatação de data/hora para exibição (TASK-120) -- padrão pt-BR,
 * sempre no fuso de Brasília (`America/Sao_Paulo`), nunca o fuso local
 * do navegador/máquina. O backend continua enviando/armazenando
 * timestamps técnicos (ISO 8601, UTC ou com offset) normalmente; esta
 * conversão é só de apresentação.
 *
 * `Intl.DateTimeFormat('pt-BR', { dateStyle, timeStyle })` insere uma
 * vírgula entre data e hora (`"05/09/2026, 11:32"`) -- o padrão exigido
 * aqui é `"05/09/2026 11:32"` (sem vírgula), então data e hora são
 * formatadas separadamente e unidas com espaço. */

const TIME_ZONE = 'America/Sao_Paulo'
const LOCALE = 'pt-BR'
const EMPTY_PLACEHOLDER = '—'

const DATE_FORMATTER = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TIME_ZONE,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
})

const TIME_FORMATTER = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

function parseValidDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `"05/09/2026"` -- só a data, sem hora. `null`/`undefined`/valor
 * inválido devolvem o placeholder padrão do produto (`—`), nunca
 * `"Invalid Date"`. */
export function formatDate(value: string | null | undefined): string {
  const date = parseValidDate(value)
  return date ? DATE_FORMATTER.format(date) : EMPTY_PLACEHOLDER
}

/** `"05/09/2026 11:32"` -- data + hora (sem segundos), fuso de
 * Brasília. `null`/`undefined`/valor inválido devolvem o placeholder
 * padrão do produto (`—`), nunca `"Invalid Date"`. */
export function formatDateTime(value: string | null | undefined): string {
  const date = parseValidDate(value)
  if (!date) return EMPTY_PLACEHOLDER
  return `${DATE_FORMATTER.format(date)} ${TIME_FORMATTER.format(date)}`
}
