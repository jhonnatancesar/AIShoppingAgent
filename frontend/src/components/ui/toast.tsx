import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ToastItem {
  id: string
  title: string
  description?: string
  variant?: 'default' | 'success' | 'destructive'
}

const VARIANT_CLASSES: Record<NonNullable<ToastItem['variant']>, string> = {
  default: 'border-border bg-card text-card-foreground',
  success: 'border-success/30 bg-success/10 text-foreground',
  destructive: 'border-destructive/30 bg-destructive/10 text-foreground',
}

function Toast({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: string) => void }) {
  return (
    <div
      role="status"
      className={cn(
        'pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border p-4 shadow-2xl',
        VARIANT_CLASSES[toast.variant ?? 'default'],
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{toast.title}</p>
        {toast.description ? <p className="mt-1 text-sm text-muted-foreground">{toast.description}</p> : null}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dispensar"
        className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-black/5 hover:text-foreground dark:hover:bg-white/10"
      >
        <X className="size-4" />
      </button>
    </div>
  )
}

export { Toast }
