import * as React from 'react'
import { cn } from '@/lib/utils'

function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return (
    <textarea
      className={cn('flex min-h-28 w-full rounded-xl border border-input bg-background px-3.5 py-3 text-sm shadow-xs outline-none transition-[border-color,box-shadow,background-color] placeholder:text-muted-foreground/85 hover:border-primary/25 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50', className)}
      {...props}
    />
  )
}

export { Textarea }
