import { api } from './client'
import type {
  ProductSearchResponse,
  SearchHistoryResponse,
  TrendingSearchedProductsResponse,
} from './types'

export const searchApi = {
  search: (query: string, stores: string[]) => {
    const params = new URLSearchParams({ q: query })
    stores.forEach((store) => params.append('stores', store))
    return api.get<ProductSearchResponse>(`/product-search?${params.toString()}`)
  },
  trending: () =>
    api.get<TrendingSearchedProductsResponse>('/product-search/trending'),
  historyMine: (limit = 20, offset = 0) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    return api.get<SearchHistoryResponse>(`/product-search/history/mine?${params.toString()}`)
  },
  historyAll: (limit = 20, offset = 0) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    return api.get<SearchHistoryResponse>(`/product-search/history/all?${params.toString()}`)
  },
}
