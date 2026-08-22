/**
 * Cliente das missões da aplicação web (TASK-092, item 2 da V1.2). Só
 * chama os endpoints `/api/v1/missions*` -- nenhuma regra de negócio no
 * cliente, tudo validado pelo backend.
 */
import { api } from './client'
import type {
  MissionDetail,
  MissionListResponse,
  MissionStatusFilter,
  MissionSummary,
} from './types'

export interface CreateMissionInput {
  search_query: string
  model?: string | null
  title?: string | null
  target_amount?: string | null
  target_currency?: string | null
  source_codes?: string[]
  variant_product_ids?: string[]
  select_all_variants?: boolean
}

export interface EditMissionInput {
  expected_state_version: number
  target_amount?: string | null
  target_currency?: string | null
  clear_target?: boolean
  source_codes?: string[] | null
}

export interface SelectMissionVariantsInput {
  expected_state_version: number
  product_ids?: string[]
  select_all?: boolean
}

export const missionsApi = {
  list: (status: MissionStatusFilter | null, limit = 20, offset = 0) => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    params.set('limit', String(limit))
    params.set('offset', String(offset))
    return api.get<MissionListResponse>(`/missions?${params.toString()}`)
  },
  get: (missionId: string) => api.get<MissionDetail>(`/missions/${missionId}`),
  create: (input: CreateMissionInput) =>
    api.post<MissionSummary>('/missions', input),
  edit: (missionId: string, input: EditMissionInput) =>
    api.patch<MissionSummary>(`/missions/${missionId}`, input),
  selectVariants: (missionId: string, input: SelectMissionVariantsInput) =>
    api.put<MissionSummary>(`/missions/${missionId}/variants`, input),
  pause: (missionId: string, expectedStateVersion: number) =>
    api.post<MissionSummary>(`/missions/${missionId}/pause`, {
      expected_state_version: expectedStateVersion,
    }),
  resume: (missionId: string, expectedStateVersion: number) =>
    api.post<MissionSummary>(`/missions/${missionId}/resume`, {
      expected_state_version: expectedStateVersion,
    }),
  cancel: (missionId: string, expectedStateVersion: number) =>
    api.post<MissionSummary>(`/missions/${missionId}/cancel`, {
      expected_state_version: expectedStateVersion,
    }),
}
