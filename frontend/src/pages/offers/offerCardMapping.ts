import type { OfferSummary } from '@/api/types'

/** Exportado para reuso pela Home (Subtask 15) -- evita uma segunda
 * implementação independente do mesmo mapeamento OfferSummary→OfferCardData. */
export function offerSummaryToCardData(offer: OfferSummary) {
  return {
    id: offer.id,
    title: offer.title,
    imageUrl: offer.image_url,
    imageFallbackUrl: offer.image_fallback_url,
    store: offer.store,
    price: offer.latest_observation
      ? { amount: offer.latest_observation.amount, totalAmount: offer.latest_observation.total_amount, currency: offer.latest_observation.currency }
      : null,
    condition: offer.latest_observation?.condition ?? null,
    seller: offer.seller,
    rating: offer.rating ? { average: offer.rating.average, reviewCount: offer.rating.review_count } : null,
  }
}
