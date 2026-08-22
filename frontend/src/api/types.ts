export type UserRole = 'USER' | 'ADMIN' | 'DEV'

export interface WebSessionUser {
  id: string
  display_name: string
  username: string | null
  role: UserRole
}

// TASK-092 (item 2 da V1.2): mesmo vocabulário de
// `app.missions.models.MissionStatus`/`MissionCommand` (backend).
export type MissionStatus =
  | 'draft'
  | 'active'
  | 'paused'
  | 'completed'
  | 'cancelled'
  | 'expired'

export type MissionCommand =
  | 'activate'
  | 'pause'
  | 'resume'
  | 'complete'
  | 'cancel'
  | 'expire'

export type MissionStatusFilter =
  | 'active'
  | 'paused'
  | 'cancelled'
  | 'completed'
  | 'expired'
  | 'all'

export interface MissionSummary {
  id: string
  title: string
  status: MissionStatus
  state_version: number
  created_at: string
  updated_at: string
  expires_at: string | null
}

export interface MissionListResponse {
  items: MissionSummary[]
  limit: number
  offset: number
  total: number
}

export interface MissionCriteria {
  search_query: string
  model: string | null
  target_amount: string | null
  target_currency: string | null
}

export interface MissionSourceOut {
  store_code: string
  store_name: string
}

export interface MissionSchedule {
  interval_minutes: number
  next_run_at: string
  last_run_at: string | null
  is_enabled: boolean
}

export interface MissionTransitionOut {
  from_status: MissionStatus
  to_status: MissionStatus
  command: MissionCommand
  actor_type: string
  reason: string | null
  transitioned_at: string
}

export interface MissionDetail extends MissionSummary {
  criteria: MissionCriteria | null
  sources: MissionSourceOut[]
  schedule: MissionSchedule | null
  transitions: MissionTransitionOut[]
}
