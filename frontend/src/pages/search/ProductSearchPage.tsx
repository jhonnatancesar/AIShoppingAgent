import { useEffect, useState, type FormEvent } from 'react'
import { Search, Target } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { accountApi } from '@/api/account'
import { ApiError } from '@/api/client'
import { missionsApi } from '@/api/missions'
import { searchApi } from '@/api/search'
import type { AccountQuota, ProductSearchOffer, ProductSearchResponse, QuotaErrorDetails } from '@/api/types'
import { FormMessage } from '@/components/FormMessage'
import { OfferCard, type OfferCardData } from '@/components/OfferCard'
import { PageHeader } from '@/components/PageHeader'
import { QuotaExceededNotice } from '@/components/QuotaExceededNotice'
import { quotaDetailsFromError } from '@/components/quotaDetails'
import { QuotaUsageRow } from '@/components/QuotaSummary'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ToggleGroup } from '@/components/ui/toggle-group'
import { StoreName } from '@/components/StoreMark'

const STORES = [
  { code: 'amazon', label: 'Amazon' },
  { code: 'kabum', label: 'KaBuM!' },
  { code: 'magalu', label: 'Magalu' },
  { code: 'mercadolivre', label: 'Mercado Livre' },
  { code: 'pichau', label: 'Pichau' },
  { code: 'terabyte', label: 'Terabyte' },
] as const

