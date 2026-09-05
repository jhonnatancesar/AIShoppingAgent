import { STORE_LABELS } from './statusLabels'
import { formatPriceDisplay } from './priceFormat'
import { Input } from '@/components/ui/input'
import { ToggleGroup } from '@/components/ui/toggle-group'
import { StoreName } from '@/components/StoreMark'

const STORE_OPTIONS = Object.entries(STORE_LABELS)
  .sort(([, first], [, second]) => first.localeCompare(second, 'pt-BR'))
  .map(([code, label]) => ({ value: code, label: <StoreName store={code}>{label}</StoreName> }))

/** Campos de preço-alvo compartilhados entre Criar e Editar Missão
 * (Subtask 14) -- mesmo componente/visual nos dois fluxos, só Editar
 * passa `clearTarget`/`onClearTargetChange` (Criar não tem alvo prévio
 * para remover). */
export function TargetPriceFields({
  targetAmount,
  onTargetAmountChange,
  targetCurrency,
  onTargetCurrencyChange,
  clearTarget,
  onClearTargetChange,
}: {
  targetAmount: string
  onTargetAmountChange: (value: string) => void
  targetCurrency: string
  onTargetCurrencyChange: (value: string) => void
  clearTarget?: boolean
  onClearTargetChange?: (value: boolean) => void
}) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="target_amount">Preço-alvo (opcional)</label>
          <Input
            id="target_amount"
            inputMode="decimal"
            placeholder="ex.: 4.500,00"
            value={targetAmount}
            onChange={(event) => onTargetAmountChange(event.target.value)}
            onBlur={(event) => onTargetAmountChange(formatPriceDisplay(event.target.value))}
            disabled={clearTarget}
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="target_currency">Moeda</label>
          <Input
            id="target_currency"
            maxLength={3}
            value={targetCurrency}
            onChange={(event) => onTargetCurrencyChange(event.target.value.toUpperCase())}
            disabled={clearTarget || !targetAmount.trim()}
          />
        </div>
      </div>
      {onClearTargetChange ? (
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            className="size-4 rounded border-input"
            checked={clearTarget ?? false}
            onChange={(event) => onClearTargetChange(event.target.checked)}
          />
          Remover preço-alvo
        </label>
      ) : null}
    </div>
  )
}

/** Seleção de lojas compartilhada entre Criar e Editar Missão (Subtask
 * 14) -- mesmo `ToggleGroup` da Pesquisa (Subtask 13). `hint` muda por
 * tela porque o significado de "vazio" é diferente: em Criar, vazio =
 * todas as lojas da V1; em Editar, vazio = mantém as lojas atuais
 * (regra pré-existente do backend, não alterada aqui). */
export function StoreSelectionField({
  selected,
  onChange,
  hint,
}: {
  selected: string[]
  onChange: (value: string[]) => void
  hint: string
}) {
  return (
    <div className="space-y-1.5">
      <span className="text-sm font-medium">Lojas</span>
      <p className="text-xs text-muted-foreground">{hint}</p>
      <ToggleGroup type="multiple" aria-label="Lojas" value={selected} onChange={onChange} options={STORE_OPTIONS} />
    </div>
  )
}
