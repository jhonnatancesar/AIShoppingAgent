import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { missionsApi } from '../../api/missions'
import type { MissionStatusFilter, MissionSummary } from '../../api/types'
import { STATUS_FILTER_LABELS, STATUS_LABELS } from './statusLabels'

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
      <div className="page-header">
        <h1>Missões</h1>
        <Link className="button" to="/app/missions/new">
          + Nova missão
        </Link>
      </div>

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

      {error ? <p className="form-error">{error}</p> : null}

      {missions === null ? (
        <p className="loading">Carregando…</p>
      ) : missions.length === 0 ? (
        <p className="empty-state">Nenhuma missão encontrada para este filtro.</p>
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
