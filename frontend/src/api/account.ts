import { api } from './client'
import type { AccountProfile, AccountQuota, TelegramLinkChallenge } from './types'

export interface AccountProfileUpdate {
  display_name: string
  email: string | null
  favorite_stores: string[]
  preferred_categories: string[]
}

export interface NotificationPreferencesUpdate {
  notify_price_decreases: boolean
  notify_target_reached: boolean
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
  new_password_confirmation: string
}

export const accountApi = {
  get: () => api.get<AccountProfile>('/account'),
  getQuota: () => api.get<AccountQuota>('/account/quota'),
  updateProfile: (payload: AccountProfileUpdate) =>
    api.put<AccountProfile>('/account/profile', payload),
  updateNotifications: (payload: NotificationPreferencesUpdate) =>
    api.put<AccountProfile>('/account/notification-preferences', payload),
  changePassword: (payload: ChangePasswordRequest) =>
    api.put<AccountProfile>('/account/password', payload),
  startTelegramLink: () => api.post<TelegramLinkChallenge>('/account/telegram-link', {}),
  unlinkTelegram: () => api.del<AccountProfile>('/account/telegram-link'),
}
