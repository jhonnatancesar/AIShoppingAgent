import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { ApiError } from '../../api/client'
import { missionsApi } from '../../api/missions'
import type { MissionListResponse, MissionStatusFilter } from '../../api/types'
import { STATUS_FILTER_LABELS } from './statusLabels'
import { MissionCard } from '@/components/MissionCard'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const PAGE_SIZE = 20
const DEFAULT_FILTER_VALUE = 'default'
const FILTER_OPTIONS: MissionStatusFilter[] = ['active', 'paused', 'cancelled', 'completed', 'expired', 'all']

export function MissionsListPage() {
  const [filter, setFilter] = useState<MissionStatusFilter | null>(null)
  const [offset, setOffset] = useState(0)
  const [result, setResult] = useState<MissionListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (currentFilter: MissionStatusFilter | null, currentOffset: number) => {
    setError(null)
    try {
      setResult(await missionsApi.list(currentFilter, PAGE_SIZE, currentOffset))
    } catch (loadError) {
      setResult(null)
      setError(
        loadError instanceof ApiError
          ? loadError.message
          : 'Não foi possível carregar as missões.',
      )
    }
  }, [])

  useEffect(() => {
    load(filter, offset)
  }, [filter, offset, load])

  function changeFilter(value: string) {
    setFilter(value === DEFAULT_FILTER_VALUE ? null : (value as MissionStatusFilter))
    setOffset(0)
  }

  return (
    <section>
      <PageHeader eyebrow="Monitoramento" title="Missões" description="O que você pediu para o GG Oferta acompanhar -- produto, preço-alvo, lojas e o que já foi encontrado." actions={<Button asChild><Link to="/app/missions/new"><Plus />Nova missão</Link></Button>} />

      <div className="mb-5 max-w-xs space-y-1.5">
        <label className="text-sm font-medium" htmlFor="status-filter">Status</label>
        <Select value={filter ?? DEFAULT_FILTER_VALUE} onValueChange={changeFilter}>
          <SelectTrigger id="status-filter"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={DEFAULT_FILTER_VALUE}>Ativas + pausadas (padrão)</SelectItem>
            {FILTER_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>{STATUS_FILTER_LABELS[option]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {error ? (
        <ErrorState title="Não foi possível carregar as missões" description={error} onRetry={() => load(filter, offset)} />
      ) : !result ? (
        <LoadingState label="Carregando missões…" />
      ) : (
        <MissionsListView result={result} onPage={setOffset} />
      )}
    </section>
  )
}

export function MissionsListView({ result, onPage = () => undefined }: { result: MissionListResponse; onPage?: (offset: number) => void }) {
  if (result.items.length === 0) return <EmptyState title="Nenhuma missão encontrada" description="Experimente outro filtro ou crie sua primeira missão." action={<Button asChild><Link to="/app/missions/new">Criar missão</Link></Button>} />
  const end = Math.min(result.offset + result.items.length, result.total)
  return (
    <>
      <div className="mb-4 flex items-center justify-between text-sm text-muted-foreground">
        <span>{result.total} missão(ões)</span>
        <span>{result.offset + 1}–{end} de {result.total}</span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {result.items.map((mission) => <MissionCard key={mission.id} mission={mission} />)}
      </div>
      <div className="mt-7 flex justify-center gap-2">
        <Button variant="outline" disabled={result.offset === 0} onClick={() => onPage(Math.max(0, result.offset - result.limit))}>Anterior</Button>
        <Button variant="outline" disabled={result.offset + result.limit >= result.total} onClick={() => onPage(result.offset + result.limit)}>Próxima</Button>
      </div>
    </>
  )
}
