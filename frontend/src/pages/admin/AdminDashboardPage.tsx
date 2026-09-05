import { useCallback, useEffect, useState } from 'react'
import { Activity, Play, RefreshCw, Server, Users, Workflow } from 'lucide-react'
import { adminApi, type ApiKeysStatus, type Dashboard, type QueueDashboard } from '@/api/admin'
import { PageHeader } from '@/components/PageHeader'
import { ErrorState, LoadingState } from '@/components/StatePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useToast } from '@/hooks/toastContext'
import { AdminStatusBadge, ConfirmActionButton } from './adminComponents'
import { adminStatusLabel } from './adminLabels'
import { StoreName } from '@/components/StoreMark'

interface DashboardData {
  dashboard: Dashboard
  queue: QueueDashboard
  apiKeys: ApiKeysStatus
}

export function AdminDashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [dashboard, queue, apiKeys] = await Promise.all([
        adminApi.dashboard(),
        adminApi.queue(),
        adminApi.apiKeysStatus(),
      ])
      if (!dashboard || !queue || !apiKeys) throw new Error('Resposta inesperada do servidor.')
      setData({ dashboard, queue, apiKeys })
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Não foi possível carregar o painel.')
    }
  }, [])

  // Mesma consulta de `load` (duplicada de propósito, não chamada por
  // referência): o efeito só roda uma vez ao montar, então o corpo fica
  // inline aqui -- chamar `load()` de dentro de um `useEffect` dispara o
  // lint `set-state-in-effect`. `load` continua definida para os usos por
  // evento (botão "Atualizar", retry do ErrorState, recarregar após
  // confirmar uma ação).
  useEffect(() => {
    let cancelled = false
    Promise.all([
      adminApi.dashboard(),
      adminApi.queue(),
      adminApi.apiKeysStatus(),
    ]).then(
      ([dashboard, queue, apiKeys]) => {
        if (cancelled) return
        if (!dashboard || !queue || !apiKeys) {
          setError('Resposta inesperada do servidor.')
          return
        }
        setData({ dashboard, queue, apiKeys })
        setError(null)
      },
      (loadError) => {
        if (cancelled) return
        setError(loadError instanceof Error ? loadError.message : 'Não foi possível carregar o painel.')
      },
    )
    return () => { cancelled = true }
  }, [])

  if (error && !data) return <ErrorState title="Painel indisponível" description={error} onRetry={load} />
  if (!data) return <LoadingState label="Carregando os dados do sistema…" />

  return <AdminDashboardView data={data} onReload={load} />
}

