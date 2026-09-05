import { Check, Copy, Ticket } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { StoreMark } from '@/components/StoreMark'
import { getStoreVisual, type StoreCode } from '@/components/storeVisuals'

export type CouponStoreCode = StoreCode

export interface CouponCardData {
  id: string
  storeCode: CouponStoreCode
  title: string
  description?: string | null
  code?: string | null
  expiresLabel?: string | null
}

/** Visual único para cupom destacado ou para uma coleção. O componente é
 * propositalmente só apresentacional: nenhum código, validade ou benefício é
 * inventado; tudo precisa chegar do futuro contrato real de cupons. */
export function CouponCard({ coupon, featured = false, copied = false, onCopy }: { coupon: CouponCardData; featured?: boolean; copied?: boolean; onCopy?: () => void }) {
  const store = getStoreVisual(coupon.storeCode)!
  return (
    <article className={cn('relative overflow-hidden rounded-2xl border border-border bg-card shadow-card', featured && 'sm:grid sm:grid-cols-[11rem_1fr]')}>
      <div className={cn('flex items-center gap-3 border-b border-border/70 p-5 sm:border-b-0', featured && 'sm:flex-col sm:items-start sm:justify-between sm:border-r sm:p-6')} style={{ background: `color-mix(in srgb, ${store.background} 11%, var(--card))` }}>
        <StoreMark store={coupon.storeCode} className="size-11 rounded-xl text-base" />
        <div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Cupom</p><p className="font-bold" style={{ color: store.accent }}>{store.name}</p></div>
      </div>
      <div className="flex min-w-0 flex-col gap-4 p-5 sm:p-6">
        <div className="min-w-0 flex-1">
          <h2 className={cn('font-bold tracking-[-0.025em]', featured ? 'text-xl' : 'text-lg')}>{coupon.title}</h2>
          {coupon.description ? <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{coupon.description}</p> : null}
          {coupon.expiresLabel ? <p className="mt-2 text-xs font-medium text-muted-foreground">{coupon.expiresLabel}</p> : null}
        </div>
        {coupon.code ? (
          <div className="flex items-stretch gap-2">
            <code className="flex min-h-10 min-w-0 flex-1 items-center rounded-xl border border-dashed border-primary/35 bg-primary/5 px-3 font-mono text-sm font-bold tracking-wide text-primary">{coupon.code}</code>
            <Button type="button" variant="outline" onClick={onCopy} aria-label={`Copiar cupom ${coupon.code}`}>
              {copied ? <Check /> : <Copy />}{copied ? 'Copiado' : 'Copiar'}
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Ticket className="size-4" />Código informado pela loja</div>
        )}
      </div>
    </article>
  )
}

/** Um cupom recebe o layout de destaque; vários viram uma grade fluida sem
 * criar duas implementações visuais divergentes. */
export function CouponCollection({ coupons }: { coupons: CouponCardData[] }) {
  if (coupons.length === 0) return null
  if (coupons.length === 1) return <CouponCard coupon={coupons[0]} featured />
  return <div className="grid gap-4 md:grid-cols-2">{coupons.map((coupon) => <CouponCard key={coupon.id} coupon={coupon} />)}</div>
}
