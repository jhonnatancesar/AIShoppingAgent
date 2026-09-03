import { cn } from '@/lib/utils'

/** Logotipo GG Oferta (cabeçalho completo, ícone + "GG Ofertas" + tagline).
 * A variante escura tem fundo sólido embutido na própria imagem (fornecida
 * já pronta para o tema `.dark`) -- por isso a troca é feita alternando
 * qual `<img>` fica visível via `dark:`, nunca recolorindo em runtime. */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <>
      <img src="/logo-header.png" alt="GG Oferta" className={cn(className, 'dark:hidden')} />
      <img src="/logo-header-dark.png" alt="GG Oferta" className={cn('hidden dark:block', className)} />
    </>
  )
}
