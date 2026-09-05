import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, Pause, Play, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { missionsApi } from '../../api/missions'
import type { MissionDetail, MissionOfferLink, QuotaErrorDetails } from '../../api/types'
import { STATUS_BADGE_VARIANT, STATUS_LABELS, STORE_LABELS } from './statusLabels'
import { StoreName } from '@/components/StoreMark'
import { StoreSelectionField, TargetPriceFields } from './MissionFormFields'
import { formatPriceDisplay, toApiDecimal } from './priceFormat'
import { OfferCard, type OfferCardData } from '@/components/OfferCard'
import { PageHeader } from '@/components/PageHeader'
import { QuotaExceededNotice } from '@/components/QuotaExceededNotice'
import { quotaDetailsFromError } from '@/components/quotaDetails'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { FormMessage } from '@/components/FormMessage'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ToggleGroup } from '@/components/ui/toggle-group'
import { useToast } from '@/hooks/toastContext'

function money(value: string, currency: string) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(Number(value))
}

function toOfferCardData(offer: MissionOfferLink): OfferCardData {
  return {
    id: offer.id,
    title: offer.title,
    imageUrl: null,
    store: { name: offer.store_name },
    price: null,
    condition: offer.condition,
  }
}

export function MissionDetailPage() {
  const { missionId } = useParams<{ missionId: string }>()
  const [mission, setMission] = useState<MissionDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!missionId) return
    setLoadError(null)
    try {
      setMission(await missionsApi.get(missionId))
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : 'Não foi possível carregar a missão.')
    }
  }, [missionId])

  // Mesma consulta de `load` (duplicada de propósito, não chamada por
  // referência): o efeito só precisa rodar quando o missionId da rota
  // mudar, mas chamar `load()` de dentro de um `useEffect` dispara o lint
  // `set-state-in-effect`. `load` continua definida para os usos por
  // evento (recarregar após pausar/retomar/cancelar/editar/selecionar
  // variantes).
  useEffect(() => {
    if (!missionId) return
    let cancelled = false
    missionsApi.get(missionId).then(
      (loadedMission) => {
        if (cancelled) return
        setMission(loadedMission)
        setLoadError(null)
      },
      (error) => {
        if (cancelled) return
        setLoadError(error instanceof ApiError ? error.message : 'Não foi possível carregar a missão.')
      },
    )
    return () => { cancelled = true }
  }, [missionId])

  if (loadError && !mission) {
    return (
      <ErrorState
        title="Não foi possível abrir esta missão"
        description={loadError}
        action={<Button variant="outline" asChild><Link to="/app/missions"><ArrowLeft />Voltar para missões</Link></Button>}
      />
    )
  }

  if (!mission) {
    return <LoadingState label="Carregando missão…" />
  }

  return <MissionDetailView mission={mission} onReload={load} />
}

