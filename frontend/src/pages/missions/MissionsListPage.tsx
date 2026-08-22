import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { missionsApi } from '../../api/missions'
import type { MissionStatusFilter, MissionSummary } from '../../api/types'
import { STATUS_FILTER_LABELS, STATUS_LABELS } from './statusLabels'
import { Plus } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Button } from '@/components/ui/button'

const FILTER_OPTIONS: (MissionStatusFilter | null)[] = [
  null,
  'active',
  'paused',
  'cancelled',
  'completed',
  'expired',
  'all',
]

export function MissionsListPage() {
  const [filter, setFilter] = useState<MissionStatusFilter | null>(null)
  const [missions, setMissions] = useState<MissionSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (currentFilter: MissionStatusFilter | null) => {
    setError(null)
    try {
      const response = await missionsApi.list(currentFilter)
      setMissions(response?.items ?? [])
    } catch (loadError) {
      setMissions([])
      setError(
        loadError instanceof ApiError
          ? loadError.message
          : 'Não foi possível carregar as missões.',
      )
    }
  }, [])

  useEffect(() => {
    load(filter)
  }, [filter, load])

  return (
    <section>
      <PageHeader eyebrow="Monitoramento" title="Missões" description="Gerencie produtos, lojas e preços-alvo em um só lugar." actions={<Button asChild><Link to="/app/missions/new"><Plus />Nova missão</Link></Button>} />

      <div className="field mission-filter">
        <label htmlFor="status-filter">Status</label>
        <select
          id="status-filter"
          value={filter ?? ''}
          onChange={(event) =>
            setFilter((event.target.value || null) as MissionStatusFilter | null)
          }
        >
          <option value="">Ativas + pausadas (padrão)</option>
          {FILTER_OPTIONS.filter((option): option is MissionStatusFilter => option !== null).map(
            (option) => (
              <option key={option} value={option}>
                {STATUS_FILTER_LABELS[option]}
              </option>
            ),
          )}
        </select>
      </div>

      {error ? <ErrorState title="Não foi possível carregar as missões" description={error} onRetry={() => load(filter)} /> : missions === null ? (
        <LoadingState label="Carregando missões…" />
      ) : missions.length === 0 ? (
        <EmptyState title="Nenhuma missão encontrada" description="Experimente outro filtro ou crie sua primeira missão." action={<Button asChild><Link to="/app/missions/new">Criar missão</Link></Button>} />
      ) : (
        <ul className="mission-list">
          {missions.map((mission) => (
            <li key={mission.id}>
              <Link className="mission-card" to={`/app/missions/${mission.id}`}>
                <span>{mission.title}</span>
                <span className={`status-badge status-${mission.status}`}>
                  {STATUS_LABELS[mission.status]}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
