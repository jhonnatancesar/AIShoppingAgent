import type { PriceHistoryResponse } from '@/api/types'

export type ChartRow = { date: string } & Record<string, string | number>

// TASK-125: o ponto de cada loja fica no preço COM cupom quando havia
// cupom naquele dia ("tudo na mesma linha", decisão do usuário); o preço
// de tabela e o cupom usado viajam na MESMA linha de dados, em chaves
// próprias, só para o tooltip -- nunca viram uma linha nova no gráfico.
export function originalKey(storeCode: string) {
  return `${storeCode}__original`
}

export function couponKey(storeCode: string) {
  return `${storeCode}__coupon`
}

export function couponLabel(code: string): string {
  return code.trim() ? code.trim() : 'automático (sem código)'
}

export function buildChartData(history: PriceHistoryResponse): ChartRow[] {
  const byDate = new Map<string, ChartRow>()
  for (const series of history.series) {
    for (const point of series.points) {
      const row: ChartRow = byDate.get(point.date) ?? { date: point.date }
      row[series.store_code] = Number(point.amount)
      if (point.coupon_code !== undefined && point.coupon_code !== null && point.original_amount) {
        row[originalKey(series.store_code)] = Number(point.original_amount)
        row[couponKey(series.store_code)] = point.coupon_code
      }
      byDate.set(point.date, row)
    }
  }
  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date))
}
