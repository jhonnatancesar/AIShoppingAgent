import type { ReactNode } from 'react'
import { AlertCircle, Inbox, LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

export function LoadingState({ label = 'Carregando…' }: { label?: string }) {
  return (
    <Card aria-live="polite">
      <CardContent className="space-y-4 pt-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />{label}</div>
        <Skeleton className="h-5 w-2/3" /><Skeleton className="h-20 w-full" /><Skeleton className="h-9 w-32" />
      </CardContent>
    </Card>
  )
}

interface StateProps { title: string; description?: string; action?: ReactNode }

export function EmptyState({ title, description, action }: StateProps) {
  return <State icon={<Inbox />} title={title} description={description} action={action} />
}

export function ErrorState({ title = 'Algo deu errado', description, onRetry }: StateProps & { onRetry?: () => void }) {
  return <State icon={<AlertCircle />} title={title} description={description} action={onRetry ? <Button variant="outline" onClick={onRetry}>Tentar novamente</Button> : undefined} />
}

function State({ icon, title, description, action }: StateProps & { icon: ReactNode }) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex min-h-52 flex-col items-center justify-center p-8 text-center">
        <div className="mb-4 grid size-11 place-items-center rounded-xl bg-muted text-muted-foreground [&_svg]:size-5">{icon}</div>
        <h2 className="font-semibold">{title}</h2>
        {description ? <p className="mt-2 max-w-md text-sm text-muted-foreground">{description}</p> : null}
        {action ? <div className="mt-5">{action}</div> : null}
      </CardContent>
    </Card>
  )
}
