import { cn } from '@/lib/utils'

/** Símbolo oficial isolado. O nome fica apenas no texto alternativo para
 * acessibilidade; nenhum wordmark adicional é desenhado ao lado da marca. */
export function BrandLogo({ className }: { className?: string; compact?: boolean }) {
  return (
    <img
      src="/logo-icon.png"
      alt="GG Oferta"
      className={cn('w-auto object-contain', className)}
    />
  )
}
