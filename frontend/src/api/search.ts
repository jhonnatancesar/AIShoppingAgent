import { api } from './client'
import type { ProductSearchResponse } from './types'

export const searchApi = {
  search: (query: string, stores: string[]) => {
    const params = new URLSearchParams({ q: query })
    stores.forEach((store) => params.append('stores', store))
    return api.get<ProductSearchResponse>(`/product-search?${params.toString()}`)
  },
}
