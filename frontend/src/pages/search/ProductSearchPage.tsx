import { useState, type FormEvent } from 'react'
import { motion } from 'motion/react'
import { ExternalLink, Eye, Search, ShoppingBag, Target } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { missionsApi } from '@/api/missions'
import { searchApi } from '@/api/search'
import type { ProductSearchOffer, ProductSearchResponse } from '@/api/types'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

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

  async function runSearch(value: string, stores: string[]) {
    if (!value.trim() || stores.length === 0) return
    setSearching(true); setError(null); setResult(null)
    try {
      const response = await searchApi.search(value.trim(), stores)
      if (!response) throw new Error('Resposta inesperada do servidor.')
      setResult(response); setSelectedVariants([]); setSelectAllVariants(false); setSelectedGenericOffer(null)
    } catch (searchError) {
      setError(searchError instanceof ApiError ? searchError.message : 'Não foi possível pesquisar agora.')
    } finally { setSearching(false) }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setParams({ q: query.trim() })
    runSearch(query, selectedStores)
  }

  function toggleStore(code: string) {
    setResult(null)
    setSelectedStores((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code])
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
      <PageHeader eyebrow="Pesquisa multiloja" title="O que você procura?" description="Pesquise e explore resultados livremente. Uma missão só será criada quando você escolher Monitorar." />
      <SearchForm query={query} setQuery={updateQuery} selectedStores={selectedStores} toggleStore={toggleStore} searching={searching} onSubmit={submit} />
      <div className="mt-7">
        {searching ? <LoadingState label="Consultando ofertas já encontradas…" /> : error && !result ? <ErrorState title="Pesquisa indisponível" description={error} /> : result ? (
          result.offers.length === 0 ? <EmptyState title="Nenhum resultado conhecido" description="Ainda não há ofertas persistidas que correspondam com segurança a esta pesquisa. Pesquisar não criou nenhuma missão." /> : <>
            <ResultHeading result={result} />
            {result.request_kind === 'product_family' ? <VariantSelection result={result} selected={selectedVariants} setSelected={setSelectedVariants} selectAll={selectAllVariants} setSelectAll={setSelectAllVariants} /> : null}
            {result.request_kind === 'generic_category' ? <GenericChoice result={result} selectedOffer={selectedGenericOffer} setSelectedOffer={setSelectedGenericOffer} /> : null}
            <OfferGrid offers={result.offers} selectable={result.request_kind === 'generic_category'} selectedOffer={selectedGenericOffer} setSelectedOffer={setSelectedGenericOffer} />
            {error ? <p className="form-error mt-4" role="alert">{error}</p> : null}
            <div className="sticky bottom-4 z-10 mt-6 flex flex-col items-start justify-between gap-3 rounded-xl border border-border bg-card/95 p-4 shadow-xl backdrop-blur sm:flex-row sm:items-center"><div><p className="text-sm font-medium">Quer acompanhar esta escolha?</p><p className="text-xs text-muted-foreground">Só esta ação criará uma missão ativa.</p></div><Button size="lg" disabled={!canMonitor || monitoring} onClick={monitor}><Target />{monitoring ? 'Criando missão…' : 'Monitorar'}</Button></div>
          </>
        ) : null}
      </div>
    </section>
  )
}

