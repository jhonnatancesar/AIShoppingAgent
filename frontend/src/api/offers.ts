import { api } from './client'
import type { OfferDetail } from './types'

export const offersApi = {
  get: (offerId: string) => api.get<OfferDetail>(`/offers/${offerId}`),
}