export function AdminDashboardView({ data, onReload }: { data: DashboardData; onReload: () => void }) {
  const { toast } = useToast()
  const { dashboard, queue, apiKeys } = data

  async function runServiceAction(service: string, operation: 'start' | 'restart') {
    try {
      await adminApi.serviceAction({ service, operation, confirmation: true, reason: 'Ação confirmada no painel' })
      toast({ title: operation === 'start' ? 'Serviço iniciado.' : 'Serviço reiniciado.', variant: 'success' })
      onReload()
    } catch (actionError) {
      toast({ title: 'Não foi possível concluir a ação.', description: actionError instanceof Error ? actionError.message : undefined, variant: 'destructive' })
    }
  }

  async function toggleProvider(storeId: string, enable: boolean) {
    try {
      await adminApi.provider(storeId, { enabled: enable, reason: 'Ação confirmada no painel' })
      toast({ title: enable ? 'Loja habilitada.' : 'Loja desabilitada.', variant: 'success' })
      onReload()
    } catch (actionError) {
      toast({ title: 'Não foi possível concluir a ação.', description: actionError instanceof Error ? actionError.message : undefined, variant: 'destructive' })
    }
  }

  return (
    <section>
      <PageHeader
        eyebrow="DEV / ADMIN"
        title="Operação do sistema"
        description="Acompanhe a saúde dos serviços, o volume de uso e as rotinas de operação."
        actions={<Button variant="outline" onClick={onReload}><RefreshCw />Atualizar</Button>}
      />

      <Card className="mb-6">
        <CardHeader><CardTitle className="text-base">Saúde e volume</CardTitle></CardHeader>
        <CardContent>
          <div className="mb-5 flex flex-wrap gap-2">
            <Badge variant={dashboard.api === 'healthy' ? 'success' : 'destructive'}>API: {adminStatusLabel(dashboard.api)}</Badge>
            <Badge variant={dashboard.postgresql === 'healthy' ? 'success' : 'destructive'}>PostgreSQL: {adminStatusLabel(dashboard.postgresql)}</Badge>
            <Badge variant={dashboard.redis === 'healthy' ? 'success' : 'destructive'}>Redis: {adminStatusLabel(dashboard.redis)}</Badge>
          </div>
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat icon={Users} label="Usuários ativos" value={`${dashboard.users.active}/${dashboard.users.total}`} />
            <Stat icon={Workflow} label="Missões ativas" value={`${dashboard.missions.active}/${dashboard.missions.total}`} />
            <Stat icon={Activity} label="Coletas / falhas (24h)" value={`${dashboard.collections.total} / ${dashboard.collections.failed_24h}`} />
            <Stat icon={Workflow} label="Eventos / falhas (24h)" value={`${dashboard.events.total} / ${dashboard.events.failed_24h}`} />
          </dl>
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader><CardTitle className="text-base">Serviços de processamento</CardTitle><CardDescription>Inicie ou reinicie os serviços que executam as tarefas em segundo plano.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {dashboard.workers.map((worker) => (
            <div key={worker.service} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-4">
              <div><p className="font-medium">{worker.service}</p><AdminStatusBadge value={worker.status} /></div>
              <div className="flex gap-2">
                <ConfirmActionButton
                  triggerLabel="Iniciar" triggerIcon={Play}
                  title={`Iniciar ${worker.service}?`} description="O serviço será iniciado agora."
                  onConfirm={() => runServiceAction(worker.service, 'start')}
                />
                <ConfirmActionButton
                  triggerLabel="Reiniciar" triggerIcon={RefreshCw}
                  title={`Reiniciar ${worker.service}?`} description="O serviço será reiniciado agora, interrompendo o que estiver em andamento."
                  onConfirm={() => runServiceAction(worker.service, 'restart')}
                />
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader><CardTitle className="text-base">Lojas e coletas</CardTitle><CardDescription>Desabilitar uma loja interrompe novas coletas sem apagar o histórico.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {dashboard.stores.map((store) => (
            <div key={store.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-4">
              <div><p className="font-medium"><StoreName store={store.code}>{store.name}</StoreName></p><p className="mt-1 text-xs text-muted-foreground">Última coleta: {store.last_run_status ? adminStatusLabel(store.last_run_status) : 'sem informação'}</p></div>
              <ConfirmActionButton
                triggerLabel={store.is_active ? 'Desabilitar' : 'Habilitar'}
                variant={store.is_active ? 'outline' : 'default'}
                title={`${store.is_active ? 'Desabilitar' : 'Habilitar'} ${store.name}?`}
                description={store.is_active ? 'Novas coletas param; o histórico é preservado.' : 'A loja volta a ser coletada normalmente.'}
                destructive={store.is_active}
                onConfirm={() => toggleProvider(store.id, !store.is_active)}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader><CardTitle className="text-base">Fila e intervalos</CardTitle><CardDescription>Veja, sem alterar nada, a ordem e os limites usados na próxima rodada de coletas.</CardDescription></CardHeader>
        <CardContent className="space-y-5">
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><dt className="text-xs text-muted-foreground">Lotes simultâneos por usuário</dt><dd>{queue.config.max_concurrent_user_batches}{queue.config.max_concurrent_user_batches_override != null ? ' (ajustado)' : ' (padrão)'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Cooldown mín./máx.</dt><dd>{queue.config.user_cooldown_min_seconds}s / {queue.config.user_cooldown_max_seconds}s</dd></div>
            <div><dt className="text-xs text-muted-foreground">Intervalo mínimo por loja</dt><dd>{queue.config.store_min_interval_seconds}s</dd></div>
            <div><dt className="text-xs text-muted-foreground">Gerado em</dt><dd>{new Date(queue.generated_at).toLocaleString('pt-BR')}</dd></div>
          </dl>

          <div>
            <p className="mb-2 text-sm font-medium">Usuários na fila</p>
            {queue.users.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nenhum usuário processando ou aguardando agora.</p>
            ) : (
              <div className="space-y-2">
                {queue.users.map((entry) => (
                  <div key={entry.user_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3 text-sm">
                    <span className="font-medium">{entry.display_name}</span>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      {entry.is_processing_now ? <Badge variant="success">Processando agora</Badge> : <Badge variant="secondary">Posição {entry.queue_position}</Badge>}
                      {entry.cooldown_active ? <Badge variant="warning">Intervalo de espera ativo</Badge> : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div>
            <p className="mb-2 text-sm font-medium">Limite temporário por loja</p>
            <div className="flex flex-wrap gap-2">
              {queue.stores.map((store) => (
                <Badge key={store.store_id} variant={store.throttled ? 'warning' : 'secondary'}><StoreName store={store.code}>{store.name}{store.throttled ? ' · em espera' : ''}</StoreName></Badge>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base flex items-center gap-2"><Server className="size-4" />API para agentes</CardTitle></CardHeader>
        <CardContent>
          <Badge variant={apiKeys.enabled ? 'success' : 'secondary'}>{apiKeys.message}</Badge>
        </CardContent>
      </Card>
    </section>
  )
}

function Stat({ icon: Icon, label, value }: { icon: typeof Activity; label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border p-4">
      <Icon className="mb-2 size-4 text-primary" />
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold">{value}</dd>
    </div>
  )
}
