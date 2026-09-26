import { useEffect, useState } from 'react'
import { LoaderCircle, Search } from 'lucide-react'
import { ApiError } from '@/api/client'
import { offersApi } from '@/api/offers'
import type { HistoricalPriceResponse } from '@/api/types'
import { FormMessage } from '@/components/FormMessage'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatDate } from '@/lib/formatDateTime'

// A busca roda em segundo plano no backend (cada leitura de página pode
// levar minutos no César Core) -- a tela só consulta o estado até sair de
// `in_progress`, com um teto para nunca consultar para sempre.
const POLL_INTERVAL_MS = 4000
const MAX_POLLS = 75

type Feedback = { tone: 'success' | 'neutral' | 'error'; text: string } | null

function money(value: string, currency: string) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(Number(value))
}

/** `"2026-03-12"` (data pura, sem fuso) -> `"12/03/2026"`. `formatDate`
 * converteria de UTC para o fuso de Brasília e voltaria um dia. */
function formatDayOnly(value: string): string {
  const [year, month, day] = value.split('-')
  return year && month && day ? `${day}/${month}/${year}` : value
}

function sourceLabel(url: string, fallback: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return fallback
  }
}

function outcomeFeedback(data: HistoricalPriceResponse): Feedback {
  switch (data.search.last_status) {
    case 'completed_with_references':
      return { tone: 'success', text: 'Preço histórico atualizado.' }
    case 'completed_without_references':
      return { tone: 'neutral', text: 'A busca terminou, mas nenhum preço histórico de referência foi encontrado.' }
    case 'failed':
      return { tone: 'error', text: 'A busca falhou. Tente de novo mais tarde.' }
    default:
      return null
  }
}

/** TASK-127: bloco "Preço histórico" do detalhe da oferta -- mostra o que
 * já existe no banco na hora (sem ninguém clicar) e o botão de busca
 * manual com a regra decidida pelo dono do produto: sem busca recente,
 * qualquer um busca; dentro dos 90 dias, só DEV, com confirmação. */
