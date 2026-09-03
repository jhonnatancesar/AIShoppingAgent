/**
 * Ação recusada por cota (TASK-107) -- nunca renderiza só a mensagem
 * genérica. `actions` vem do backend (`QuotaExceededError.actions`);
 * este componente só traduz cada código conhecido para um rótulo/link,
 * nunca inventa ação nova.
 */
import { AlertTriangle } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ApiError } from '@/api/client'
import type { QuotaAction, QuotaErrorDetails } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

const ACTION_LABELS: Record<QuotaAction, string> = {
  pause_mission: 'Pausar uma missão',
  cancel_mission: 'Cancelar uma missão',
  manage_missions: 'Gerenciar missões',
  reduce_mission_stores: 'Reduzir lojas de uma missão',
  wait_for_daily_reset: 'Aguardar a renovação diária',
}

function isQuotaErrorDetails(details: unknown): details is QuotaErrorDetails {
  return (
    !!details &&
    typeof details === 'object' &&
    'kind' in details &&
    'actions' in details &&
    Array.isArray((details as QuotaErrorDetails).actions)
  )
}

/** Extrai o detalhe estruturado de cota de um `ApiError`, se houver. */
export function quotaDetailsFromError(error: ApiError): QuotaErrorDetails | null {
  return isQuotaErrorDetails(error.details) ? error.details : null
}

export function QuotaExceededNotice({
  message,
  details,
}: {
  message: string
  details: QuotaErrorDetails
}) {
  const actionable = details.actions.filter((action) => action !== 'wait_for_daily_reset')
  return (
    <Card className="border-warning/40 bg-warning/8">
      <CardContent className="space-y-3 pt-6">
        <div className="flex items-start gap-2 text-sm font-medium text-foreground">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
          <span>{message}</span>
        </div>
        {actionable.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {actionable.map((action) => (
              <Button key={action} asChild size="sm" variant="outline">
                <Link to="/app/missions">{ACTION_LABELS[action]}</Link>
              </Button>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
