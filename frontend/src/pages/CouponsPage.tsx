import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/StatePanel'

/** Subtask 11: só a área "Cupons" dentro do shell autenticado -- sem
 * backend, sem integração com o Coupon Collector (repositório separado).
 * Estado vazio honesto, nunca dado artificial apresentado como real. */
export function CouponsPage() {
  return (
    <section>
      <PageHeader
        title="Cupons"
        description="Se um código puder deixar uma boa oferta ainda melhor, você vai encontrar aqui."
      />
      <EmptyState
        title="Ainda não encontramos um cupom para você"
        description="Quando uma das lojas liberar um código que vale a pena, ele aparece aqui pronto para copiar."
      />
    </section>
  )
}
