/** Padrão oficial da interface para preço-alvo: pt-BR (milhar `.`,
 * decimal `,`, sempre 2 casas quando o valor vem carregado/salvo) --
 * nunca o decimal técnico que a API usa/devolve. Aceita como entrada
 * qualquer um dos formatos abaixo (`null` se não for numérico):
 * "1999.00000" (bruto da API), "1999", "1999,5", "1999,99", "1.999,99".
 * Com vírgula presente, qualquer ponto é separador de milhar (removido);
 * sem vírgula, tolera o decimal técnico com ponto sem complicar. */
function parsePriceInput(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const normalized = trimmed.includes(',') ? trimmed.replaceAll('.', '').replace(',', '.') : trimmed
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

/** Formata para o padrão visual oficial pt-BR (ex.: `1999.00000`/`1999`/
 * `1999,5` → `1.999,00`/`1.999,00`/`1.999,50`) -- usado no prefill do
 * formulário de Editar e ao sair do campo (`onBlur`). Valor não numérico
 * (campo vazio, ou ainda incompleto enquanto o usuário digita) é
 * devolvido sem alteração, nunca força um formato no meio da digitação. */
export function formatPriceDisplay(value: string): string {
  const parsed = parsePriceInput(value)
  return parsed === null
    ? value
    : new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(parsed)
}

/** Converte o valor exibido (qualquer formato aceito por
 * `parsePriceInput`) para o decimal técnico que a API espera (ponto,
 * sem separador de milhar) -- só na hora de montar o payload, nunca
 * durante a digitação. */
export function toApiDecimal(value: string): string {
  const parsed = parsePriceInput(value)
  return parsed === null ? value.trim() : parsed.toFixed(2)
}
