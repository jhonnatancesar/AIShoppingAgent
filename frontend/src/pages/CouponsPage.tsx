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
        description="Em breve, cupons de desconto para completar as ofertas que você acompanha."
      />
      <EmptyState
        title="Nenhum cupom disponível no momento"
        description="Quando houver cupons disponíveis no GG Oferta, eles aparecerão aqui."
      />
    </section>
  )
}
