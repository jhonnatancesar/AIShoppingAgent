import { useEffect, useState } from 'react'
import { ApiError } from '@/api/client'
import { couponsApi } from '@/api/coupons'
import type { Coupon } from '@/api/types'
import { CouponCollection } from '@/components/CouponCard'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { resolveCouponsPageState } from './couponCardMapping'

/** TASK-121: aba "Cupons" ligada a dados reais -- cupons ativos que o
 * Coupon Worker coletou, listados livremente (sem depender de o usuário
 * já rastrear aquele produto). Propósito diferente do badge de oferta
 * (`applied_coupon`, `DEC-129`), que só aparece quando um cupom REALMENTE
 * se aplica a uma Offer específica. */
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
        <CouponCollection coupons={state.cards} />
      )}
    </section>
  )
}
