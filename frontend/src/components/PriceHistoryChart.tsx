import { useCallback, useEffect, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ApiError } from '@/api/client'
import { offersApi } from '@/api/offers'
import type { PriceHistoryPeriod, PriceHistoryResponse } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { ChartContainer } from '@/components/ui/chart'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'

const PERIOD_OPTIONS: { value: PriceHistoryPeriod; label: string }[] = [
  { value: '1d', label: '1 dia' },
  { value: '7d', label: '7 dias' },
  { value: '1m', label: '1 mês' },
  { value: '6m', label: '6 meses' },
  { value: '1a', label: '1 ano' },
  { value: 'all', label: 'Tudo' },
]

// Ciclado por índice de loja -- sem paleta `--chart-*` definida no design
// system ainda; cores distintas o bastante para até 6 lojas (V1).
const LINE_COLORS = [
  'oklch(0.56 0.22 272)',
  'oklch(0.62 0.19 160)',
  'oklch(0.65 0.2 40)',
  'oklch(0.6 0.18 320)',
  'oklch(0.58 0.16 200)',
  'oklch(0.55 0.2 10)',
]

function money(value: string | null, currency: string | null) {
  if (value === null || currency === null) return 'Não informado'
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(
    Number(value),
  )
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit' }).format(
    new Date(`${value}T00:00:00`),
  )
}

type ChartRow = { date: string } & Record<string, string | number>

function buildChartData(history: PriceHistoryResponse): ChartRow[] {
  const byDate = new Map<string, ChartRow>()
  for (const series of history.series) {
    for (const point of series.points) {
      const row: ChartRow = byDate.get(point.date) ?? { date: point.date }
      row[series.store_code] = Number(point.amount)
      byDate.set(point.date, row)
    }
  }
  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date))
}

export function PriceHistoryChart({ offerId }: { offerId: string }) {
  const [period, setPeriod] = useState<PriceHistoryPeriod>('1m')
  const [history, setHistory] = useState<PriceHistoryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    setHistory(null)
    try {
      setHistory(await offersApi.priceHistory(offerId, period))
    } catch (loadError) {
      setError(
        loadError instanceof ApiError
          ? loadError.message
          : 'Não foi possível carregar o histórico de preço.',
      )
    }
  }, [offerId, period])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div>
      <h2 className="mb-3 text-lg font-semibold tracking-tight">Histórico de preço</h2>
      <Card>
      <CardContent className="space-y-4 pt-6">
      <div className="flex flex-wrap gap-2">
        {PERIOD_OPTIONS.map((option) => (
          <Button
            key={option.value}
            size="sm"
            variant={option.value === period ? 'default' : 'outline'}
            onClick={() => setPeriod(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      {error ? (
        <ErrorState
          title="Não foi possível carregar o histórico"
          description={error}
          onRetry={load}
        />
      ) : !history ? (
        <LoadingState label="Carregando histórico de preço…" />
      ) : !history.comparable ? (
        <EmptyState
          title="Histórico indisponível"
          description="Esta oferta ainda não tem identidade de produto/variante resolvida entre lojas — o histórico fica disponível assim que ela for resolvida."
        />
      ) : history.series.length === 0 ? (
        <EmptyState
          title="Sem observações de preço"
          description="Ainda não há preço válido registrado neste período para nenhuma loja acessível deste produto."
        />
      ) : (
        <>
          <ChartContainer>
            <LineChart data={buildChartData(history)}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tickFormatter={shortDate} />
              <YAxis
                domain={[(min: number) => min * 0.95, (max: number) => max * 1.05]}
                tickFormatter={(value: number) =>
                  new Intl.NumberFormat('pt-BR', { notation: 'compact' }).format(value)
                }
              />
              <Tooltip
                labelFormatter={(label) => shortDate(String(label))}
                formatter={(value, name) => [
                  money(String(value), history.currency),
                  history.series.find((series) => series.store_code === name)
                    ?.store_name ?? String(name),
                ]}
                contentStyle={{
                  background: 'var(--popover)',
                  color: 'var(--popover-foreground)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius)',
                }}
                labelStyle={{ color: 'var(--popover-foreground)' }}
                itemStyle={{ color: 'var(--popover-foreground)' }}
              />
              <Legend
                formatter={(value) =>
                  history.series.find((series) => series.store_code === value)
                    ?.store_name ?? String(value)
                }
              />
              {history.series.map((series, index) => (
                <Line
                  key={series.store_id}
                  type="monotone"
                  dataKey={series.store_code}
                  stroke={LINE_COLORS[index % LINE_COLORS.length]}
                  dot={series.points.length === 1}
                  strokeWidth={2}
                />
              ))}
            </LineChart>
          </ChartContainer>

          {history.metrics ? (
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <div>
                <dt className="text-xs text-muted-foreground">Atual</dt>
                <dd className="font-semibold">
                  {money(history.metrics.current_amount, history.currency)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Mínimo</dt>
                <dd className="font-semibold">
                  {money(history.metrics.min_amount, history.currency)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Máximo</dt>
                <dd className="font-semibold">
                  {money(history.metrics.max_amount, history.currency)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Média</dt>
                <dd className="font-semibold">
                  {money(history.metrics.average_amount, history.currency)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Variação no período</dt>
                <dd className="font-semibold">
                  {history.metrics.variation_percent === null
                    ? 'Não informado'
                    : `${history.metrics.variation_percent}%`}
                </dd>
              </div>
            </dl>
          ) : null}
        </>
      )}
      </CardContent>
      </Card>
    </div>
  )
}
