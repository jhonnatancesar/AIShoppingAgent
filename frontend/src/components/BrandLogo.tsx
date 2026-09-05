import { cn } from '@/lib/utils'

/** Marca horizontal responsiva ao tema e monograma para espaços compactos. */
export function BrandLogo({ className, compact = false }: { className?: string; compact?: boolean }) {
  if (compact) {
    return <img src="/logo-mark.png" alt="GG Oferta" className={cn('w-auto object-contain', className)} />
  }

  return (
    <span className={cn('inline-flex w-auto items-center', className)}>
      <img src="/logo-full-light.png" alt="GG Oferta" className="h-full w-auto object-contain dark:hidden" />
      <img src="/logo-full-dark.png" alt="GG Oferta" className="hidden h-full w-auto object-contain dark:block" />
    </span>
  )
}
