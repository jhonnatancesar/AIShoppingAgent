import { motion, useReducedMotion } from 'motion/react'
import { ArrowRight, ExternalLink, MousePointerClick, ShoppingBag } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { OfferCondition } from '@/api/types'
import { ConditionBadge } from '@/components/ConditionBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useImageFallbackChain } from '@/hooks/useImageFallbackChain'

function money(value: string, currency: string) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(Number(value))
}

export interface OfferCardData {
  id: string
  title: string
  imageUrl: string | null
  imageFallbackUrl?: string | null
  store: { name: string }
  /** `null` quando a oferta ainda não tem observação comercial -- nunca um valor inventado. */
  price: { amount: string; totalAmount: string; currency: string } | null
  condition: OfferCondition | null
  seller?: { name: string } | null
  rating?: { average: string; reviewCount: number } | null
}

export type OfferCardAction = { label: string } & ({ href: string } | { to: string })

/** Card de oferta compartilhado entre Pesquisa e Ofertas (Subtask 13).
 * Puramente apresentacional -- cada tela normaliza seu próprio DTO
 * (`OfferSummary`/`ProductSearchOffer`) para `OfferCardData` antes de
 * passar para cá; o componente nunca sabe de qual tela veio o dado, só
 * decide como desenhar o que recebeu. A ação (interna/externa) é resolvida
 * por `href` (link externo, abre em nova aba) ou `to` (rota interna via
 * `Link`), nunca por um "modo" da tela. `selected`/`onSelect` são
 * genéricos (destaque + clique na área da imagem) -- quem decide quando
 * usar isso é quem chama o componente (ex.: Pesquisa, ao escolher uma
 * oferta de categoria genérica), não o card. */
export function OfferCard({
  offer,
  action,
  index = 0,
  selected = false,
  onSelect,
}: {
  offer: OfferCardData
  action: OfferCardAction
  index?: number
  selected?: boolean
  onSelect?: () => void
}) {
  const prefersReducedMotion = useReducedMotion()
  const { src: imageSrc, onError: onImageError } = useImageFallbackChain([offer.imageUrl, offer.imageFallbackUrl])

  return (
    <motion.div
      initial={prefersReducedMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.025, 0.2) }}
    >
      <Card className={`flex h-full flex-col overflow-hidden transition-transform hover:-translate-y-0.5 ${selected ? 'ring-2 ring-primary' : ''}`}>
        {onSelect ? (
          <button type="button" onClick={onSelect} className="grid h-44 w-full place-items-center bg-muted/40 p-4 text-left">
            {imageSrc ? <img className="h-full w-full min-h-0 object-contain" src={imageSrc} alt="" onError={onImageError} /> : <ShoppingBag className="size-10 text-muted-foreground/35" />}
          </button>
        ) : (
          <div className="grid h-44 place-items-center bg-muted/40 p-4">
            {imageSrc ? (
              <img className="h-full w-full min-h-0 object-contain" src={imageSrc} alt="" onError={onImageError} />
            ) : (
              <ShoppingBag className="size-10 text-muted-foreground/35" />
            )}
          </div>
        )}
        <CardHeader className="flex-1">
          <div className="flex items-center justify-between gap-2">
            <Badge variant="secondary">{offer.store.name}</Badge>
            {offer.rating ? <span className="text-xs text-muted-foreground">★ {Number(offer.rating.average).toLocaleString('pt-BR')}</span> : null}
          </div>
          <CardTitle className="line-clamp-2 text-base leading-snug">{offer.title}</CardTitle>
          {offer.price ? (
            <>
              <p className="text-xl font-semibold tracking-tight">{money(offer.price.amount, offer.price.currency)}</p>
              <CardDescription className="flex items-center gap-1.5">
                Total {money(offer.price.totalAmount, offer.price.currency)}
                {offer.condition ? <>{' · '}<ConditionBadge condition={offer.condition} /></> : null}
              </CardDescription>
            </>
          ) : (
            <CardDescription>Preço ainda não coletado.</CardDescription>
          )}
          {offer.seller ? <p className="text-xs text-muted-foreground">Vendido por {offer.seller.name}</p> : null}
          {onSelect ? <p className="flex items-center gap-1 text-xs text-primary"><MousePointerClick className="size-3" />Clique para selecionar esta oferta</p> : null}
        </CardHeader>
        <CardFooter>
          <Button variant="outline" className="w-full" asChild>
            {'to' in action ? (
              <Link to={action.to}>{action.label} <ArrowRight /></Link>
            ) : (
              <a href={action.href} target="_blank" rel="noreferrer">{action.label} <ExternalLink /></a>
            )}
          </Button>
        </CardFooter>
      </Card>
    </motion.div>
  )
}
