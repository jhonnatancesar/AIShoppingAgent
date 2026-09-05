import { useEffect, useState, type FormEvent } from 'react'
import { Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { offersApi, type OfferListFilters } from '@/api/offers'
import type { OfferListResponse } from '@/api/types'
import { FilterBar } from '@/components/FilterBar'
import { OfferCard } from '@/components/OfferCard'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Button } from '@/components/ui/button'
import { offerSummaryToCardData } from './offerCardMapping'

const PAGE_SIZE = 24

export function OffersListPage() {
  const [draftQuery, setDraftQuery] = useState('')
  const [filters, setFilters] = useState<OfferListFilters>({ sort: 'recent', limit: PAGE_SIZE, offset: 0 })
  const [result, setResult] = useState<OfferListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Só para o botão "Tentar novamente" pedir a mesma busca de novo, sem
  // chamar a busca por referência de dentro do efeito.
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    offersApi.list(filters).then(
      (response) => {
        if (cancelled) return
        setResult(response)
        setError(null)
      },
      (loadError) => {
        if (cancelled) return
        setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar as ofertas.')
      },
    )
    return () => { cancelled = true }
  }, [filters, retryToken])

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
      <PageHeader eyebrow="Boas oportunidades" title="Ofertas" description="Os preços que mais combinam com o que você colocou no radar, organizados para comparar sem confusão." actions={<Button asChild><Link to="/app/search"><Search />Buscar produto</Link></Button>} />
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
      {error && !result ? <ErrorState title="Ofertas indisponíveis" description={error} onRetry={() => setRetryToken((token) => token + 1)} /> : !result ? <LoadingState label="Carregando suas ofertas…" /> : <OffersListView result={result} onPage={(offset) => setFilters((current) => ({ ...current, offset }))} />}
    </section>
  )
}

export function OffersListView({ result, onPage = () => undefined }: { result: OfferListResponse; onPage?: (offset: number) => void }) {
  if (result.items.length === 0) return <EmptyState title="Nenhuma oferta por aqui ainda" description="Tente outros filtros ou busque um produto para colocar uma nova missão no radar." action={<Button asChild><Link to="/app/search">Buscar produtos</Link></Button>} />
  const end = Math.min(result.offset + result.items.length, result.total)
  return (
    <>
      <div className="mb-4 flex items-center justify-between text-sm text-muted-foreground">
        <span>{result.total} oferta(s)</span>
        <span>{result.offset + 1}–{end} de {result.total}</span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {result.items.map((offer, index) => <OfferCard key={offer.id} offer={offerSummaryToCardData(offer)} action={{ label: 'Ver detalhes', to: `/app/offers/${offer.id}` }} index={index} />)}
      </div>
      <div className="mt-7 flex justify-center gap-2">
        <Button variant="outline" disabled={result.offset === 0} onClick={() => onPage(Math.max(0, result.offset - result.limit))}>Anterior</Button>
        <Button variant="outline" disabled={result.offset + result.limit >= result.total} onClick={() => onPage(result.offset + result.limit)}>Próxima</Button>
      </div>
    </>
  )
}