function SearchForm({ query, setQuery, selectedStores, toggleStore, searching, onSubmit }: { query: string; setQuery: (value: string) => void; selectedStores: string[]; toggleStore: (code: string) => void; searching: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return <Card className="border-primary/20"><CardContent className="p-5 sm:p-6"><form onSubmit={onSubmit} className="space-y-5"><div className="relative"><Search className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="product-search" aria-label="Produto, modelo ou categoria" className="h-12 pl-10 text-base" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ex.: Samsung Galaxy S24 Ultra 512 GB" required /></div><div className="flex flex-wrap gap-2">{STORES.map((store) => <label key={store.code} className={`cursor-pointer rounded-full border px-3 py-1.5 text-xs font-medium ${selectedStores.includes(store.code) ? 'border-primary/40 bg-primary/10 text-primary' : 'text-muted-foreground'}`}><input className="sr-only" type="checkbox" checked={selectedStores.includes(store.code)} onChange={() => toggleStore(store.code)} />{store.label}</label>)}</div><Button disabled={searching || !query.trim() || selectedStores.length === 0}><Search />{searching ? 'Pesquisando…' : 'Pesquisar'}</Button></form></CardContent></Card>
}

function ResultHeading({ result }: { result: ProductSearchResponse }) {
  const labels = { specific_product: 'Produto específico', product_family: 'Família de produtos', generic_category: 'Categoria genérica' }
  return <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-lg font-semibold">Resultados para “{result.query}”</h2><p className="text-sm text-muted-foreground">{result.offers.length} oferta(s) conhecida(s), sem criar missão.</p></div><Badge>{labels[result.request_kind]}</Badge></div>
}

function VariantSelection({ result, selected, setSelected, selectAll, setSelectAll }: { result: ProductSearchResponse; selected: string[]; setSelected: (value: string[]) => void; selectAll: boolean; setSelectAll: (value: boolean) => void }) {
  return <Card className="mb-4"><CardHeader><CardTitle className="text-base">Escolha as variantes</CardTitle><CardDescription>Variantes permanecem separadas pela identidade determinística da TASK-097.</CardDescription></CardHeader><CardContent className="grid gap-2 sm:grid-cols-2"><label className="flex cursor-pointer gap-3 rounded-lg border p-3 text-sm"><input type="checkbox" checked={selectAll} onChange={(event) => { setSelectAll(event.target.checked); if (event.target.checked) setSelected([]) }} />Todas as variantes encontradas</label>{result.variants.map((variant) => <label key={variant.product_id} className="flex cursor-pointer gap-3 rounded-lg border p-3 text-sm"><input type="checkbox" disabled={selectAll} checked={selected.includes(variant.product_id)} onChange={() => setSelected(selected.includes(variant.product_id) ? selected.filter((id) => id !== variant.product_id) : [...selected, variant.product_id])} /><span><strong className="block">{variant.label}</strong>{Object.keys(variant.attributes).length ? <small className="text-muted-foreground">{Object.values(variant.attributes).join(' · ')}</small> : null}</span></label>)}</CardContent></Card>
}

function GenericChoice({ result, selectedOffer, setSelectedOffer }: { result: ProductSearchResponse; selectedOffer: string | null; setSelectedOffer: (value: string | null) => void }) {
  return <Card className="mb-4"><CardContent className="flex flex-wrap items-center gap-3 pt-6"><span className="text-sm font-medium">Monitorar:</span><Button size="sm" variant={selectedOffer === null ? 'default' : 'outline'} onClick={() => setSelectedOffer(null)}>A categoria “{result.query}”</Button><span className="text-xs text-muted-foreground">ou selecione um resultado abaixo</span></CardContent></Card>
}

function OfferGrid({ offers, selectable, selectedOffer, setSelectedOffer }: { offers: ProductSearchOffer[]; selectable: boolean; selectedOffer: string | null; setSelectedOffer: (value: string) => void }) {
  return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{offers.map((offer, index) => <motion.div key={offer.offer_id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(index * .03, .2) }}><Card className={`flex h-full flex-col overflow-hidden ${selectable && selectedOffer === offer.offer_id ? 'ring-2 ring-primary' : ''}`}><button type="button" disabled={!selectable} onClick={() => setSelectedOffer(offer.offer_id)} className="grid h-40 w-full place-items-center bg-muted/40 p-4 text-left disabled:cursor-default">{offer.image_url ? <img className="h-full w-full object-contain" src={offer.image_url} alt="" /> : <ShoppingBag className="size-9 text-muted-foreground/40" />}</button><CardHeader className="flex-1"><div className="flex items-center justify-between"><Badge variant="secondary">{offer.store_name}</Badge>{offer.rating_average !== null ? <span className="text-xs text-muted-foreground">★ {Number(offer.rating_average).toLocaleString('pt-BR')}</span> : null}</div><CardTitle className="line-clamp-2 text-base">{offer.title}</CardTitle><p className="text-xl font-semibold">{money(offer.amount, offer.currency)}</p><CardDescription>Total {money(offer.total_amount, offer.currency)} · {conditionLabel(offer.condition)}</CardDescription>{selectable ? <p className="flex items-center gap-1 text-xs text-primary"><Eye className="size-3" />Clique no card para escolher este produto</p> : null}</CardHeader><CardFooter><Button variant="outline" className="w-full" asChild><a href={offer.original_url} target="_blank" rel="noreferrer">Ver na loja <ExternalLink /></a></Button></CardFooter></Card></motion.div>)}</div>
}

function money(value: string, currency: string) { return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(Number(value)) }
function conditionLabel(condition: ProductSearchOffer['condition']) { return condition === 'new' ? 'Novo' : condition === 'used' ? 'Usado' : condition === 'refurbished' ? 'Recondicionado' : 'Condição não informada' }
