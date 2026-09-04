import type { FormEvent } from 'react'
import { Filter, Search, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export type OfferFilterKey = 'store' | 'condition' | 'availability' | 'sort'

interface FilterBarProps {
  draftQuery: string
  onDraftQueryChange: (value: string) => void
  onSearchSubmit: (event: FormEvent<HTMLFormElement>) => void
  store: string
  condition: string
  availability: string
  sort: string
  onChange: (key: OfferFilterKey, value: string) => void
  onClearAdvanced: () => void
}

const STORE_OPTIONS: [string, string][] = [
  ['amazon', 'Amazon'], ['kabum', 'KaBuM!'], ['magalu', 'Magalu'],
  ['mercadolivre', 'Mercado Livre'], ['pichau', 'Pichau'], ['terabyte', 'Terabyte'],
]
const CONDITION_OPTIONS: [string, string][] = [
  ['new', 'Novo'], ['refurbished', 'Recondicionado'], ['used', 'Usado'], ['unknown', 'Não identificada'],
]
const AVAILABILITY_OPTIONS: [string, string][] = [
  ['available', 'Disponível'], ['unavailable', 'Indisponível'], ['unknown', 'Não confirmada'],
]
const SORT_OPTIONS: [string, string][] = [
  ['recent', 'Mais recentes'], ['price_asc', 'Menor preço'], ['price_desc', 'Maior preço'],
]

/** Barra de filtros das Ofertas (Subtask 13): busca + Loja + Ordenar sempre
 * visíveis (os controles mais usados); Condição/Disponibilidade atrás de
 * "Mais filtros" (menos usados no dia a dia) -- mesmo `Popover` em desktop
 * e mobile, responsivo via `max-w-[calc(100vw-2rem)]` em `ui/popover.tsx`.
 * Nenhum filtro novo: os 5 campos já existem hoje, só reorganizados. */
export function FilterBar({ draftQuery, onDraftQueryChange, onSearchSubmit, store, condition, availability, sort, onChange, onClearAdvanced }: FilterBarProps) {
  const activeAdvancedCount = [condition, availability].filter(Boolean).length

  return (
    <form onSubmit={onSearchSubmit} className="mb-6 flex flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-card sm:flex-row sm:flex-wrap sm:items-center">
      <div className="relative flex-1 sm:min-w-[14rem]">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input aria-label="Buscar nas ofertas" className="pl-9" value={draftQuery} onChange={(event) => onDraftQueryChange(event.target.value)} placeholder="Buscar produto" />
      </div>
      <div className="flex flex-wrap gap-3 sm:flex-nowrap">
        <div className="w-[9.5rem]">
          <FilterSelect label="Loja" value={store} onChange={(value) => onChange('store', value)} options={STORE_OPTIONS} />
        </div>
        <div className="w-[9.5rem]">
          <FilterSelect label="Ordenar" value={sort || 'recent'} onChange={(value) => onChange('sort', value)} includeAll={false} options={SORT_OPTIONS} />
        </div>
        <Popover>
          <PopoverTrigger asChild>
            <Button type="button" variant="outline">
              <Filter />Mais filtros
              {activeAdvancedCount > 0 ? <Badge className="ml-1">{activeAdvancedCount}</Badge> : null}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start">
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold">Mais filtros</p>
                {activeAdvancedCount > 0 ? (
                  <Button type="button" variant="ghost" size="sm" onClick={onClearAdvanced}><X />Limpar</Button>
                ) : null}
              </div>
              <div className="space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">Condição</span>
                <FilterSelect label="Condição" value={condition} onChange={(value) => onChange('condition', value)} options={CONDITION_OPTIONS} />
              </div>
              <div className="space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">Disponibilidade</span>
                <FilterSelect label="Disponibilidade" value={availability} onChange={(value) => onChange('availability', value)} options={AVAILABILITY_OPTIONS} />
              </div>
            </div>
          </PopoverContent>
        </Popover>
        <Button type="submit" variant="outline"><Search />Buscar</Button>
      </div>
    </form>
  )
}

function FilterSelect({ label, value, onChange, options, includeAll = true }: { label: string; value: string; onChange: (value: string) => void; options: [string, string][]; includeAll?: boolean }) {
  return (
    <Select value={value || 'all'} onValueChange={(next) => onChange(next === 'all' ? '' : next)}>
      <SelectTrigger aria-label={label} className="w-full"><SelectValue placeholder={label} /></SelectTrigger>
      <SelectContent>
        {includeAll ? <SelectItem value="all">{label}: todas</SelectItem> : null}
        {options.map(([optionValue, text]) => <SelectItem key={optionValue} value={optionValue}>{text}</SelectItem>)}
      </SelectContent>
    </Select>
  )
}
