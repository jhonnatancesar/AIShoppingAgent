import { api } from './client'
import type { WebSessionUser } from './types'

export interface RegisterRequest {
  username: string
  email: string
  password: string
  password_confirmation: string
}

export type VerificationChannel = 'telegram' | 'email'

export interface ChallengeIssued {
  challenge_id: string
  expires_at: string
}

export interface RecoveryConfirmRequest {
  challenge_id: string
  code: string
  new_password: string
  new_password_confirmation: string
}

export const registrationApi = {
  register: (payload: RegisterRequest) => api.post<WebSessionUser>('/users/register', payload),
}

export const recoveryApi = {
  channels: (identifier: string) =>
    api.post<{ channels: VerificationChannel[] }>('/auth/password-recovery/channels', { identifier }),
  request: (identifier: string, channel: VerificationChannel) =>
    api.post<ChallengeIssued>('/auth/password-recovery/request', { identifier, channel }),
  confirm: (payload: RecoveryConfirmRequest) =>
    api.post<{ ok: boolean }>('/auth/password-recovery/confirm', payload),
}

export const emailVerificationApi = {
  request: () => api.post<ChallengeIssued>('/auth/verification/request', {}),
  confirm: (challengeId: string, code: string) =>
    api.post<{ ok: boolean }>('/auth/verification/confirm', { challenge_id: challengeId, code }),
}
