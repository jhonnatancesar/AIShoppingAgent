import type { Coupon } from '@/api/types'
import type { CouponCardData } from '@/components/CouponCard'

function money(value: string) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value))
}

function percent(value: string) {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(Number(value))
}

/** Prioridade real, nunca inventada: 1. desconto estruturado
 * (`discount_kind`/`discount_value`, valor real só formatado); 2. texto
 * cru coletado (`raw_rule_text`, aparado); 3. rótulo neutro, sem valor
 * nenhum, quando não há informação estruturada nem texto. */
export function couponTitle(coupon: Coupon): string {
  if (coupon.discount_kind === 'fixed_amount' && coupon.discount_value != null) {
    return `${money(coupon.discount_value)} OFF`
  }
  if (coupon.discount_kind === 'percentage' && coupon.discount_value != null) {
    return `${percent(coupon.discount_value)}% OFF`
  }
  const trimmedRule = coupon.raw_rule_text?.trim()
  if (trimmedRule) return trimmedRule
  return 'Cupom disponível'
}

function couponDescription(coupon: Coupon): string | null {
  const parts: string[] = []
  const trimmedRule = coupon.raw_rule_text?.trim()
  const title = couponTitle(coupon)
  // Só repete o texto cru na descrição quando o título NÃO já é esse
  // mesmo texto (evita duas linhas idênticas no card).
  if (trimmedRule && trimmedRule !== title) parts.push(trimmedRule)
  if (coupon.minimum_purchase_amount != null) {
    parts.push(`Compra mínima de ${money(coupon.minimum_purchase_amount)}`)
  }
  return parts.length > 0 ? parts.join(' · ') : null
}

/** Nunca retorna `null` -- um cupom ativo real do backend nunca some da
 * aba só porque a loja ainda não tem identidade visual no catálogo
 * hardcoded (`storeVisuals.ts`). `CouponCard` já sabe cair num visual
 * neutro (ver seu próprio comentário) quando `storeCode` não bate com
 * nenhuma loja conhecida -- aqui só repassamos `store.code`/`store.name`
 * reais, sem decidir nada sobre aparência. */
export function couponToCardData(coupon: Coupon): CouponCardData {
  return {
    id: coupon.id,
    storeCode: coupon.store.code,
    storeName: coupon.store.name,
    title: couponTitle(coupon),
    description: couponDescription(coupon),
    code: coupon.code,
    expiresLabel: coupon.valid_until ? `Válido até ${coupon.valid_until}` : null,
  }
}

export type CouponsPageState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'empty' }
  | { kind: 'success'; cards: CouponCardData[] }

/** Extraído do componente só pra ser testável sem precisar montar/rodar
 * efeitos do React (este projeto não tem jsdom/interação -- os testes de
 * página aqui são renderização estática via `react-dom/server`). A
 * decisão de qual estado mostrar fica pura e testável isolada; o
 * componente só consome o resultado. */
export function resolveCouponsPageState(
  coupons: Coupon[] | null,
  error: string | null,
): CouponsPageState {
  const cards = coupons?.map(couponToCardData) ?? null
  if (error && !cards) return { kind: 'error', message: error }
  if (!cards) return { kind: 'loading' }
  if (cards.length === 0) return { kind: 'empty' }
  return { kind: 'success', cards }
}
