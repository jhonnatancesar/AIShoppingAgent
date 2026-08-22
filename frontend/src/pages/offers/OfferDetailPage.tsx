import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { offersApi } from '../../api/offers'
import type {
  MarketplacePartyKind,
  OfferAvailability,
  OfferCondition,
  OfferDetail,
} from '../../api/types'

const CONDITION_LABELS: Record<OfferCondition, string> = {
  new: 'Novo',
  refurbished: 'Recondicionado',
  used: 'Usado',
  unknown: 'Condição não identificada',
}

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

export function OfferDetailView({ offer }: { offer: OfferDetail }) {
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
        <div className="offer-image-panel">
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
          <dl>
            <div><dt>Loja</dt><dd>{offer.store.name}</dd></div>
            <div><dt>Vendedor</dt><dd>{offer.seller?.name ?? 'Não informado'}</dd></div>
            <div>
              <dt>Tipo de vendedor</dt>
              <dd>{observation?.seller_kind ? PARTY_LABELS[observation.seller_kind] : 'Não informado'}</dd>
            </div>
            <div>
              <dt>Entrega</dt>
              <dd>
                {observation?.fulfillment ??
                  (observation?.fulfillment_kind
                    ? PARTY_LABELS[observation.fulfillment_kind]
                    : 'Não informada')}
              </dd>
            </div>
            <div><dt>Condição</dt><dd>{observation ? CONDITION_LABELS[observation.condition] : 'Não informada'}</dd></div>
            <div><dt>Disponibilidade</dt><dd>{observation ? AVAILABILITY_LABELS[observation.availability] : 'Não informada'}</dd></div>
          </dl>

          {observation ? (
            <div className="offer-price-panel">
              <p className="offer-price-label">Preço à vista</p>
              <p className="offer-price">{money(observation.amount, observation.currency)}</p>
              <p>
                Frete: {observation.shipping_amount === null
                  ? 'Não informado'
                  : money(observation.shipping_amount, observation.currency)}
              </p>
              <p>Total: {money(observation.total_amount, observation.currency)}</p>
              <p className="field-hint">Atualizado em {dateTime(observation.observed_at)}</p>
            </div>
          ) : (
            <p className="field-hint">Ainda não há observação comercial disponível.</p>
          )}
        </div>
      </div>

      <div className="mission-section">
        <h2>Parcelamento</h2>
        {!observation || observation.installments.length === 0 ? (
          <p className="field-hint">Parcelamento não informado.</p>
        ) : (
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
        )}
      </div>

      <p className="field-hint">Oferta vista pela última vez em {dateTime(offer.last_seen_at)}.</p>
      <div className="mission-actions">
        <a className="button" href={offer.original_url} target="_blank" rel="noreferrer">
          Abrir oferta na loja
        </a>
        <Link className="button button-secondary" to="/app/missions">
          Voltar para missões
        </Link>
      </div>
    </section>
  )
}

export function OfferDetailPage() {
  const { offerId } = useParams<{ offerId: string }>()
  const [offer, setOffer] = useState<OfferDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!offerId) {
      setError('Oferta não encontrada.')
      return
    }
    setError(null)
    setOffer(null)
    try {
      setOffer(await offersApi.get(offerId))
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
  return <OfferDetailView offer={offer} />
}
