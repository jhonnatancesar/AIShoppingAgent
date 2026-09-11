import { useState } from 'react'
import { Check, Copy, ExternalLink, Store as StoreIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { StoreMark } from '@/components/StoreMark'
import { getStoreVisual } from '@/components/storeVisuals'

export interface CouponCardData {
  id: string
  /** Código cru da loja (`Store.code` do backend) -- nunca restrito ao
   * catálogo visual hardcoded (`storeVisuals.ts`); um cupom de loja ainda
   * não mapeada visualmente continua chegando aqui, nunca é descartado. */
  storeCode: string
  /** `Store.name` real do backend -- nome visível quando a loja não tem
   * identidade visual própria ainda. */
  storeName: string
  /** Benefício estruturado ("10% OFF"/"R$100,00 OFF") ou "Cupom
   * disponível" -- nunca `raw_rule_text` (pode ser preço final ou regra
   * extensa demais pra um título; ver `description`). */
  title: string
  code: string | null
  /** "Válido para toda a loja"/"Válido para este produto" -- só quando o
   * backend já decidiu `scope_kind` com evidência real; `null` quando
   * desconhecido (nunca afirma nada nesse caso). */
  scopeLabel: string | null
  /** `raw_rule_text` aparado -- regra/condição real da loja, sempre em
   * texto livre, nunca reformatada como se fosse um valor estruturado. */
  description: string | null
  minimumPurchaseLabel: string | null
  expiresLabel: string | null
  /** Só `http(s)` já validado pelo mapeamento -- nunca fabricada aqui. */
  actionUrl: string | null
}

/** Visual único para cupom destacado ou para uma coleção. O componente é
 * propositalmente só apresentacional: nenhum código, validade ou benefício é
 * inventado; tudo precisa chegar do contrato real de cupons.
 *
 * TASK-121 (revisão 2026-09-10): uma loja fora do catálogo visual
 * hardcoded (`storeVisuals.ts`, hoje só as 6 lojas da V1) NUNCA faz o
 * cupom sumir -- cai num visual neutro já existente no design system
 * (`bg-muted`/`text-muted-foreground`, ícone genérico), com o nome/código
 * REAIS da loja. Nenhuma cor, logo ou identidade de marca é inventada
 * para ela.
 *
 * Hierarquia visual (pedido explícito da revisão): loja -> benefício ->
 * código (destacado, com copiar) -> abrangência/regra -> compra mínima
 * -> validade -> ação pra loja. Nenhum campo vazio é exibido; nenhum
 * "automático"/"toda a loja"/"sem restrições" é inventado quando a
 * informação é desconhecida. */
export function CouponCard({ coupon, featured = false }: { coupon: CouponCardData; featured?: boolean }) {
  const [copied, setCopied] = useState(false)
  const visual = getStoreVisual(coupon.storeCode)
  const displayName = coupon.storeName || coupon.storeCode

  async function copyCode() {
    if (!coupon.code) return
    try {
      await navigator.clipboard.writeText(coupon.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard indisponível (permissão negada, contexto não seguro) --
      // o código já está visível e selecionável na tela, então falhar
      // aqui nunca impede o usuário de copiar manualmente.
    }
  }

  return (
    <article className={cn('relative overflow-hidden rounded-2xl border border-border bg-card shadow-card', featured && 'sm:grid sm:grid-cols-[11rem_1fr]')}>
      <div
        className={cn('flex items-center gap-3 border-b border-border/70 p-5 sm:border-b-0', featured && 'sm:flex-col sm:items-start sm:justify-between sm:border-r sm:p-6')}
        style={{ background: visual ? `color-mix(in srgb, ${visual.background} 11%, var(--card))` : 'var(--muted)' }}
      >
        {visual ? (
          <StoreMark store={coupon.storeCode} className="size-11 rounded-xl text-base" />
        ) : (
          <span aria-hidden="true" className="grid size-11 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground ring-1 ring-border">
            <StoreIcon className="size-5" />
          </span>
        )}
        <div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Cupom</p><p className="font-bold" style={visual ? { color: visual.accent } : undefined}>{displayName}</p></div>
      </div>
      <div className="flex min-w-0 flex-col gap-3 p-5 sm:p-6">
        <h2 className={cn('font-bold tracking-[-0.025em]', featured ? 'text-xl' : 'text-lg')}>{coupon.title}</h2>

        {coupon.code ? (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">Código</p>
            <div className="flex items-stretch gap-2">
              <code className="flex min-h-10 min-w-0 flex-1 items-center rounded-xl border border-dashed border-primary/35 bg-primary/5 px-3 font-mono text-sm font-bold tracking-wide text-primary">{coupon.code}</code>
              <Button type="button" variant="outline" onClick={copyCode} aria-label={`Copiar código ${coupon.code}`}>
                {copied ? <Check /> : <Copy />}{copied ? 'Copiado' : 'Copiar'}
              </Button>
            </div>
          </div>
        ) : null}

        {coupon.scopeLabel ? <p className="text-sm text-muted-foreground">{coupon.scopeLabel}</p> : null}
        {coupon.description ? <p className="text-sm leading-6 text-muted-foreground">{coupon.description}</p> : null}
        {coupon.minimumPurchaseLabel ? <p className="text-sm text-muted-foreground">{coupon.minimumPurchaseLabel}</p> : null}
        {coupon.expiresLabel ? <p className="text-xs font-medium text-muted-foreground">{coupon.expiresLabel}</p> : null}

        {coupon.actionUrl ? (
          <Button asChild type="button" variant="outline" className="mt-1 w-fit">
            <a href={coupon.actionUrl} target="_blank" rel="noreferrer">Ver na loja <ExternalLink /></a>
          </Button>
        ) : null}
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
