import { api } from './client'
import type {
  OfferComparison,
  OfferDetail,
  OfferListResponse,
  PriceHistoryPeriod,
  PriceHistoryResponse,
} from './types'

export interface OfferListFilters {
  q?: string
  store?: string
  condition?: string
  availability?: string
  sort?: 'recent' | 'price_asc' | 'price_desc'
  limit?: number
  offset?: number
}

export const offersApi = {
  list: (filters: OfferListFilters = {}) => {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    return api.get<OfferListResponse>(`/offers?${params.toString()}`)
  },
  get: (offerId: string) => api.get<OfferDetail>(`/offers/${offerId}`),
  compare: (offerId: string) =>
    api.get<OfferComparison>(`/offers/${offerId}/comparison`),
  priceHistory: (offerId: string, period: PriceHistoryPeriod) =>
    api.get<PriceHistoryResponse>(
      `/offers/${offerId}/price-history?period=${period}`,
    ),
}
