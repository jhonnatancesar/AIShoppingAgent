import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { getStoreVisual } from '@/components/storeVisuals'

export function StoreMark({ store, className }: { store: string; className?: string }) {
  const visual = getStoreVisual(store)
  if (!visual) return null
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid h-6 shrink-0 place-items-center overflow-hidden rounded-md p-0.5 shadow-xs ring-1 ring-black/10',
        visual.wide ? 'w-10 px-1' : 'w-6',
        className,
      )}
      style={{ backgroundColor: visual.background }}
    >
      <img src={visual.logo} alt="" className="size-full object-contain" />
    </span>
  )
}

export function StoreName({ store, children, className }: { store: string; children?: ReactNode; className?: string }) {
  const visual = getStoreVisual(store)
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1.5', className)}>
      <StoreMark store={store} />
      <span>{children ?? visual?.name ?? store}</span>
    </span>
  )
}
