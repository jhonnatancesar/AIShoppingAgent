import type { OfferCondition } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export const CONDITION_LABELS: Record<OfferCondition, string> = {
  new: 'Novo',
  refurbished: 'Recondicionado',
  used: 'Usado',
  unknown: 'Condição não identificada',
}

// Subtask 3 (auditoria GG Oferta): USED/REFURBISHED precisam ficar
// visualmente evidentes; NEW/unknown continuam discretos (texto simples).
const NOTABLE_CONDITIONS: ReadonlySet<OfferCondition> = new Set(['used', 'refurbished'])

export function conditionLabel(condition: OfferCondition) {
  return CONDITION_LABELS[condition]
}

export function ConditionBadge({ condition, className = '' }: { condition: OfferCondition; className?: string }) {
  const label = CONDITION_LABELS[condition]
  if (!NOTABLE_CONDITIONS.has(condition)) {
    return <span className={className}>{label}</span>
  }
  return <Badge variant="warning" className={cn(className)}>{label}</Badge>
}
