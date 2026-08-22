import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { motion } from 'motion/react'
import { ArrowRight, Filter, Search, ShoppingBag } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { offersApi, type OfferListFilters } from '@/api/offers'
import type { OfferCondition, OfferListResponse, OfferSummary } from '@/api/types'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

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

  return (
    <section>
      <PageHeader eyebrow="Sua área" title="Ofertas" description="Todas as ofertas relevantes ligadas às suas missões, sem duplicar anúncios entre missões." actions={<Button asChild><Link to="/app/search"><Search />Pesquisar</Link></Button>} />
      <form onSubmit={submit} className="mb-6 grid gap-3 rounded-xl border border-border bg-card p-4 shadow-card lg:grid-cols-[minmax(14rem,1fr)_repeat(4,minmax(8rem,auto))_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Buscar nas ofertas" className="pl-9" value={draftQuery} onChange={(event) => setDraftQuery(event.target.value)} placeholder="Buscar produto" /></div>
        <FilterSelect label="Loja" value={filters.store || ''} onChange={(value) => updateFilter('store', value)} options={[['amazon', 'Amazon'], ['kabum', 'KaBuM!'], ['magalu', 'Magalu'], ['pichau', 'Pichau'], ['terabyte', 'Terabyte']]} />
        <FilterSelect label="Condição" value={filters.condition || ''} onChange={(value) => updateFilter('condition', value)} options={[['new', 'Novo'], ['refurbished', 'Recondicionado'], ['used', 'Usado'], ['unknown', 'Não identificada']]} />
        <FilterSelect label="Disponibilidade" value={filters.availability || ''} onChange={(value) => updateFilter('availability', value)} options={[['available', 'Disponível'], ['unavailable', 'Indisponível'], ['unknown', 'Não confirmada']]} />
        <FilterSelect label="Ordenar" value={filters.sort || 'recent'} onChange={(value) => updateFilter('sort', value)} includeAll={false} options={[['recent', 'Mais recentes'], ['price_asc', 'Menor preço'], ['price_desc', 'Maior preço']]} />
        <Button variant="outline"><Filter />Aplicar</Button>
      </form>
      {error && !result ? <ErrorState title="Ofertas indisponíveis" description={error} onRetry={load} /> : !result ? <LoadingState label="Carregando suas ofertas…" /> : <OffersListView result={result} onPage={(offset) => setFilters((current) => ({ ...current, offset }))} />}
    </section>
  )
}

function FilterSelect({ label, value, onChange, options, includeAll = true }: { label: string; value: string; onChange: (value: string) => void; options: [string, string][]; includeAll?: boolean }) {
  return <label><span className="sr-only">{label}</span><select className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm text-foreground" aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>{includeAll ? <option value="">{label}: todas</option> : null}{options.map(([optionValue, text]) => <option key={optionValue} value={optionValue}>{text}</option>)}</select></label>
}

export function OffersListView({ result, onPage = () => undefined }: { result: OfferListResponse; onPage?: (offset: number) => void }) {
  if (result.items.length === 0) return <EmptyState title="Nenhuma oferta encontrada" description="Crie ou ajuste uma missão para começar a receber ofertas relevantes." action={<Button asChild><Link to="/app/search">Pesquisar produtos</Link></Button>} />
  const end = Math.min(result.offset + result.items.length, result.total)
  return <><div className="mb-4 flex items-center justify-between text-sm text-muted-foreground"><span>{result.total} oferta(s)</span><span>{result.offset + 1}–{end} de {result.total}</span></div><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{result.items.map((offer, index) => <OfferCard key={offer.id} offer={offer} index={index} />)}</div><div className="mt-7 flex justify-center gap-2"><Button variant="outline" disabled={result.offset === 0} onClick={() => onPage(Math.max(0, result.offset - result.limit))}>Anterior</Button><Button variant="outline" disabled={result.offset + result.limit >= result.total} onClick={() => onPage(result.offset + result.limit)}>Próxima</Button></div></>
}

function OfferCard({ offer, index }: { offer: OfferSummary; index: number }) {
  const observation = offer.latest_observation
  return <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(index * .025, .2) }}><Card className="flex h-full flex-col overflow-hidden transition-transform hover:-translate-y-0.5"><div className="grid h-44 place-items-center bg-muted/40 p-4">{offer.image_url ? <img className="h-full w-full object-contain" src={offer.image_url} alt="" /> : <ShoppingBag className="size-10 text-muted-foreground/35" />}</div><CardHeader className="flex-1"><div className="flex items-center justify-between gap-2"><Badge variant="secondary">{offer.store.name}</Badge>{offer.rating ? <span className="text-xs text-muted-foreground">★ {Number(offer.rating.average).toLocaleString('pt-BR')}</span> : null}</div><CardTitle className="line-clamp-2 text-base leading-snug">{offer.title}</CardTitle>{observation ? <><p className="text-xl font-semibold tracking-tight">{money(observation.amount, observation.currency)}</p><CardDescription>Total {money(observation.total_amount, observation.currency)} · {conditionLabel(observation.condition)}</CardDescription></> : <CardDescription>Dados comerciais ainda não disponíveis.</CardDescription>}{offer.seller ? <p className="text-xs text-muted-foreground">Vendido por {offer.seller.name}</p> : null}</CardHeader><CardFooter><Button variant="outline" className="w-full" asChild><Link to={`/app/offers/${offer.id}`}>Ver detalhes <ArrowRight /></Link></Button></CardFooter></Card></motion.div>
}

function money(value: string, currency: string) { return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(Number(value)) }
function conditionLabel(condition: OfferCondition) { return condition === 'new' ? 'Novo' : condition === 'used' ? 'Usado' : condition === 'refurbished' ? 'Recondicionado' : 'Condição não identificada' }
