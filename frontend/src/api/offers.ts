import { api } from './client'
import type {
  OfferComparison,
  OfferDetail,
  OfferListResponse,
  HistoricalPriceResponse,
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
  /** TASK-126, DEV-only: quando `true`, mostra ofertas de TODOS os
   * usuários (não só as acessíveis ao logado) e inclui `NO_MATCH`.
   * `false`/ausente preserva o comportamento de sempre. */
  all_users?: boolean
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
  historicalPrice: (offerId: string) =>
    api.get<HistoricalPriceResponse>(`/offers/${offerId}/historical-price`),
  /** `force` só tem efeito para DEV, depois da confirmação na tela. */
  searchHistoricalPrice: (offerId: string, force = false) =>
    api.post<HistoricalPriceResponse>(`/offers/${offerId}/historical-price/search`, { force }),
  priceHistory: (offerId: string, period: PriceHistoryPeriod, storeIds?: string[]) => {
    const params = new URLSearchParams({ period })
    for (const storeId of storeIds ?? []) params.append('store_ids', storeId)
    return api.get<PriceHistoryResponse>(
      `/offers/${offerId}/price-history?${params.toString()}`,
    )
  },
}
