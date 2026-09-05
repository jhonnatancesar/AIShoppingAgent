import { useEffect, useState, type ReactNode } from 'react'
import { ArrowLeft, ExternalLink, ShoppingBag } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { offersApi } from '../../api/offers'
import type {
  MarketplacePartyKind,
  OfferAvailability,
  OfferComparison,
  OfferDetail,
  OfferInstallment,
} from '../../api/types'
import { ConditionBadge } from '../../components/ConditionBadge'
import { PageHeader } from '../../components/PageHeader'
import { PriceHistoryChart } from '../../components/PriceHistoryChart'
import { EmptyState, ErrorState, LoadingState } from '../../components/StatePanel'
import { StoreName } from '@/components/StoreMark'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent } from '../../components/ui/card'
import { useImageFallbackChain } from '../../hooks/useImageFallbackChain'

const AVAILABILITY_LABELS: Record<OfferAvailability, string> = {
  available: 'Disponível',
  unavailable: 'Indisponível',
  unknown: 'Disponibilidade não confirmada',
}

const PARTY_LABELS: Record<MarketplacePartyKind, string> = {
  platform: 'Própria loja',
  marketplace_partner: 'Parceiro do marketplace',
  unknown: 'Não identificado',
}

function money(value: string, currency: string) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(Number(value))
}

function dateTime(value: string) {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
}

function ratingAverage(value: string) {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(Number(value))
}

function installmentLabel(item: OfferInstallment, currency: string) {
  const base = `${item.installment_count}x de ${money(item.installment_amount, currency)}${item.interest_kind === 'interest_free' ? ' sem juros' : ''}`
  return item.installment_total_amount ? `${base} — total ${money(item.installment_total_amount, currency)}` : base
}

// Subtask 4 (auditoria GG Oferta, revisão): componente próprio (não só um
// bloco inline) para poder receber `key={offer.id}` no chamador -- força
// o hook de fallback a resetar a tentativa ao navegar para outra oferta,
// em vez de herdar o estado de falha da oferta anterior.
function OfferImage({ offer }: { offer: OfferDetail }) {
  const { src, onError } = useImageFallbackChain([offer.image_url, offer.image_fallback_url])
  return (
    <Card className="grid min-h-64 place-items-center overflow-hidden lg:min-h-full">
      {src ? (
        <img className="max-h-96 max-w-full object-contain p-6" src={src} alt={offer.title} onError={onError} />
      ) : (
        <div className="flex flex-col items-center gap-2 p-8 text-muted-foreground">
          <ShoppingBag className="size-10" />
          <p className="text-sm">Imagem não disponível.</p>
        </div>
      )}
    </Card>
  )
}

