import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

const TONE_CLASSES = {
  neutral: 'text-muted-foreground',
  success: 'text-success',
  error: 'text-destructive',
} as const

/** Mensagem de status de formulário (Subtask 12) -- substitui os cinco
 * padrões distintos que existiam para a mesma ideia (erro/sucesso de
 * envio) por um único componente com o `role` correto para cada tom:
 * erro interrompe o leitor de tela (`alert`), sucesso/neutro só anuncia
 * (`status`). */
export function FormMessage({ tone = 'neutral', children }: { tone?: keyof typeof TONE_CLASSES; children: ReactNode }) {
  if (!children) return null
  return (
    <p role={tone === 'error' ? 'alert' : 'status'} className={cn('text-sm', TONE_CLASSES[tone])}>
      {children}
    </p>
  )
}
