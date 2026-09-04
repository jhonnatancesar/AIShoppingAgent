import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { offersApi, type OfferListFilters } from '@/api/offers'
import type { OfferListResponse, OfferSummary } from '@/api/types'
import { FilterBar } from '@/components/FilterBar'
import { OfferCard } from '@/components/OfferCard'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Button } from '@/components/ui/button'

const PAGE_SIZE = 24

export function OffersListPage() {
  const [draftQuery, setDraftQuery] = useState('')
  const [filters, setFilters] = useState<OfferListFilters>({ sort: 'recent', limit: PAGE_SIZE, offset: 0 })
  const [result, setResult] = useState<OfferListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      setResult(await offersApi.list(filters))
    } catch (loadError) {
      setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar as ofertas.')
    }
  }, [filters])

  useEffect(() => { load() }, [load])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFilters((current) => ({ ...current, q: draftQuery.trim() || undefined, offset: 0 }))
  }

  function updateFilter(key: keyof OfferListFilters, value: string) {
    setFilters((current) => ({ ...current, [key]: value || undefined, offset: 0 }))
  }

  function clearAdvancedFilters() {
    setFilters((current) => ({ ...current, condition: undefined, availability: undefined, offset: 0 }))
  }

  return (
    <section>
      <PageHeader eyebrow="Sua área" title="Ofertas" description="Todas as ofertas relevantes ligadas às suas missões, sem duplicar anúncios entre missões." actions={<Button asChild><Link to="/app/search"><Search />Pesquisar</Link></Button>} />
      <FilterBar
        draftQuery={draftQuery}
        onDraftQueryChange={setDraftQuery}
        onSearchSubmit={submit}
        store={filters.store || ''}
        condition={filters.condition || ''}
        availability={filters.availability || ''}
        sort={filters.sort || 'recent'}
        onChange={updateFilter}
        onClearAdvanced={clearAdvancedFilters}
      />
      {error && !result ? <ErrorState title="Ofertas indisponíveis" description={error} onRetry={load} /> : !result ? <LoadingState label="Carregando suas ofertas…" /> : <OffersListView result={result} onPage={(offset) => setFilters((current) => ({ ...current, offset }))} />}
    </section>
  )
}

export function OffersListView({ result, onPage = () => undefined }: { result: OfferListResponse; onPage?: (offset: number) => void }) {
  if (result.items.length === 0) return <EmptyState title="Nenhuma oferta encontrada" description="Crie ou ajuste uma missão para começar a receber ofertas relevantes, ou tente outros filtros." action={<Button asChild><Link to="/app/search">Pesquisar produtos</Link></Button>} />
  const end = Math.min(result.offset + result.items.length, result.total)
  return (
    <>
      <div className="mb-4 flex items-center justify-between text-sm text-muted-foreground">
        <span>{result.total} oferta(s)</span>
        <span>{result.offset + 1}–{end} de {result.total}</span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {result.items.map((offer, index) => <OfferCard key={offer.id} offer={toCardData(offer)} action={{ label: 'Ver detalhes', to: `/app/offers/${offer.id}` }} index={index} />)}
      </div>
      <div className="mt-7 flex justify-center gap-2">
        <Button variant="outline" disabled={result.offset === 0} onClick={() => onPage(Math.max(0, result.offset - result.limit))}>Anterior</Button>
        <Button variant="outline" disabled={result.offset + result.limit >= result.total} onClick={() => onPage(result.offset + result.limit)}>Próxima</Button>
      </div>
    </>
  )
}

function toCardData(offer: OfferSummary) {
  return {
    id: offer.id,
    title: offer.title,
    imageUrl: offer.image_url,
    imageFallbackUrl: offer.image_fallback_url,
    store: offer.store,
    price: offer.latest_observation
      ? { amount: offer.latest_observation.amount, totalAmount: offer.latest_observation.total_amount, currency: offer.latest_observation.currency }
      : null,
    condition: offer.latest_observation?.condition ?? null,
    seller: offer.seller,
    rating: offer.rating ? { average: offer.rating.average, reviewCount: offer.rating.review_count } : null,
  }
}
