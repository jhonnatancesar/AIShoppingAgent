import { api } from './client'
import type { AccountProfile, TelegramLinkChallenge } from './types'

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

export const accountApi = {
  get: () => api.get<AccountProfile>('/account'),
  updateProfile: (payload: AccountProfileUpdate) =>
    api.put<AccountProfile>('/account/profile', payload),
  updateNotifications: (payload: NotificationPreferencesUpdate) =>
    api.put<AccountProfile>('/account/notification-preferences', payload),
  startTelegramLink: () => api.post<TelegramLinkChallenge>('/account/telegram-link', {}),
  unlinkTelegram: () => api.del<AccountProfile>('/account/telegram-link'),
}
