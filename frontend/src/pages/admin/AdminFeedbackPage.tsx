import { useCallback, useEffect, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { adminApi, type FeedbackChannel, type FeedbackItem, type FeedbackKind, type FeedbackListResponse, type FeedbackStatus } from '@/api/admin'
import { ApiError } from '@/api/client'
import { FormMessage } from '@/components/FormMessage'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/toastContext'
import { formatDateTime } from '@/lib/formatDateTime'

const PAGE_SIZE = 20
const ALL = 'all'

const KIND_LABELS: Record<FeedbackKind, string> = { bug: 'Bug', support: 'Suporte', store_suggestion: 'Sugestão de loja' }
const CHANNEL_LABELS: Record<FeedbackChannel, string> = { web: 'Web', telegram: 'Telegram' }
const STATUS_LABELS: Record<FeedbackStatus, string> = { new: 'Novo', reviewed: 'Revisado', closed: 'Fechado' }
const STATUS_VARIANT: Record<FeedbackStatus, 'warning' | 'secondary' | 'outline'> = { new: 'warning', reviewed: 'secondary', closed: 'outline' }

export function AdminFeedbackPage() {
  const [status, setStatus] = useState<FeedbackStatus | null>(null)
  const [kind, setKind] = useState<FeedbackKind | null>(null)
  const [offset, setOffset] = useState(0)
  const [result, setResult] = useState<FeedbackListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (currentStatus: FeedbackStatus | null, currentKind: FeedbackKind | null, currentOffset: number) => {
    setError(null)
    try {
      const response = await adminApi.feedback({ status: currentStatus ?? undefined, kind: currentKind ?? undefined, limit: PAGE_SIZE, offset: currentOffset })
      if (!response) throw new Error('Resposta inesperada do servidor.')
      setResult(response)
    } catch (loadError) {
      setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar o feedback.')
    }
  }, [])

  // Mesma consulta de `load` (duplicada de propósito, não chamada por
  // referência): o efeito precisa rodar sempre que status/kind/offset
  // mudarem, mas chamar `load(...)` de dentro de um `useEffect` dispara o
  // lint `set-state-in-effect`. `load` continua definida para os usos por
  // evento (retry do ErrorState, onChanged após atualizar o status de um
  // item).
  useEffect(() => {
    let cancelled = false
    adminApi.feedback({ status: status ?? undefined, kind: kind ?? undefined, limit: PAGE_SIZE, offset }).then(
      (response) => {
        if (cancelled) return
        if (!response) {
          setError('Resposta inesperada do servidor.')
          return
        }
        setResult(response)
        setError(null)
      },
      (loadError) => {
        if (cancelled) return
        setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar o feedback.')
      },
    )
    return () => { cancelled = true }
  }, [status, kind, offset])

  function changeStatus(value: string) {
    setStatus(value === ALL ? null : (value as FeedbackStatus))
    setOffset(0)
  }
  function changeKind(value: string) {
    setKind(value === ALL ? null : (value as FeedbackKind))
    setOffset(0)
  }

  return (
    <section>
      <PageHeader eyebrow="Administração" title="Feedback" description="Acompanhe pedidos de ajuda, problemas relatados e novas lojas sugeridas." />

      <div className="mb-6 grid gap-4 sm:grid-cols-2 sm:max-w-md">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Status</label>
          <Select value={status ?? ALL} onValueChange={changeStatus}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todos</SelectItem>
              {(Object.keys(STATUS_LABELS) as FeedbackStatus[]).map((value) => <SelectItem key={value} value={value}>{STATUS_LABELS[value]}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Tipo</label>
          <Select value={kind ?? ALL} onValueChange={changeKind}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todos</SelectItem>
              {(Object.keys(KIND_LABELS) as FeedbackKind[]).map((value) => <SelectItem key={value} value={value}>{KIND_LABELS[value]}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>

      {error && !result ? (
        <ErrorState title="Não foi possível carregar o feedback" description={error} onRetry={() => load(status, kind, offset)} />
      ) : !result ? (
        <LoadingState label="Carregando feedback…" />
      ) : (
        <AdminFeedbackView result={result} onPage={setOffset} onChanged={() => load(status, kind, offset)} />
      )}
    </section>
  )
}

export function AdminFeedbackView({
  result,
  onPage = () => undefined,
  onChanged = () => undefined,
}: {
  result: FeedbackListResponse
  onPage?: (offset: number) => void
  onChanged?: () => void
}) {
  if (result.items.length === 0) return <EmptyState title="Nenhum registro encontrado" description="Experimente outro filtro." />
  const end = Math.min(result.offset + result.items.length, result.total)
  return (
    <>
      <div className="mb-4 flex items-center justify-between text-sm text-muted-foreground">
        <span>{result.total} registro(s)</span>
        <span>{result.offset + 1}–{end} de {result.total}</span>
      </div>
      <div className="space-y-3">
        {result.items.map((item) => <FeedbackRow key={item.id} item={item} onChanged={onChanged} />)}
      </div>
      <div className="mt-7 flex justify-center gap-2">
        <Button variant="outline" disabled={result.offset === 0} onClick={() => onPage(Math.max(0, result.offset - result.limit))}>Anterior</Button>
        <Button variant="outline" disabled={result.offset + result.limit >= result.total} onClick={() => onPage(result.offset + result.limit)}>Próxima</Button>
      </div>
    </>
  )
}

function FeedbackRow({ item, onChanged }: { item: FeedbackItem; onChanged: () => void }) {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function setStatus(status: FeedbackStatus) {
    setSaving(true)
    setError(null)
    try {
      await adminApi.updateFeedbackStatus(item.id, status)
      toast({ title: status === 'reviewed' ? 'Marcado como revisado.' : 'Marcado como fechado.', variant: 'success' })
      onChanged()
    } catch (statusError) {
      setError(statusError instanceof ApiError ? statusError.message : 'Não foi possível atualizar o status.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <button type="button" className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen((current) => !current)}>
          <Badge variant={STATUS_VARIANT[item.status]}>{STATUS_LABELS[item.status]}</Badge>
          <Badge variant="secondary">{KIND_LABELS[item.kind]}</Badge>
          <span className="text-sm font-medium">{item.user_display_name || 'Usuário anônimo'}</span>
          <span className="text-xs text-muted-foreground">· {CHANNEL_LABELS[item.channel]} · {formatDateTime(item.created_at)}</span>
        </button>

        {open ? (
          <div className="space-y-3 border-t border-border pt-3">
            {item.message ? <p className="text-sm">{item.message}</p> : <p className="text-sm text-muted-foreground">Sem mensagem.</p>}
            {item.store_name ? (
              <p className="flex items-center gap-1.5 text-sm">
                Loja sugerida: <span className="font-medium">{item.store_name}</span>
                {item.store_url ? <a className="inline-flex items-center gap-1 text-primary hover:underline" href={item.store_url} target="_blank" rel="noreferrer">abrir <ExternalLink className="size-3.5" /></a> : null}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              {item.status !== 'reviewed' ? <Button size="sm" variant="outline" disabled={saving} onClick={() => setStatus('reviewed')}>Marcar revisado</Button> : null}
              {item.status !== 'closed' ? <Button size="sm" variant="outline" disabled={saving} onClick={() => setStatus('closed')}>Marcar fechado</Button> : null}
            </div>
            <FormMessage tone="error">{error}</FormMessage>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
