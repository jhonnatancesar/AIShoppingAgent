import { useEffect, useState } from 'react'
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
import { StoreMark } from '@/components/StoreMark'
import { getStoreVisual } from '@/components/storeVisuals'
import { cn } from '@/lib/utils'

const PERIOD_OPTIONS: { value: PriceHistoryPeriod; label: string }[] = [
  { value: '1d', label: '1 dia' },
  { value: '7d', label: '7 dias' },
  { value: '1m', label: '1 mês' },
  { value: '6m', label: '6 meses' },
  { value: '1a', label: '1 ano' },
  { value: 'all', label: 'Tudo' },
]

// Fallback só para um `store_code` fora das 6 lojas com marca conhecida em
// `storeVisuals.ts` (loja nova ainda sem visual cadastrado) -- escolhido por
// hash do PRÓPRIO código, nunca por posição na lista, para nunca mudar de
// cor quando a seleção de lojas do usuário muda (o que uma cor por índice
// faria toda vez que uma loja saísse/entrasse da lista visível).
const FALLBACK_COLORS = [
  'oklch(0.56 0.22 272)',
  'oklch(0.62 0.19 160)',
  'oklch(0.65 0.2 40)',
  'oklch(0.6 0.18 320)',
  'oklch(0.58 0.16 200)',
  'oklch(0.55 0.2 10)',
]

function hashCode(value: string): number {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) | 0
  }
  return Math.abs(hash)
}

// Cor estável por loja (mesma cor de marca usada em `StoreMark`/badges no
// resto do app) -- nunca por índice de posição na série, que mudaria toda
// vez que o filtro de lojas alterasse quais séries estão presentes.
function storeColor(storeCode: string): string {
  return getStoreVisual(storeCode)?.accent ?? FALLBACK_COLORS[hashCode(storeCode) % FALLBACK_COLORS.length]
}

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

