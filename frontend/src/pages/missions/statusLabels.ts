import type { MissionStatus, MissionStatusFilter } from '../../api/types'

export const STATUS_LABELS: Record<MissionStatus, string> = {
  draft: 'Rascunho',
  active: 'Ativa',
  paused: 'Pausada',
  completed: 'Concluída',
  cancelled: 'Cancelada',
  expired: 'Expirada',
}

export const STATUS_FILTER_LABELS: Record<MissionStatusFilter, string> = {
  active: 'Ativas',
  paused: 'Pausadas',
  cancelled: 'Canceladas',
  completed: 'Concluídas',
  expired: 'Expiradas',
  all: 'Todas',
}

export const STORE_LABELS: Record<string, string> = {
  pichau: 'Pichau',
  terabyte: 'Terabyte',
  amazon: 'Amazon',
  kabum: 'KaBuM!',
}
