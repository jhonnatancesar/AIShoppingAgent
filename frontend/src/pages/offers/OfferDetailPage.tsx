import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { offersApi } from '../../api/offers'
import type {
  MarketplacePartyKind,
  OfferAvailability,
  OfferComparison,
  OfferDetail,
} from '../../api/types'
import { ConditionBadge } from '../../components/ConditionBadge'
import { PriceHistoryChart } from '../../components/PriceHistoryChart'

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
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency,
  }).format(Number(value))
}

function dateTime(value: string) {
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value))
}

function ratingAverage(value: string) {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(
    Number(value),
  )
}

export function OfferDetailView({ offer, comparison }: { offer: OfferDetail; comparison?: OfferComparison | null }) {
  const observation = offer.latest_observation
  return (
    <section>
      <div className="page-header">
        <div>
          <p className="offer-store">{offer.store.name}</p>
          <h1>{offer.title}</h1>
        </div>
      </div>

      <div className="offer-detail-grid">
        <div className={`offer-image-panel${offer.image_url ? '' : ' offer-image-panel--empty'}`}>
          {offer.image_url ? (
            <img
              className="offer-image"
              src={offer.image_url}
              alt={offer.title}
              onError={(event) => {
                event.currentTarget.style.display = 'none'
              }}
            />
          ) : (
            <p className="field-hint">Imagem não disponível.</p>
          )}
        </div>

        <div className="offer-facts">
          {offer.rating ? (
            <p className="offer-rating">
              ⭐ {ratingAverage(offer.rating.average)} ·{' '}
              {offer.rating.review_count.toLocaleString('pt-BR')}{' '}
              {offer.rating.review_count === 1 ? 'avaliação' : 'avaliações'}
            </p>
          ) : null}

          {observation ? (
            <div className="offer-price-panel">
              <p className="offer-price-label">Preço à vista</p>
              <p className="offer-price">{money(observation.amount, observation.currency)}</p>
              {observation.installments.length > 0 ? (
                <ul className="installment-list">
                  {observation.installments.map((item) => (
                    <li key={item.installment_count}>
                      {item.installment_count}x de {money(item.installment_amount, observation.currency)}
                      {item.interest_kind === 'interest_free' ? ' sem juros' : ''}
                      {item.installment_total_amount
                        ? ` — total ${money(item.installment_total_amount, observation.currency)}`
                        : ''}
                    </li>
                  ))}
                </ul>
              ) : null}
              <a className="button" href={offer.original_url} target="_blank" rel="noreferrer">
                Ver anúncio
              </a>
              {observation.shipping_amount !== null ? (
                <p>Frete: {money(observation.shipping_amount, observation.currency)}</p>
              ) : null}
              <p>Total: {money(observation.total_amount, observation.currency)}</p>
              <p className="field-hint">Atualizado em {dateTime(observation.observed_at)}</p>
            </div>
          ) : (
            <>
              <p className="field-hint">Ainda não há observação comercial disponível.</p>
              <a className="button" href={offer.original_url} target="_blank" rel="noreferrer">
                Ver anúncio
              </a>
            </>
          )}

          <OfferFacts offer={offer} observation={observation} />
        </div>
      </div>

      <ComparisonSection comparison={comparison} currentOfferId={offer.id} />

      <PriceHistoryChart offerId={offer.id} />

      <p className="field-hint">Oferta vista pela última vez em {dateTime(offer.last_seen_at)}.</p>
      <div className="mission-actions">
        <Link className="button button-secondary" to="/app/missions">
          Voltar para missões
        </Link>
      </div>
    </section>
  )
}

