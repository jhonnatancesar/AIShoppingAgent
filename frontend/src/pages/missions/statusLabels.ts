import type { MissionStatus, MissionStatusFilter } from '../../api/types'

export const STATUS_LABELS: Record<MissionStatus, string> = {
  draft: 'Rascunho',
  active: 'Ativa',
  paused: 'Pausada',
  completed: 'Concluída',
  cancelled: 'Cancelada',
  expired: 'Expirada',
}

/** Variant do `Badge` (Subtask 12) por status real de missão -- única
 * fonte da cor, reaproveitada por `MissionCard` e `MissionDetailPage`. */
export const STATUS_BADGE_VARIANT: Record<
  MissionStatus,
  'default' | 'secondary' | 'outline' | 'destructive' | 'warning' | 'success'
> = {
  draft: 'outline',
  active: 'success',
  paused: 'warning',
  completed: 'secondary',
  cancelled: 'destructive',
  expired: 'outline',
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
  magalu: 'Magalu',
  mercadolivre: 'Mercado Livre',
}