export function HistoricalPriceSection({ offerId }: { offerId: string }) {
  const [data, setData] = useState<HistoricalPriceResponse | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)

  useEffect(() => {
    let cancelled = false
    offersApi.historicalPrice(offerId).then(
      (response) => {
        if (cancelled) return
        setData(response)
        setLoadError(null)
      },
      (error) => {
        if (cancelled) return
        setLoadError(error instanceof ApiError ? error.message : 'Não foi possível carregar o preço histórico.')
      },
    )
    return () => { cancelled = true }
  }, [offerId])

  const inProgress = data?.search.availability === 'in_progress'

  useEffect(() => {
    if (!inProgress) return
    let cancelled = false
    let polls = 0
    const timer = window.setInterval(() => {
      polls += 1
      if (polls > MAX_POLLS) {
        window.clearInterval(timer)
        setFeedback({ tone: 'neutral', text: 'A busca está demorando mais que o normal. Volte daqui a pouco para ver o resultado.' })
        return
      }
      offersApi.historicalPrice(offerId).then(
        (response) => {
          if (cancelled || !response) return
          setData(response)
          if (response.search.availability !== 'in_progress') setFeedback(outcomeFeedback(response))
        },
        () => undefined,
      )
    }, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [inProgress, offerId])

  async function runSearch(force: boolean) {
    setSubmitting(true)
    setFeedback(null)
    try {
      const response = await offersApi.searchHistoricalPrice(offerId, force)
      if (response) setData(response)
    } catch (error) {
      setFeedback({ tone: 'error', text: error instanceof ApiError ? error.message : 'Não foi possível iniciar a busca.' })
      // 409 = o estado mudou (outro clique, busca recente) -- ressincroniza.
      const refreshed = await offersApi.historicalPrice(offerId).catch(() => null)
      if (refreshed) setData(refreshed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card className="mt-6">
      <CardHeader><CardTitle className="text-base">Preço histórico</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        {loadError ? (
          <FormMessage tone="error">{loadError}</FormMessage>
        ) : !data ? (
          <p className="text-sm text-muted-foreground">Carregando preço histórico…</p>
        ) : (
          <>
            <dl className="divide-y divide-border border-t border-border text-sm">
              <div className="grid gap-1 py-2.5 sm:grid-cols-[13rem_1fr] sm:items-baseline sm:gap-3">
                <dt className="text-muted-foreground">Menor registrado no GG</dt>
                <dd className="m-0">
                  {data.internal ? (
                    <span className="font-semibold">{money(data.internal.amount, data.internal.currency)}</span>
                  ) : (
                    <span className="text-muted-foreground">Ainda sem preço registrado</span>
                  )}
                </dd>
              </div>
              <div className="grid gap-1 py-2.5 sm:grid-cols-[13rem_1fr] sm:items-baseline sm:gap-3">
                <dt className="text-muted-foreground">Histórico de referência</dt>
                <dd className="m-0">
                  {data.reference ? (
                    <>
                      <span className="font-semibold">{money(data.reference.amount, data.reference.currency)}</span>
                      <span className="block text-xs text-muted-foreground">
                        (<a className="underline-offset-2 hover:underline" href={data.reference.source_url} target="_blank" rel="noreferrer">{sourceLabel(data.reference.source_url, data.reference.source)}</a>
                        {data.reference.historical_date ? `, ${formatDayOnly(data.reference.historical_date)}` : ''})
                      </span>
                    </>
                  ) : (
                    <span className="text-muted-foreground">Ainda sem referência externa</span>
                  )}
                </dd>
              </div>
            </dl>
            <SearchControl data={data} submitting={submitting} onSearch={runSearch} />
            {feedback ? <FormMessage tone={feedback.tone}>{feedback.text}</FormMessage> : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function SearchControl({
  data,
  submitting,
  onSearch,
}: {
  data: HistoricalPriceResponse
  submitting: boolean
  onSearch: (force: boolean) => void
}) {
  const { availability, last_completed_at: lastCompletedAt, next_allowed_at: nextAllowedAt } = data.search

  if (availability === 'disabled') return null
  if (availability === 'no_identity') {
    return <p className="text-sm text-muted-foreground">Produto ainda sem identidade reconhecida — não dá para buscar preço histórico com segurança.</p>
  }
  if (availability === 'in_progress') {
    return (
      <div className="space-y-2">
        <Button disabled><LoaderCircle className="animate-spin" />Buscando…</Button>
        <p className="text-sm text-muted-foreground">Buscando no hardwarebarato.com e na internet — pode levar alguns minutos.</p>
      </div>
    )
  }
  if (availability === 'blocked_recent') {
    return (
      <div className="space-y-2">
        <Button disabled variant="outline"><Search />Buscar preço histórico</Button>
        <p className="text-sm text-muted-foreground">
          Já pesquisado em {formatDate(lastCompletedAt)} — nova busca liberada a partir de {formatDate(nextAllowedAt)}.
        </p>
      </div>
    )
  }
  if (availability === 'requires_force') {
    return (
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button variant="outline" disabled={submitting}><Search />Buscar preço histórico</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Pesquisar mesmo assim?</AlertDialogTitle>
            <AlertDialogDescription>
              O preço ainda está no limite de 90 dias (última busca em {formatDate(lastCompletedAt)}; nova busca liberada em {formatDate(nextAllowedAt)}). Pesquisar agora gasta busca e IA de novo.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={() => onSearch(true)}>Pesquisar mesmo assim</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    )
  }
  return (
    <Button variant="outline" disabled={submitting} onClick={() => onSearch(false)}>
      {submitting ? <LoaderCircle className="animate-spin" /> : <Search />}Buscar preço histórico
    </Button>
  )
}
