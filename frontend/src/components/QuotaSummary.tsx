/**
 * Exibição de cota de capacidade por usuário (TASK-107). `near_limit` já
 * vem calculado pelo backend (`GET /account/quota`) -- o cliente só
 * decide como destacar, nunca recalcula o limiar de aviso.
 */
import { AlertTriangle } from 'lucide-react'
import type { AccountQuota, QuotaItem } from '@/api/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function QuotaUsageRow({ label, item }: { label: string; item: QuotaItem }) {
  const pct = item.limit > 0 ? Math.min(100, Math.round((item.current / item.limit) * 100)) : 0
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{label}</span>
        <span className={item.near_limit ? 'font-semibold text-amber-600' : 'text-muted-foreground'}>
          {item.current}/{item.limit}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all ${item.near_limit ? 'bg-amber-500' : 'bg-primary'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {item.near_limit ? (
        <p className="flex items-center gap-1 text-xs text-amber-600">
          <AlertTriangle className="size-3" />
          Perto do limite
        </p>
      ) : null}
    </div>
  )
}

export function QuotaSummaryCard({ quota }: { quota: AccountQuota }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Capacidade da conta</CardTitle>
        <CardDescription>Limites de uso da sua conta agora.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <QuotaUsageRow label="Missões ativas" item={quota.active_missions} />
        <QuotaUsageRow label="Lojas monitoradas" item={quota.store_slots} />
        <QuotaUsageRow label="Pesquisas hoje" item={quota.daily_searches} />
      </CardContent>
    </Card>
  )
}