function OfferFacts({ offer, observation }: { offer: OfferDetail; observation: OfferDetail['latest_observation'] }) {
  const facts: [string, ReactNode][] = [['Loja', offer.store.name]]
  if (offer.seller?.name) facts.push(['Vendedor', offer.seller.name])
  if (observation?.seller_kind) facts.push(['Tipo de vendedor', PARTY_LABELS[observation.seller_kind]])
  const delivery = observation?.fulfillment ?? (observation?.fulfillment_kind ? PARTY_LABELS[observation.fulfillment_kind] : null)
  if (delivery) facts.push(['Entrega', delivery])
  if (observation) facts.push(['Condição', <ConditionBadge key="condition" condition={observation.condition} />])
  if (observation) facts.push(['Disponibilidade', AVAILABILITY_LABELS[observation.availability]])
  return (
    <dl>
      {facts.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  )
}

function ComparisonSection({ comparison, currentOfferId }: { comparison?: OfferComparison | null; currentOfferId: string }) {
  if (comparison === undefined) return null
  if (comparison === null) return <div className="mission-section"><h2>Comparar entre lojas</h2><p className="field-hint">Comparação indisponível no momento.</p></div>
  if (!comparison.comparable) return <div className="mission-section"><h2>Comparar entre lojas</h2><p className="field-hint">Esta oferta ainda não possui identidade específica de produto/variante resolvida.</p></div>
  const stores = Array.from(new Set(comparison.offers.map((item) => item.store.code)))
  return <div className="mission-section">
    <h2>Comparar entre lojas</h2>
    <p className="field-hint">Somente {comparison.title}{comparison.variant ? ` — ${comparison.variant}` : ''}. Variantes diferentes nunca entram nesta comparação.</p>
    {comparison.offers.length === 0 ? <p className="field-hint">Nenhuma outra oferta autorizada encontrada.</p> : (
      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {stores.map((storeCode) => {
          const items = comparison.offers.filter((item) => item.store.code === storeCode)
          return <div key={storeCode} className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <h3 className="font-semibold">{items[0].store.name}</h3>
            <div className="mt-3 space-y-3">{items.map((item) => {
              const observation = item.latest_observation
              const installment = observation?.installments[0]
              return <div key={item.id} className={`rounded-lg border p-3 ${item.id === currentOfferId ? 'border-primary bg-primary/5' : 'border-border'}`}>
                <div className="flex items-start justify-between gap-3"><div><p className="text-sm font-medium">{item.seller?.name ?? item.store.name}</p><p className="flex items-center gap-1.5 text-xs text-muted-foreground">{observation ? <><ConditionBadge condition={observation.condition} /> · {AVAILABILITY_LABELS[observation.availability]}</> : 'Sem preço atual'}</p></div>{item.id === currentOfferId ? <span className="rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground">Atual</span> : null}</div>
                {observation ? <><p className="mt-2 text-lg font-semibold">{money(observation.total_amount, observation.currency)}</p><p className="text-xs text-muted-foreground">À vista {money(observation.amount, observation.currency)}{observation.shipping_amount === null ? ' · frete não informado' : ` · frete ${money(observation.shipping_amount, observation.currency)}`}</p></> : null}
                {installment ? <p className="mt-1 text-xs">{installment.installment_count}x de {money(installment.installment_amount, observation!.currency)}{installment.interest_kind === 'interest_free' ? ' sem juros' : ''}</p> : null}
                {item.rating ? <p className="mt-1 text-xs">⭐ {ratingAverage(item.rating.average)} · {item.rating.review_count.toLocaleString('pt-BR')}</p> : null}
                <div className="mt-3 flex gap-2"><Link className="button button-secondary" to={`/app/offers/${item.id}`}>Detalhes</Link><a className="button button-secondary" href={item.original_url} target="_blank" rel="noreferrer">Loja</a></div>
              </div>
            })}</div>
          </div>
        })}
      </div>
    )}
  </div>
}

export function OfferDetailPage() {
  const { offerId } = useParams<{ offerId: string }>()
  const [offer, setOffer] = useState<OfferDetail | null>(null)
  const [comparison, setComparison] = useState<OfferComparison | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!offerId) {
      setError('Oferta não encontrada.')
      return
    }
    setError(null)
    setOffer(null)
    setComparison(undefined)
    try {
      setOffer(await offersApi.get(offerId))
      try { setComparison(await offersApi.compare(offerId)) } catch { setComparison(null) }
    } catch (loadError) {
      if (loadError instanceof ApiError && loadError.status === 403) {
        setError('Você não tem acesso a esta oferta ou ela não foi encontrada.')
      } else if (loadError instanceof ApiError && loadError.status === 404) {
        setError('Oferta não encontrada.')
      } else {
        setError(
          loadError instanceof ApiError
            ? loadError.message
            : 'Não foi possível carregar a oferta.',
        )
      }
    }
  }, [offerId])

  useEffect(() => {
    load()
  }, [load])

  if (error) {
    return (
      <section>
        <p className="form-error">{error}</p>
        <Link className="button button-secondary" to="/app/missions">
          Voltar para missões
        </Link>
      </section>
    )
  }
  if (!offer) return <p className="loading">Carregando…</p>
  return <OfferDetailView offer={offer} comparison={comparison} />
}
