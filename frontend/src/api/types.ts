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
