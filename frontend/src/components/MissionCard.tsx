import { ArrowRight, ShoppingBag } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { MissionListItem } from '@/api/types'
import { STATUS_BADGE_VARIANT, STATUS_LABELS, STORE_LABELS } from '@/pages/missions/statusLabels'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'

function money(value: string, currency: string) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(Number(value))
}

/** Card de missão da Lista (Subtask 14) -- puramente apresentacional,
 * mesmo padrão do `OfferCard` (Subtask 13). Nunca inventa preço-alvo,
 * lojas ou contagem de ofertas: os três vêm prontos de
 * `MissionListItem` (`load_mission_list_extras`, backend). */
export function MissionCard({ mission }: { mission: MissionListItem }) {
  return (
    <Card className="flex h-full flex-col transition-transform hover:-translate-y-0.5">
      <CardHeader className="flex-1 gap-2">
        <Badge variant={STATUS_BADGE_VARIANT[mission.status]} className="w-fit">
          {STATUS_LABELS[mission.status]}
        </Badge>
        <CardTitle className="line-clamp-2 text-base leading-snug">{mission.title}</CardTitle>
        <CardDescription>
          {mission.target_amount
            ? `Alvo: ${money(mission.target_amount, mission.target_currency ?? 'BRL')}`
            : 'Sem preço-alvo definido'}
        </CardDescription>
        <p className="text-xs text-muted-foreground">
          {mission.sources.length === 0
            ? 'Nenhuma loja selecionada'
            : mission.sources.map((source) => STORE_LABELS[source.store_code] ?? source.store_name).join(', ')}
        </p>
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <ShoppingBag className="size-3.5" />
          {mission.relevant_offer_count === 0
            ? 'Nenhuma oferta relevante ainda'
            : `${mission.relevant_offer_count} oferta(s) relevante(s)`}
        </p>
      </CardHeader>
      <CardFooter>
        <Button variant="outline" className="w-full" asChild>
          <Link to={`/app/missions/${mission.id}`}>Ver detalhes <ArrowRight /></Link>
        </Button>
      </CardFooter>
    </Card>
  )
}