export function ProductSearchPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get('q') || '')
  const [selectedStores, setSelectedStores] = useState<string[]>(STORES.map((store) => store.code))
  const [result, setResult] = useState<ProductSearchResponse | null>(null)
  const [selectedVariants, setSelectedVariants] = useState<string[]>([])
  const [selectAllVariants, setSelectAllVariants] = useState(false)
  const [selectedGenericOffer, setSelectedGenericOffer] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const [monitoring, setMonitoring] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [quotaError, setQuotaError] = useState<QuotaErrorDetails | null>(null)
  const [quota, setQuota] = useState<AccountQuota | null>(null)

  async function loadQuota() {
    try {
      const loaded = await accountApi.getQuota()
      if (loaded) setQuota(loaded)
    } catch {
      // TASK-107: uso diário é só informativo aqui -- falha ao carregar
      // não deve impedir a pesquisa em si.
    }
  }

  useEffect(() => {
    let cancelled = false
    accountApi.getQuota().then((loaded) => {
      if (!cancelled && loaded) setQuota(loaded)
    }, () => {
      // TASK-107: uso diário é só informativo aqui -- falha ao carregar
      // não deve impedir a pesquisa em si.
    })
    return () => { cancelled = true }
  }, [])

  async function runSearch(value: string, stores: string[]) {
    if (!value.trim() || stores.length === 0) return
    setSearching(true); setError(null); setQuotaError(null); setResult(null)
    try {
      const response = await searchApi.search(value.trim(), stores)
      if (!response) throw new Error('Resposta inesperada do servidor.')
      setResult(response); setSelectedVariants([]); setSelectAllVariants(false); setSelectedGenericOffer(null)
      void loadQuota()
    } catch (searchError) {
      if (searchError instanceof ApiError) {
        const details = quotaDetailsFromError(searchError)
        if (details) {
          // TASK-107: pesquisa recusada por cota diária -- mensagem clara
          // de que a quota acabou, nunca "não foi possível pesquisar agora".
          setQuotaError(details)
          void loadQuota()
        }
        setError(searchError.message)
      } else {
        setError('Não foi possível pesquisar agora.')
      }
    } finally { setSearching(false) }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setParams({ q: query.trim() })
    runSearch(query, selectedStores)
  }

  function toggleStore(codes: string[]) {
    setResult(null)
    setSelectedStores(codes)
  }

  function updateQuery(value: string) {
    setQuery(value)
    setResult(null)
  }

  async function monitor() {
    if (!result) return
    const genericOffer = result.offers.find((offer) => offer.offer_id === selectedGenericOffer)
    const monitorQuery = result.request_kind === 'generic_category' && genericOffer ? genericOffer.title : result.query
    setMonitoring(true); setError(null)
    try {
      const mission = await missionsApi.create({
        search_query: monitorQuery,
        source_codes: selectedStores,
        variant_product_ids: result.request_kind === 'product_family' && !selectAllVariants ? selectedVariants : [],
        select_all_variants: result.request_kind === 'product_family' && selectAllVariants,
      })
      if (!mission) throw new Error('Resposta inesperada do servidor.')
      navigate(`/app/missions/${mission.id}`)
    } catch (monitorError) {
      setError(monitorError instanceof ApiError ? monitorError.message : 'Não foi possível criar a missão.')
    } finally { setMonitoring(false) }
  }

  const canMonitor = result && result.offers.length > 0 && (
    result.request_kind !== 'product_family' || selectAllVariants || selectedVariants.length > 0
  )

  return (
    <section>
      <PageHeader eyebrow="Busca em várias lojas" title="O que você está procurando?" description="Compare o que já encontramos. Nada entra no seu radar até você escolher Monitorar." />
      {quota ? (
        <div className="mb-5 max-w-xs">
          <QuotaUsageRow label="Pesquisas hoje" item={quota.daily_searches} />
        </div>
      ) : null}
      <SearchForm query={query} setQuery={updateQuery} selectedStores={selectedStores} toggleStore={toggleStore} searching={searching} onSubmit={submit} />
      {quotaError ? (
        <div className="mt-4"><QuotaExceededNotice message={error ?? ''} details={quotaError} /></div>
      ) : null}
      <div className="mt-7">
        {searching ? <LoadingState label="Procurando nas ofertas disponíveis…" /> : error && !result && !quotaError ? <ErrorState title="Não foi possível fazer a busca" description={error} /> : result ? (
          result.offers.length === 0 ? <EmptyState title="Ainda não encontramos esse produto" description="Tente um nome mais curto, outro modelo ou selecione mais lojas. Fique tranquilo: nenhuma missão foi criada." /> : <>
            <ResultHeading result={result} />
            {result.request_kind === 'product_family' ? <VariantSelection result={result} selected={selectedVariants} setSelected={setSelectedVariants} selectAll={selectAllVariants} setSelectAll={setSelectAllVariants} /> : null}
            {result.request_kind === 'generic_category' ? <GenericChoice result={result} selectedOffer={selectedGenericOffer} setSelectedOffer={setSelectedGenericOffer} /> : null}
            <OfferGrid offers={result.offers} selectable={result.request_kind === 'generic_category'} selectedOffer={selectedGenericOffer} setSelectedOffer={setSelectedGenericOffer} />
            <div className="mt-4"><FormMessage tone="error">{error}</FormMessage></div>
            <div className="sticky bottom-4 z-10 mt-6 flex flex-col items-start justify-between gap-3 rounded-xl border border-border bg-card/95 p-4 shadow-xl backdrop-blur sm:flex-row sm:items-center"><div><p className="text-sm font-medium">Quer acompanhar esta busca?</p><p className="text-xs text-muted-foreground">Crie um alerta para receber as próximas oportunidades encontradas.</p></div><Button size="lg" disabled={!canMonitor || monitoring} onClick={monitor}><Target />{monitoring ? 'Criando missão…' : 'Criar alerta'}</Button></div>
          </>
        ) : null}
      </div>
    </section>
  )
}

