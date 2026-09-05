import { PageHeader } from '@/components/PageHeader'
import { CouponCollection, type CouponCardData } from '@/components/CouponCard'

const COUPON_TEMPLATES: CouponCardData[] = [
  { id: 'amazon-template', storeCode: 'amazon', title: 'Espaço pronto para o próximo cupom', description: 'Código, benefício e validade aparecem aqui quando houver uma oferta ativa.', expiresLabel: 'Modelo visual · nenhum cupom ativo' },
  { id: 'kabum-template', storeCode: 'kabum', title: 'Espaço pronto para o próximo cupom', description: 'Código, benefício e validade aparecem aqui quando houver uma oferta ativa.', expiresLabel: 'Modelo visual · nenhum cupom ativo' },
  { id: 'magalu-template', storeCode: 'magalu', title: 'Espaço pronto para o próximo cupom', description: 'Código, benefício e validade aparecem aqui quando houver uma oferta ativa.', expiresLabel: 'Modelo visual · nenhum cupom ativo' },
  { id: 'mercadolivre-template', storeCode: 'mercadolivre', title: 'Espaço pronto para o próximo cupom', description: 'Código, benefício e validade aparecem aqui quando houver uma oferta ativa.', expiresLabel: 'Modelo visual · nenhum cupom ativo' },
  { id: 'pichau-template', storeCode: 'pichau', title: 'Espaço pronto para o próximo cupom', description: 'Código, benefício e validade aparecem aqui quando houver uma oferta ativa.', expiresLabel: 'Modelo visual · nenhum cupom ativo' },
  { id: 'terabyte-template', storeCode: 'terabyte', title: 'Espaço pronto para o próximo cupom', description: 'Código, benefício e validade aparecem aqui quando houver uma oferta ativa.', expiresLabel: 'Modelo visual · nenhum cupom ativo' },
]

/** Subtask 11: só a área "Cupons" dentro do shell autenticado -- sem
 * backend, sem integração com o Coupon Collector (repositório separado).
 * Estado vazio honesto, nunca dado artificial apresentado como real. */
export function CouponsPage() {
  return (
    <section>
      <PageHeader
        eyebrow="Modelos por loja"
        title="Cupons"
        description="Cada loja já tem um cartão com sua cor e sua marca. Com um cupom, o layout ganha destaque; com vários, vira uma grade responsiva."
      />
      <div className="mb-5 rounded-xl border border-primary/20 bg-primary/5 px-4 py-3 text-sm text-muted-foreground">
        Estes são modelos de apresentação, não cupons ativos. Quando houver dados reais, o código e o botão de copiar entram automaticamente.
      </div>
      <CouponCollection coupons={COUPON_TEMPLATES} />
    </section>
  )
}