export function MissionDetailView({ mission, onReload }: { mission: MissionDetail; onReload: () => void }) {
  const { toast } = useToast()
  const [error, setError] = useState<string | null>(null)
  const [quotaError, setQuotaError] = useState<QuotaErrorDetails | null>(null)
  const [actionPending, setActionPending] = useState(false)

  async function runAction(
    action: (id: string, version: number) => Promise<unknown>,
    successMessage: string,
  ) {
    setActionPending(true)
    setError(null)
    setQuotaError(null)
    try {
      await action(mission.id, mission.state_version)
      toast({ title: successMessage, variant: 'success' })
      onReload()
    } catch (actionError) {
      if (actionError instanceof ApiError && actionError.code === 'mission_version_conflict') {
        // Optimistic locking: nunca força a alteração nem ignora o
        // conflito -- recarrega os dados atuais para que o usuário revise
        // antes de tentar de novo (a versão em tela já estava obsoleta).
        setError(
          'Esta missão foi alterada enquanto você estava nesta página. ' +
            'Atualizamos os dados abaixo para a versão mais recente -- revise antes de tentar novamente.',
        )
        onReload()
        return
      }
      if (actionError instanceof ApiError) {
        // TASK-107: `resume` pode ser recusado por cota -- mostra motivo
        // e ações reais, nunca só "não foi possível concluir a ação".
        const details = quotaDetailsFromError(actionError)
        if (details) setQuotaError(details)
        setError(actionError.message)
        return
      }
      setError('Não foi possível concluir a ação.')
    } finally {
      setActionPending(false)
    }
  }

  return (
    <section>
      <PageHeader
        eyebrow="Monitoramento"
        title={mission.title}
        actions={
          <>
            <Badge variant={STATUS_BADGE_VARIANT[mission.status]}>{STATUS_LABELS[mission.status]}</Badge>
            {mission.status === 'active' ? (
              <Button variant="outline" disabled={actionPending} onClick={() => runAction(missionsApi.pause, 'Missão pausada.')}>
                <Pause />Pausar
              </Button>
            ) : null}
            {mission.status === 'paused' ? (
              <Button disabled={actionPending} onClick={() => runAction(missionsApi.resume, 'Missão retomada.')}>
                <Play />Retomar
              </Button>
            ) : null}
            {mission.status === 'active' || mission.status === 'paused' ? (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="destructive" disabled={actionPending}><X />Cancelar</Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Cancelar esta missão?</AlertDialogTitle>
                    <AlertDialogDescription>O GG Oferta para de acompanhar este produto. Essa ação não pode ser desfeita.</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Voltar</AlertDialogCancel>
                    <AlertDialogAction variant="destructive" onClick={() => runAction(missionsApi.cancel, 'Missão cancelada.')}>Cancelar missão</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}
          </>
        }
      />

      {quotaError ? (
        <div className="mb-6"><QuotaExceededNotice message={error ?? ''} details={quotaError} /></div>
      ) : error ? (
        <div className="mb-6"><FormMessage tone="error">{error}</FormMessage></div>
      ) : null}

      <Card className="mb-6">
        <CardHeader><CardTitle className="text-base">Critério</CardTitle></CardHeader>
        <CardContent>
          <dl className="grid gap-3 sm:grid-cols-2">
            <div><dt className="text-xs text-muted-foreground">Busca</dt><dd className="text-sm">{mission.criteria?.search_query ?? '—'}</dd></div>
            {mission.criteria?.model ? (
              <div><dt className="text-xs text-muted-foreground">Modelo</dt><dd className="text-sm">{mission.criteria.model}</dd></div>
            ) : null}
            <div>
              <dt className="text-xs text-muted-foreground">Tipo</dt>
              <dd className="text-sm">
                {mission.criteria?.request_kind === 'specific_product'
                  ? 'Produto específico'
                  : mission.criteria?.request_kind === 'product_family'
                    ? 'Família de produtos'
                    : 'Categoria genérica'}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Preço-alvo</dt>
              <dd className="text-sm">
                {mission.criteria?.target_amount
                  ? money(mission.criteria.target_amount, mission.criteria.target_currency ?? 'BRL')
                  : 'Sem preço-alvo definido'}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Lojas</dt>
              <dd className="mt-1 flex flex-wrap gap-2 text-sm">
                {mission.sources.length === 0
                  ? 'Nenhuma loja selecionada'
                  : mission.sources.map((source) => <StoreName key={source.store_code} store={source.store_code}>{STORE_LABELS[source.store_code] ?? source.store_code}</StoreName>)}
              </dd>
            </div>
            {mission.schedule ? (
              <div><dt className="text-xs text-muted-foreground">Agenda</dt><dd className="text-sm">A cada {mission.schedule.interval_minutes} minutos</dd></div>
            ) : null}
          </dl>
          {mission.criteria?.request_kind === 'generic_category' ? (
            <p className="mt-4 text-xs text-muted-foreground">Esta missão acompanha uma categoria inteira, então você não precisa escolher um produto específico.</p>
          ) : null}
        </CardContent>
      </Card>

      {mission.criteria?.request_kind === 'product_family' ? (
        <VariantSelection key={`${mission.state_version}-${mission.criteria.variant_selection_mode}`} mission={mission} onSaved={onReload} />
      ) : null}

      {mission.status === 'paused' ? (
        // `key` força remontar o formulário quando `state_version` muda
        // (inclusive após um recarregamento disparado por 409) -- os
        // campos locais (useState) precisam refletir os dados atuais do
        // servidor antes de uma nova tentativa, nunca os que o usuário
        // via antes do conflito.
        <EditMissionForm key={mission.state_version} mission={mission} onSaved={onReload} toast={toast} />
      ) : null}

      <Card className="mb-6">
        <CardHeader><CardTitle className="text-base">Histórico</CardTitle></CardHeader>
        <CardContent>
          {mission.transitions.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhuma transição registrada ainda.</p>
          ) : (
            <ul className="space-y-1.5 text-sm text-muted-foreground">
              {mission.transitions.map((transition, index) => (
                <li key={index}>{STATUS_LABELS[transition.from_status]} → {STATUS_LABELS[transition.to_status]} ({transition.transitioned_at})</li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <h2 className="mb-3 text-lg font-semibold tracking-tight">Ofertas relevantes</h2>
      {mission.offers.length === 0 ? (
        <EmptyState title="Nenhuma oferta relevante ainda" description="Assim que o GG Oferta encontrar uma oferta compatível, ela aparece aqui." />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {mission.offers.map((offer) => (
            <OfferCard key={offer.id} offer={toOfferCardData(offer)} action={{ label: 'Ver detalhes', to: `/app/offers/${offer.id}` }} />
          ))}
        </div>
      )}

      <div className="mt-8">
        <Button variant="outline" asChild><Link to="/app/missions"><ArrowLeft />Voltar para missões</Link></Button>
      </div>
    </section>
  )
}

function VariantSelection({
  mission,
  onSaved,
}: {
  mission: MissionDetail
  onSaved: () => void
}) {
  const [selected, setSelected] = useState<string[]>(
    mission.available_variants.filter((item) => item.selected).map((item) => item.product_id),
  )
  const [selectAll, setSelectAll] = useState(
    mission.criteria?.variant_selection_mode === 'all',
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await missionsApi.selectVariants(mission.id, {
        expected_state_version: mission.state_version,
        product_ids: selectAll ? [] : selected,
        select_all: selectAll,
      })
      onSaved()
    } catch (saveError) {
      setError(
        saveError instanceof ApiError
          ? saveError.message
          : 'Não foi possível salvar as variantes.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="mb-6">
      <CardHeader><CardTitle className="text-base">Variantes encontradas</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        {mission.available_variants.length === 0 ? (
          <p className="text-sm text-muted-foreground">Ainda estamos separando os modelos encontrados para você escolher com tranquilidade.</p>
        ) : (
          <>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 rounded border-input"
                checked={selectAll}
                onChange={(event) => setSelectAll(event.target.checked)}
              />
              Todas as variantes desta família
            </label>
            {!selectAll ? (
              <ToggleGroup
                type="multiple"
                aria-label="Variantes"
                value={selected}
                onChange={setSelected}
                options={mission.available_variants.map((variant) => ({ value: variant.product_id, label: variant.label }))}
              />
            ) : null}
            <FormMessage tone="error">{error}</FormMessage>
            <Button disabled={saving || (!selectAll && selected.length === 0)} onClick={save}>
              {saving ? 'Salvando…' : 'Salvar variantes'}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function EditMissionForm({
  mission,
  onSaved,
  toast,
}: {
  mission: MissionDetail
  onSaved: () => void
  toast: (input: { title: string; description?: string; variant?: 'default' | 'success' | 'destructive' }) => void
}) {
  // `target_amount` vem da API como Numeric(19,4) (ex.: "4500.0000") --
  // formatado para o padrão visual oficial pt-BR ("4.500,00") antes de
  // entrar no campo editável, nunca o valor bruto da API.
  const [targetAmount, setTargetAmount] = useState(
    mission.criteria?.target_amount ? formatPriceDisplay(mission.criteria.target_amount) : '',
  )
  const [targetCurrency, setTargetCurrency] = useState(mission.criteria?.target_currency ?? 'BRL')
  const [clearTarget, setClearTarget] = useState(false)
  const [sourceCodes, setSourceCodes] = useState<string[]>(mission.sources.map((source) => source.store_code))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await missionsApi.edit(mission.id, {
        expected_state_version: mission.state_version,
        target_amount: clearTarget ? null : targetAmount.trim() ? toApiDecimal(targetAmount) : undefined,
        target_currency: clearTarget ? null : targetAmount.trim() ? targetCurrency : undefined,
        clear_target: clearTarget,
        source_codes: sourceCodes.length > 0 ? sourceCodes : undefined,
      })
      toast({ title: 'Missão atualizada.', variant: 'success' })
      onSaved()
    } catch (submitError) {
      if (submitError instanceof ApiError && submitError.code === 'mission_version_conflict') {
        setError(
          'Esta missão foi alterada enquanto você estava nesta página. ' +
            'Atualizamos os dados para a versão mais recente -- revise antes de tentar novamente.',
        )
        onSaved() // recarrega a missão no componente pai; o formulário remonta com os dados atuais
        return
      }
      setError(
        submitError instanceof ApiError
          ? submitError.message
          : 'Não foi possível salvar as alterações.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="mb-6">
      <CardHeader><CardTitle className="text-base">Editar critério</CardTitle></CardHeader>
      <CardContent>
        <form className="space-y-5" onSubmit={handleSubmit}>
          <TargetPriceFields
            targetAmount={targetAmount}
            onTargetAmountChange={setTargetAmount}
            targetCurrency={targetCurrency}
            onTargetCurrencyChange={setTargetCurrency}
            clearTarget={clearTarget}
            onClearTargetChange={setClearTarget}
          />
          <StoreSelectionField selected={sourceCodes} onChange={setSourceCodes} hint="Vazio = mantém as lojas atuais." />
          <FormMessage tone="error">{error}</FormMessage>
          <Button type="submit" disabled={saving}>{saving ? 'Salvando…' : 'Salvar alterações'}</Button>
        </form>
      </CardContent>
    </Card>
  )
}
