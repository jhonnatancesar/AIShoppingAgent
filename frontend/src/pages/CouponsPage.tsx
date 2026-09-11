import { useEffect, useState } from 'react'
import { ApiError } from '@/api/client'
import { couponsApi } from '@/api/coupons'
import type { Coupon } from '@/api/types'
import { CouponCollection } from '@/components/CouponCard'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { resolveCouponsPageState } from './couponCardMapping'

/** TASK-121 (revisão 2026-09-10): aba "Cupons" -- funciona pra qualquer
 * usuário autenticado, mesmo SEM nenhuma missão cadastrada. Cupons
 * ativos que o Coupon Worker coletou, agrupados por loja, listados
 * livremente (sem depender de o usuário já rastrear aquele produto).
 * Propósito diferente do badge de oferta (`applied_coupon`, `DEC-129`),
 * que só aparece quando um cupom REALMENTE se aplica a uma Offer
 * específica que uma missão já rastreia -- a presença de um cupom aqui
 * não significa que ele seja aplicável a qualquer oferta daquela loja. */
export function CouponsPage() {
  const [coupons, setCoupons] = useState<Coupon[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    couponsApi.list().then(
      (response) => {
        if (cancelled) return
        setCoupons(response ?? [])
        setError(null)
      },
      (loadError) => {
        if (cancelled) return
        setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar os cupons.')
      },
    )
    return () => { cancelled = true }
  }, [retryToken])

  const state = resolveCouponsPageState(coupons, error)

  return (
    <section>
      <PageHeader
        eyebrow="Coletados automaticamente"
        title="Cupons"
        description="Cupons ativos encontrados pelo Coupon Worker nas lojas parceiras, disponíveis pra qualquer produto -- não só pros que você já está acompanhando."
      />
      {state.kind === 'error' ? (
        <ErrorState title="Cupons indisponíveis" description={state.message} onRetry={() => setRetryToken((token) => token + 1)} />
      ) : state.kind === 'loading' ? (
        <LoadingState label="Carregando cupons…" />
      ) : state.kind === 'empty' ? (
        <EmptyState title="Nenhum cupom ativo no momento" description="Assim que o Coupon Worker encontrar um cupom válido nas lojas parceiras, ele aparece aqui automaticamente." />
      ) : (
        <div className="space-y-8">
          {state.groups.map((group) => (
            <section key={group.storeCode} aria-labelledby={`store-group-${group.storeCode}`}>
              <h2 id={`store-group-${group.storeCode}`} className="mb-3 flex items-baseline gap-2 text-lg font-bold tracking-[-0.02em]">
                {group.storeName}
                <span className="text-sm font-medium text-muted-foreground">({group.cards.length})</span>
              </h2>
              <CouponCollection coupons={group.cards} />
            </section>
          ))}
        </div>
      )}
    </section>
  )
}
