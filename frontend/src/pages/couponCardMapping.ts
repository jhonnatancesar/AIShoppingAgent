import type { Coupon } from '@/api/types'
import type { CouponCardData } from '@/components/CouponCard'

function money(value: string) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value))
}

function percent(value: string) {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(Number(value))
}

/** Prioridade real, nunca inventada -- só 2 níveis (revisão 2026-09-10:
 * `raw_rule_text` NUNCA vira título de desconto, mesmo como fallback --
 * pode representar preço final ou regra extensa demais pra um título,
 * ele fica só na descrição): 1. desconto estruturado (`discount_kind`/
 * `discount_value`, valor real só formatado); 2. rótulo neutro, sem
 * valor nenhum. */
export function couponTitle(coupon: Coupon): string {
  if (coupon.discount_kind === 'fixed_amount' && coupon.discount_value != null) {
    return `${money(coupon.discount_value)} OFF`
  }
  if (coupon.discount_kind === 'percentage' && coupon.discount_value != null) {
    return `${percent(coupon.discount_value)}% OFF`
  }
  return 'Cupom disponível'
}

/** `null` quando o backend não decidiu a abrangência (`scope_kind`
 * ausente) -- nunca afirma "toda a loja" nem "produto específico" sem
 * confirmação real. Quando presente, `scope_kind` já foi decidido pelo
 * Coupon Worker com evidência (`store_wide` só quando o texto da própria
 * loja diz isso; `product` só quando a evidência veio de um card/produto
 * específico) -- só traduzimos pra um rótulo legível, nunca inventamos. */
export function couponScopeLabel(coupon: Coupon): string | null {
  if (coupon.scope_kind === 'store_wide') return 'Válido para toda a loja'
  if (coupon.scope_kind === 'product') return 'Válido para este produto'
  return null
}

export function couponMinimumPurchaseLabel(coupon: Coupon): string | null {
  if (coupon.minimum_purchase_amount == null) return null
  return `Compra mínima de ${money(coupon.minimum_purchase_amount)}`
}

/** `valid_until` já vem do Coupon Worker como a FRASE completa que a
 * própria loja usou ("Válido até 31/12"/"Vence amanhã" --
 * `_find_validity_hint`/`_VALIDITY_HINT_PATTERNS`, `coupons/evidence.py`)
 * -- nunca uma data crua. Bug real corrigido (revisão 2026-09-10,
 * achado numa renderização com dado real do Worker): este mapeamento
 * prefixava "Válido até " de novo, duplicando o prefixo quando a loja
 * já dizia "Válido até X" (virava "Válido até Válido até X"). Repassa
 * o texto tal como veio, sem reformatar. */
export function couponExpiresLabel(coupon: Coupon): string | null {
  return coupon.valid_until?.trim() || null
}

/** Só `http`/`https` reais viram link -- nunca fabrica URL a partir de
 * loja/código/slug. Backend já repassa `source_url` cru, sem validar
 * esquema (validação é responsabilidade do frontend, TASK-121 revisão). */
export function couponActionUrl(coupon: Coupon): string | null {
  if (!coupon.source_url) return null
  try {
    const parsed = new URL(coupon.source_url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? coupon.source_url : null
  } catch {
    return null
  }
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
    code: coupon.code,
    scopeLabel: couponScopeLabel(coupon),
    description: coupon.raw_rule_text?.trim() || null,
    minimumPurchaseLabel: couponMinimumPurchaseLabel(coupon),
    expiresLabel: couponExpiresLabel(coupon),
    actionUrl: couponActionUrl(coupon),
  }
}

export interface CouponStoreGroup {
  storeCode: string
  storeName: string
  cards: CouponCardData[]
}

/** Agrupa por loja, preservando a ordem que a API já devolve dentro de
 * cada grupo (`last_seen_at desc`, decisão do backend -- nunca um
 * ranking artificial de "melhor cupom" recalculado aqui). Ordem dos
 * GRUPOS: pela posição do primeiro cupom de cada loja na resposta
 * original (mesma ideia -- não inventa um critério novo de prioridade
 * entre lojas). Loja desconhecida entra normalmente, com `storeName`
 * real. */
export function groupCouponsByStore(coupons: Coupon[]): CouponStoreGroup[] {
  const groups = new Map<string, CouponStoreGroup>()
  for (const coupon of coupons) {
    const key = coupon.store.code
    let group = groups.get(key)
    if (!group) {
      group = { storeCode: coupon.store.code, storeName: coupon.store.name, cards: [] }
      groups.set(key, group)
    }
    group.cards.push(couponToCardData(coupon))
  }
  return Array.from(groups.values())
}

export type CouponsPageState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'empty' }
  | { kind: 'success'; groups: CouponStoreGroup[]; total: number }

/** Extraído do componente só pra ser testável sem precisar montar/rodar
 * efeitos do React (este projeto não tem jsdom/interação -- os testes de
 * página aqui são renderização estática via `react-dom/server`). A
 * decisão de qual estado mostrar fica pura e testável isolada; o
 * componente só consome o resultado. */
export function resolveCouponsPageState(
  coupons: Coupon[] | null,
  error: string | null,
): CouponsPageState {
  if (error && !coupons) return { kind: 'error', message: error }
  if (!coupons) return { kind: 'loading' }
  if (coupons.length === 0) return { kind: 'empty' }
  return { kind: 'success', groups: groupCouponsByStore(coupons), total: coupons.length }
}