export function OfferDetailView({ offer, comparison }: { offer: OfferDetail; comparison?: OfferComparison | null }) {
  const observation = offer.latest_observation

  return (
    <section>
      <PageHeader eyebrow={offer.store.name} title={offer.title} />

      <div className="grid gap-5 lg:grid-cols-[minmax(14rem,1fr)_minmax(20rem,1.4fr)]">
        <OfferImage offer={offer} key={offer.id} />

        <Card>
          <CardContent className="space-y-4 pt-6">
            {offer.rating ? (
              <p className="text-sm text-muted-foreground">
                ⭐ {ratingAverage(offer.rating.average)} ·{' '}
                {offer.rating.review_count.toLocaleString('pt-BR')}{' '}
                {offer.rating.review_count === 1 ? 'avaliação' : 'avaliações'}
              </p>
            ) : null}

            {observation ? (
              <div className="space-y-3">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Preço à vista</p>
                  <p className="text-3xl font-bold tracking-tight">{money(observation.amount, observation.currency)}</p>
                </div>
                {observation.installments.length > 0 ? (
                  <ul className="space-y-1.5 text-sm">
                    {observation.installments.map((item) => (
                      <li key={item.installment_count} className="flex items-center gap-2">
                        {item.is_highlighted ? <Badge>Destaque</Badge> : null}
                        <span className={item.is_highlighted ? 'font-medium' : 'text-muted-foreground'}>{installmentLabel(item, observation.currency)}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
                  {observation.shipping_amount !== null ? <span>Frete {money(observation.shipping_amount, observation.currency)}</span> : null}
                  <span>Total {money(observation.total_amount, observation.currency)}</span>
                </div>
                <Button asChild size="lg"><a href={offer.original_url} target="_blank" rel="noreferrer">Ver na loja <ExternalLink /></a></Button>
                <p className="text-xs text-muted-foreground">Atualizado em {dateTime(observation.observed_at)}</p>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">Preço ainda não coletado para esta oferta.</p>
                <Button asChild size="lg" variant="outline"><a href={offer.original_url} target="_blank" rel="noreferrer">Ver na loja <ExternalLink /></a></Button>
              </div>
            )}

            <OfferFacts offer={offer} observation={observation} />
          </CardContent>
        </Card>
      </div>

      <ComparisonSection comparison={comparison} currentOfferId={offer.id} />

      <div className="mt-6">
        <PriceHistoryChart offerId={offer.id} />
      </div>

      <div className="mt-6 flex flex-col items-start justify-between gap-3 border-t border-border pt-4 sm:flex-row sm:items-center">
        <p className="text-sm text-muted-foreground">Oferta vista pela última vez em {dateTime(offer.last_seen_at)}.</p>
        <Button variant="outline" asChild><Link to="/app/missions"><ArrowLeft />Voltar para missões</Link></Button>
      </div>
    </section>
  )
}

function OfferFacts({ offer, observation }: { offer: OfferDetail; observation: OfferDetail['latest_observation'] }) {
  const facts: [string, ReactNode][] = [['Loja', <StoreName key="store" store={offer.store.code}>{offer.store.name}</StoreName>]]
  if (offer.seller?.name) facts.push(['Vendedor', offer.seller.name])
  if (observation?.seller_kind) facts.push(['Tipo de vendedor', PARTY_LABELS[observation.seller_kind]])
  const delivery = observation?.fulfillment ?? (observation?.fulfillment_kind ? PARTY_LABELS[observation.fulfillment_kind] : null)
  if (delivery) facts.push(['Entrega', delivery])
  if (observation) facts.push(['Condição', <ConditionBadge key="condition" condition={observation.condition} />])
  if (observation) facts.push(['Disponibilidade', AVAILABILITY_LABELS[observation.availability]])
  return (
    <dl className="divide-y divide-border border-t border-border text-sm">
      {facts.map(([label, value]) => (
        <div key={label} className="grid grid-cols-[8rem_1fr] items-center gap-3 py-2.5">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="m-0">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function ComparisonSection({ comparison, currentOfferId }: { comparison?: OfferComparison | null; currentOfferId: string }) {
  if (comparison === undefined) return null

  const heading = <h2 className="mb-3 text-lg font-semibold tracking-tight">Comparar entre lojas</h2>

  if (comparison === null) {
    return <div className="mt-6">{heading}<EmptyState title="Não foi possível comparar agora" description="Tente novamente em alguns instantes." /></div>
  }
  if (!comparison.comparable) {
    return <div className="mt-6">{heading}<EmptyState title="Ainda não dá para comparar esta oferta" description="Precisamos confirmar o modelo exato antes de colocá-lo lado a lado com outras lojas." /></div>
  }

  const currentTotal = comparison.offers.find((item) => item.id === currentOfferId)?.latest_observation?.total_amount
  const stores = Array.from(new Set(comparison.offers.map((item) => item.store.code)))

  return (
    <div className="mt-6">
      {heading}
      <p className="mb-4 text-sm text-muted-foreground">
        Somente {comparison.title}{comparison.variant ? ` — ${comparison.variant}` : ''}. Variantes diferentes nunca entram nesta comparação.
      </p>
      {comparison.offers.length === 0 ? (
        <EmptyState title="Nenhuma outra loja com este produto por enquanto" description="Quando encontrarmos o mesmo modelo em outra loja, a comparação aparece aqui." />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {stores.map((storeCode) => {
            const items = comparison.offers.filter((item) => item.store.code === storeCode)
            return (
              <Card key={storeCode}>
                <CardContent className="space-y-3 pt-6">
                  <p className="text-sm font-semibold"><StoreName store={storeCode}>{items[0].store.name}</StoreName></p>
                  {items.map((item) => (
                    <ComparisonOffer key={item.id} item={item} isCurrent={item.id === currentOfferId} currentTotal={currentTotal} />
                  ))}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ComparisonOffer({
  item,
  isCurrent,
  currentTotal,
}: {
  item: OfferComparison['offers'][number]
  isCurrent: boolean
  currentTotal: string | undefined
}) {
  const observation = item.latest_observation
  // Delta só quando os dois lados têm preço real e comparável -- nunca
  // calculado a partir de valor ausente/estimado.
  const delta = !isCurrent && observation && currentTotal
    ? Number(observation.total_amount) - Number(currentTotal)
    : null

  return (
    <div className={`rounded-lg border p-3 ${isCurrent ? 'border-primary bg-primary/5' : 'border-border'}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium">{item.seller?.name ?? item.store.name}</p>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {observation ? <><ConditionBadge condition={observation.condition} /> · {AVAILABILITY_LABELS[observation.availability]}</> : 'Sem preço atual'}
          </p>
        </div>
        {isCurrent ? <Badge>Esta oferta</Badge> : null}
      </div>
      {observation ? (
        <>
          <p className="mt-2 text-lg font-semibold">{money(observation.total_amount, observation.currency)}</p>
          <p className="text-xs text-muted-foreground">
            À vista {money(observation.amount, observation.currency)}
            {observation.shipping_amount === null ? ' · frete não informado' : ` · frete ${money(observation.shipping_amount, observation.currency)}`}
          </p>
          {delta !== null && delta !== 0 ? (
            <p className={`text-xs font-medium ${delta < 0 ? 'text-success' : 'text-muted-foreground'}`}>
              {delta < 0
                ? `▼ ${money(String(Math.abs(delta)), observation.currency)} mais barato que a oferta atual`
                : `▲ ${money(String(delta), observation.currency)} mais caro que a oferta atual`}
            </p>
          ) : null}
          {observation.installments[0] ? <p className="mt-1 text-xs text-muted-foreground">{installmentLabel(observation.installments[0], observation.currency)}</p> : null}
        </>
      ) : null}
      {item.rating ? <p className="mt-1 text-xs text-muted-foreground">⭐ {ratingAverage(item.rating.average)} · {item.rating.review_count.toLocaleString('pt-BR')}</p> : null}
      <div className="mt-3 flex gap-2">
        <Button size="sm" variant="outline" asChild><Link to={`/app/offers/${item.id}`}>Detalhes</Link></Button>
        <Button size="sm" variant="outline" asChild><a href={item.original_url} target="_blank" rel="noreferrer">Ver na loja</a></Button>
      </div>
    </div>
  )
}

export function OfferDetailPage() {
  const { offerId } = useParams<{ offerId: string }>()
  const [offer, setOffer] = useState<OfferDetail | null>(null)
  const [comparison, setComparison] = useState<OfferComparison | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  // Marca de qual offerId são os dados acima -- enquanto ela não bater com o
  // offerId atual da rota, tratamos como "ainda carregando" na renderização,
  // em vez de zerar offer/comparison/error de forma síncrona no efeito.
  const [loadedOfferId, setLoadedOfferId] = useState<string | null>(null)

  useEffect(() => {
    if (!offerId) return
    let cancelled = false
    offersApi.get(offerId).then(
      (loadedOffer) => {
        if (cancelled) return
        setOffer(loadedOffer)
        setComparison(undefined)
        setError(null)
        setLoadedOfferId(offerId)
        offersApi.compare(offerId).then(
          (loadedComparison) => { if (!cancelled) setComparison(loadedComparison) },
          () => { if (!cancelled) setComparison(null) },
        )
      },
      (loadError) => {
        if (cancelled) return
        setOffer(null)
        setComparison(undefined)
        setLoadedOfferId(offerId)
        if (loadError instanceof ApiError && loadError.status === 403) {
          setError('Você não tem acesso a esta oferta ou ela não foi encontrada.')
        } else if (loadError instanceof ApiError && loadError.status === 404) {
          setError('Oferta não encontrada.')
        } else {
          setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar a oferta.')
        }
      },
    )
    return () => { cancelled = true }
  }, [offerId])

  const isCurrent = loadedOfferId === offerId

  if (!offerId || (isCurrent && error)) {
    return (
      <section>
        <ErrorState title="Não foi possível abrir esta oferta" description={error ?? 'Oferta não encontrada.'} action={<Button variant="outline" asChild><Link to="/app/missions"><ArrowLeft />Voltar para missões</Link></Button>} />
      </section>
    )
  }
  if (!isCurrent || !offer) return <LoadingState label="Carregando oferta…" />
  return <OfferDetailView offer={offer} comparison={comparison} />
}
