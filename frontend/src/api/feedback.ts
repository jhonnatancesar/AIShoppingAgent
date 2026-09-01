import { api } from './client'

export type FeedbackKind = 'bug' | 'support' | 'store_suggestion'

export interface FeedbackCreateRequest {
  kind: FeedbackKind
  message?: string | null
  store_name?: string | null
  store_url?: string | null
}

export interface FeedbackCreated {
  id: string
  status: 'new' | 'reviewed' | 'closed'
}

export const feedbackApi = {
  submit: (payload: FeedbackCreateRequest) =>
    api.post<FeedbackCreated>('/feedback', payload),
}
