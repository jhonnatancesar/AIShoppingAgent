import { api } from './client'
import type { Coupon } from './types'

export const couponsApi = {
  list: () => api.get<Coupon[]>('/coupons'),
}
