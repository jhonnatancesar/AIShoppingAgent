import type { ReactElement } from 'react'
import { ResponsiveContainer } from 'recharts'
import { cn } from '@/lib/utils'

interface ChartContainerProps {
  children: ReactElement
  className?: string
}

export function ChartContainer({ children, className }: ChartContainerProps) {
  return (
    <div className={cn('h-72 w-full text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground', className)}>
      <ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer>
    </div>
  )
}