function SearchForm({ query, setQuery, selectedStores, toggleStore, searching, onSubmit }: { query: string; setQuery: (value: string) => void; selectedStores: string[]; toggleStore: (codes: string[]) => void; searching: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return (
    <Card className="border-primary/20">
      <CardContent className="p-5 sm:p-6">
        <form onSubmit={onSubmit} className="space-y-5">
          <div className="relative">
            <Search className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input id="product-search" aria-label="Produto, modelo ou categoria" className="h-12 pl-10 text-base" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ex.: Samsung Galaxy S24 Ultra 512 GB" required />
          </div>
          <ToggleGroup className="grid grid-cols-2 sm:grid-cols-3" type="multiple" aria-label="Lojas" value={selectedStores} onChange={toggleStore} options={STORES.map((store) => ({ value: store.code, label: <StoreName store={store.code}>{store.label}</StoreName> }))} />
          <Button disabled={searching || !query.trim() || selectedStores.length === 0}><Search />{searching ? 'Pesquisando…' : 'Pesquisar'}</Button>
        </form>
      </CardContent>
    </Card>
  )
}

function ResultHeading({ result }: { result: ProductSearchResponse }) {
  const labels = { specific_product: 'Produto específico', product_family: 'Linha de produtos', generic_category: 'Busca mais ampla' }
  const countLabel = result.offers.length === 1 ? '1 oferta encontrada' : `${result.offers.length} ofertas encontradas`
  return <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-lg font-semibold tracking-tight">Resultados para “{result.query}”</h2><p className="text-sm text-muted-foreground">{countLabel}. Nenhuma missão foi criada.</p></div><Badge>{labels[result.request_kind]}</Badge></div>
}

function VariantSelection({ result, selected, setSelected, selectAll, setSelectAll }: { result: ProductSearchResponse; selected: string[]; setSelected: (value: string[]) => void; selectAll: boolean; setSelectAll: (value: boolean) => void }) {
  return <Card className="mb-4"><CardHeader><CardTitle className="text-base">Qual versão você quer acompanhar?</CardTitle><CardDescription>Escolha um modelo específico ou deixe todas as versões no radar.</CardDescription></CardHeader><CardContent className="grid gap-2 sm:grid-cols-2"><label className="flex cursor-pointer gap-3 rounded-lg border p-3 text-sm"><input type="checkbox" checked={selectAll} onChange={(event) => { setSelectAll(event.target.checked); if (event.target.checked) setSelected([]) }} />Todas as versões encontradas</label>{result.variants.map((variant) => <label key={variant.product_id} className="flex cursor-pointer gap-3 rounded-lg border p-3 text-sm"><input type="checkbox" disabled={selectAll} checked={selected.includes(variant.product_id)} onChange={() => setSelected(selected.includes(variant.product_id) ? selected.filter((id) => id !== variant.product_id) : [...selected, variant.product_id])} /><span><strong className="block">{variant.label}</strong>{Object.keys(variant.attributes).length ? <small className="text-muted-foreground">{Object.values(variant.attributes).join(' · ')}</small> : null}</span></label>)}</CardContent></Card>
}

function GenericChoice({ result, selectedOffer, setSelectedOffer }: { result: ProductSearchResponse; selectedOffer: string | null; setSelectedOffer: (value: string | null) => void }) {
  return <Card className="mb-4"><CardContent className="flex flex-wrap items-center gap-3 pt-6"><span className="text-sm font-medium">Monitorar:</span><Button size="sm" variant={selectedOffer === null ? 'default' : 'outline'} onClick={() => setSelectedOffer(null)}>A categoria "{result.query}"</Button><span className="text-xs text-muted-foreground">ou selecione um resultado abaixo</span></CardContent></Card>
}

function OfferGrid({ offers, selectable, selectedOffer, setSelectedOffer }: { offers: ProductSearchOffer[]; selectable: boolean; selectedOffer: string | null; setSelectedOffer: (value: string) => void }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {offers.map((offer, index) => (
        <OfferCard
          key={offer.offer_id}
          index={index}
          offer={toCardData(offer)}
          action={{ label: 'Ver na loja', href: offer.original_url }}
          selected={selectable && selectedOffer === offer.offer_id}
          onSelect={selectable ? () => setSelectedOffer(offer.offer_id) : undefined}
        />
      ))}
    </div>
  )
}

function toCardData(offer: ProductSearchOffer): OfferCardData {
  return {
    id: offer.offer_id,
    title: offer.title,
    imageUrl: offer.image_url,
    store: { name: offer.store_name },
    price: { amount: offer.amount, totalAmount: offer.total_amount, currency: offer.currency },
    condition: offer.condition,
    rating: offer.rating_average !== null && offer.review_count !== null ? { average: offer.rating_average, reviewCount: offer.review_count } : null,
  }
}