// Corrige o "-0,00%": abaixo de 0,005 (o que arredondaria pra "-0,00" ou
// "0,00" com sinal de menos indevido) é tratado como zero puro, sem sinal.
// `signDisplay: 'exceptZero'` só evita "+"/"-" quando o valor já é
// exatamente 0 -- não cobre o caso de um negativo pequeno que arredonda
// para "-0,00" na exibição, por isso o tratamento explícito abaixo.
function percent(value: string | null): string {
  if (value === null) return 'Não informado'
  const numeric = Number(value)
  const rounded = Math.abs(numeric) < 0.005 ? 0 : numeric
  return new Intl.NumberFormat('pt-BR', {
    style: 'percent',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: 'exceptZero',
  }).format(rounded / 100)
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

export interface PriceHistoryStoreOption {
  id: string
  code: string
  name: string
}

// Filtro de lojas (redesenho do gráfico): multi-seleção com "Mostrar
// todas"/"Somente esta loja" -- `null` sempre significa "todas as lojas
// acessíveis", nunca "nenhuma", mesmo comportamento que a ausência do
// parâmetro `store_ids` já tinha no backend antes deste filtro existir.
function StoreFilter({
  stores,
  anchorStoreId,
  selected,
  onChange,
}: {
  stores: PriceHistoryStoreOption[]
  anchorStoreId: string
  selected: string[] | null
  onChange: (next: string[] | null) => void
}) {
  if (stores.length <= 1) return null
  const isAll = selected === null
  const isOnlyAnchor = selected !== null && selected.length === 1 && selected[0] === anchorStoreId

  function toggle(storeId: string) {
    const current = selected ?? stores.map((store) => store.id)
    const next = current.includes(storeId)
      ? current.filter((id) => id !== storeId)
      : [...current, storeId]
    // Nunca permite esvaziar para 0 lojas -- volta para "todas" em vez de
    // deixar o gráfico sem nenhuma série (estado sem utilidade nenhuma).
    onChange(next.length === 0 || next.length === stores.length ? null : next)
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Button size="sm" variant={isAll ? 'default' : 'outline'} onClick={() => onChange(null)}>
        Mostrar todas
      </Button>
      <Button
        size="sm"
        variant={isOnlyAnchor ? 'default' : 'outline'}
        onClick={() => onChange([anchorStoreId])}
      >
        Somente esta loja
      </Button>
      <span className="mx-1 h-4 w-px bg-border" aria-hidden="true" />
      {stores.map((store) => {
        const active = isAll || (selected?.includes(store.id) ?? false)
        return (
          <button
            key={store.id}
            type="button"
            onClick={() => toggle(store.id)}
            aria-pressed={active}
            className={cn(
              'flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors',
              active
                ? 'border-transparent bg-secondary text-secondary-foreground'
                : 'border-border text-muted-foreground opacity-60 hover:opacity-100',
            )}
          >
            <StoreMark store={store.code} className="h-4 w-4" />
            {store.name}
          </button>
        )
      })}
    </div>
  )
}

export function PriceHistoryChart({
  offerId,
  anchorStoreId,
  stores,
}: {
  offerId: string
  anchorStoreId: string
  stores: PriceHistoryStoreOption[]
}) {
  const [period, setPeriod] = useState<PriceHistoryPeriod>('1m')
  // `null` = todas as lojas acessíveis. Estado deliberadamente separado do
  // efeito de busca por período -- trocar de período NUNCA reseta a
  // seleção de lojas do usuário; só trocar de oferta (`offerId`) reseta.
  const [selectedStoreIds, setSelectedStoreIds] = useState<string[] | null>(null)
  const [history, setHistory] = useState<PriceHistoryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retryToken, setRetryToken] = useState(0)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  // Reset de estado ao trocar de prop sem efeito (padrão React: comparar
  // durante a própria renderização) -- evita o round-trip extra de um
  // `useEffect` só para isso.
  const [resetForOfferId, setResetForOfferId] = useState(offerId)
  if (offerId !== resetForOfferId) {
    setResetForOfferId(offerId)
    setSelectedStoreIds(null)
  }

  const storeIdsKey = selectedStoreIds ? [...selectedStoreIds].sort().join(',') : ''
  const currentKey = `${offerId}:${period}:${storeIdsKey}`

  useEffect(() => {
    let cancelled = false
    offersApi.priceHistory(offerId, period, selectedStoreIds ?? undefined).then(
      (loaded) => {
        if (cancelled) return
        setHistory(loaded)
        setError(null)
        setLoadedKey(currentKey)
      },
      (loadError) => {
        if (cancelled) return
        setHistory(null)
        setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar o histórico de preço.')
        setLoadedKey(currentKey)
      },
    )
    return () => { cancelled = true }
  }, [offerId, period, selectedStoreIds, retryToken, currentKey])

  const isCurrent = loadedKey === currentKey

  return (
    <div>
      <h2 className="mb-3 text-lg font-semibold tracking-tight">Histórico de preço</h2>
      <Card>
      <CardContent className="space-y-4 pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
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
        <StoreFilter
          stores={stores}
          anchorStoreId={anchorStoreId}
          selected={selectedStoreIds}
          onChange={setSelectedStoreIds}
        />
      </div>

      {isCurrent && error ? (
        <ErrorState
          title="Não foi possível carregar o histórico"
          description={error}
          onRetry={() => setRetryToken((token) => token + 1)}
        />
      ) : !isCurrent || !history ? (
        <LoadingState label="Carregando histórico de preço…" />
      ) : !history.comparable ? (
        <EmptyState
          title="Histórico indisponível"
          description="Esta oferta ainda não tem identidade de produto/variante resolvida entre lojas — o histórico fica disponível assim que ela for resolvida."
        />
      ) : history.series.length === 0 ? (
        <EmptyState
          title="Sem observações de preço"
          description="Ainda não há preço válido registrado neste período para nenhuma loja selecionada."
        />
      ) : (
        <>
          <ChartContainer>
            <LineChart data={buildChartData(history)}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tickFormatter={shortDate} />
              <YAxis
                domain={([min, max]: readonly [number, number]) => {
                  if (min === max) {
                    const padding = min === 0 ? 10 : Math.abs(min) * 0.1
                    return [min - padding, max + padding]
                  }
                  return [min * 0.95, max * 1.05]
                }}
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
                cursor={{ stroke: 'var(--border)', strokeWidth: 1 }}
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
              {history.series.map((series) => {
                const color = storeColor(series.store_code)
                const isolated = series.points.length === 1
                return (
                  <Line
                    key={series.store_id}
                    type="monotone"
                    dataKey={series.store_code}
                    stroke={color}
                    strokeWidth={2}
                    dot={isolated ? { r: 5, fill: color, strokeWidth: 0 } : false}
                    activeDot={{ r: 5 }}
                    connectNulls
                  />
                )
              })}
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
                <dd
                  className={cn(
                    'font-semibold',
                    history.metrics.variation_percent !== null && Number(history.metrics.variation_percent) < 0
                      ? 'text-success'
                      : undefined,
                  )}
                >
                  {percent(history.metrics.variation_percent)}
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
