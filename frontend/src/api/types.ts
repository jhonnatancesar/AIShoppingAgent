export type UserRole = 'USER' | 'ADMIN' | 'DEV'

export interface WebSessionUser {
  id: string
  display_name: string
  username: string | null
  role: UserRole
}

// TASK-092 (item 2 da V1.2): mesmo vocabulário de
// `app.missions.models.MissionStatus`/`MissionCommand` (backend).
export type MissionStatus =
  | 'draft'
  | 'active'
  | 'paused'
  | 'completed'
  | 'cancelled'
  | 'expired'

export type MissionCommand =
  | 'activate'
  | 'pause'
  | 'resume'
  | 'complete'
  | 'cancel'
  | 'expire'

export type MissionStatusFilter =
  | 'active'
  | 'paused'
  | 'cancelled'
  | 'completed'
  | 'expired'
  | 'all'

export interface MissionSummary {
  id: string
  title: string
  status: MissionStatus
  state_version: number
  created_at: string
  updated_at: string
  expires_at: string | null
}

export interface MissionListResponse {
  items: MissionSummary[]
  limit: number
  offset: number
  total: number
}

export interface MissionCriteria {
  search_query: string
  model: string | null
  target_amount: string | null
  target_currency: string | null
  request_kind: 'specific_product' | 'product_family' | 'generic_category'
  variant_selection_mode: 'not_required' | 'pending' | 'selected' | 'all'
}

export interface AccountOption {
  code: string
  label: string
}

export interface AccountProfile {
  id: string
  display_name: string
  username: string | null
  email: string | null
  role: UserRole
  telegram_linked: boolean
  telegram_link_status: 'not_linked' | 'pending' | 'linked'
  telegram_link_expires_at: string | null
  created_at: string
  favorite_stores: string[]
  preferred_categories: string[]
  notify_price_decreases: boolean
  notify_target_reached: boolean
  available_stores: AccountOption[]
  available_categories: AccountOption[]
}

// TASK-107: cota de capacidade por usuário.
export interface QuotaItem {
  current: number
  limit: number
  near_limit: boolean
}

export interface AccountQuota {
  active_missions: QuotaItem
  store_slots: QuotaItem
  daily_searches: QuotaItem
  daily_searches_reset_at: string
}

// Mesmo vocabulário de `QuotaExceededError.actions` no backend
// (`app.quotas.service`) -- nunca inventado no cliente.
export type QuotaAction =
  | 'pause_mission'
  | 'cancel_mission'
  | 'manage_missions'
  | 'reduce_mission_stores'
  | 'wait_for_daily_reset'

export interface QuotaErrorDetails {
  kind: 'active_missions' | 'store_slots' | 'daily_searches'
  limit: number
  current: number
  actions: QuotaAction[]
}

export interface TelegramLinkChallenge {
  command: string
  expires_at: string
  account: AccountProfile
}

export interface ProductVariantOption {
  product_id: string
  label: string
  attributes: Record<string, string>
  selected: boolean
}

export interface MissionSourceOut {
  store_code: string
  store_name: string
}

export interface MissionSchedule {
  interval_minutes: number
  next_run_at: string
  last_run_at: string | null
  is_enabled: boolean
}

export interface MissionTransitionOut {
  from_status: MissionStatus
  to_status: MissionStatus
  command: MissionCommand
  actor_type: string
  reason: string | null
  transitioned_at: string
}

export interface MissionOfferLink {
  id: string
  title: string
  store_code: string
  store_name: string
  last_seen_at: string
}

export interface MissionDetail extends MissionSummary {
  criteria: MissionCriteria | null
  sources: MissionSourceOut[]
  schedule: MissionSchedule | null
  transitions: MissionTransitionOut[]
  offers: MissionOfferLink[]
  available_variants: ProductVariantOption[]
}

export type OfferCondition = 'new' | 'refurbished' | 'used' | 'unknown'
export type OfferAvailability = 'available' | 'unavailable' | 'unknown'
export type MarketplacePartyKind = 'platform' | 'marketplace_partner' | 'unknown'
export type InstallmentInterestKind = 'interest_free' | 'with_interest' | 'unknown'

export interface OfferInstallment {
  installment_count: number
  installment_amount: string
  installment_total_amount: string | null
  discount_percent: string | null
  interest_kind: InstallmentInterestKind
  is_highlighted: boolean
}

export interface LatestOfferObservation {
  amount: string
  currency: string
  shipping_amount: string | null
  total_amount: string
  fulfillment: string | null
  seller_kind: MarketplacePartyKind | null
  fulfillment_kind: MarketplacePartyKind | null
  condition: OfferCondition
  availability: OfferAvailability
  observed_at: string
  installments: OfferInstallment[]
}

export interface OfferDetail {
  id: string
  title: string
  image_url: string | null
  original_url: string
  last_seen_at: string
  store: { code: string; name: string }
  seller: { name: string } | null
  rating: {
    average: string
    review_count: number
    observed_at: string
  } | null
  latest_observation: LatestOfferObservation | null
}

export interface OfferComparisonItem {
  id: string
  original_url: string
  image_url: string | null
  store: { code: string; name: string }
  seller: { name: string } | null
  rating: { average: string; review_count: number; observed_at: string } | null
  latest_observation: LatestOfferObservation | null
}

export interface OfferComparison {
  product_id: string
  title: string
  variant: string | null
  attributes: Record<string, string>
  comparable: boolean
  offers: OfferComparisonItem[]
}

export interface OfferSummary {
  id: string
  title: string
  image_url: string | null
  last_seen_at: string
  store: { code: string; name: string }
  seller: { name: string } | null
  rating: {
    average: string
    review_count: number
    observed_at: string
  } | null
  latest_observation: {
    amount: string
    total_amount: string
    currency: string
    condition: OfferCondition
    availability: OfferAvailability
    observed_at: string
  } | null
}

export interface OfferListResponse {
  items: OfferSummary[]
  limit: number
  offset: number
  total: number
}

export interface ProductSearchOffer {
  offer_id: string
  product_id: string
  title: string
  image_url: string | null
  original_url: string
  store_code: string
  store_name: string
  amount: string
  total_amount: string
  currency: string
  condition: OfferCondition
  availability: OfferAvailability
  rating_average: string | null
  review_count: number | null
}

export interface ProductSearchVariant {
  product_id: string
  label: string
  attributes: Record<string, string>
}

export interface ProductSearchResponse {
  query: string
  request_kind: 'specific_product' | 'product_family' | 'generic_category'
  offers: ProductSearchOffer[]
  variants: ProductSearchVariant[]
}

// TASK-098: histórico de preço do Product ancorado na Offer.
export type PriceHistoryPeriod = '1d' | '7d' | '1m' | '6m' | '1a' | 'all'

export interface PriceHistoryPoint {
  date: string
  amount: string
}

export interface PriceHistorySeries {
  store_id: string
  store_code: string
  store_name: string
  points: PriceHistoryPoint[]
}

export interface PriceHistoryMetrics {
  current_amount: string | null
  min_amount: string | null
  max_amount: string | null
  average_amount: string | null
  variation_percent: string | null
}

export interface PriceHistoryResponse {
  product_id: string
  comparable: boolean
  reason: string | null
  period: PriceHistoryPeriod
  currency: string | null
  period_from: string | null
  period_to: string
  series: PriceHistorySeries[]
  metrics: PriceHistoryMetrics | null
}
