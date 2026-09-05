import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PageHeaderProps {
  eyebrow?: ReactNode
  title: string
  description?: string
  actions?: ReactNode
  className?: string
}

export function PageHeader({ eyebrow, title, description, actions, className }: PageHeaderProps) {
  return (
    <header className={cn('mb-8 flex flex-col justify-between gap-5 sm:flex-row sm:items-end', className)}>
      <div className="min-w-0">
        {eyebrow ? <div className="mb-2.5 text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-primary">{eyebrow}</div> : null}
        <h1 className="text-[1.75rem] font-bold leading-tight tracking-[-0.035em] text-foreground sm:text-[2rem]">{title}</h1>
        {description ? <p className="mt-2.5 max-w-2xl text-sm leading-6 text-muted-foreground sm:text-[0.95rem]">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  )
}
