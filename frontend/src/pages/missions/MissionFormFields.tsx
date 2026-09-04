import { STORE_LABELS } from './statusLabels'
import { Input } from '@/components/ui/input'
import { ToggleGroup } from '@/components/ui/toggle-group'

const STORE_OPTIONS = Object.entries(STORE_LABELS)
  .map(([code, label]) => ({ value: code, label }))
  .sort((a, b) => a.label.localeCompare(b.label, 'pt-BR'))

/** Padrão oficial da interface para preço-alvo: pt-BR (milhar `.`,
 * decimal `,`, sempre 2 casas quando o valor vem carregado/salvo) --
 * nunca o decimal técnico que a API usa/devolve. Aceita como entrada
 * qualquer um dos formatos abaixo (`null` se não for numérico):
 * "1999.00000" (bruto da API), "1999", "1999,5", "1999,99", "1.999,99".
 * Com vírgula presente, qualquer ponto é separador de milhar (removido);
 * sem vírgula, tolera o decimal técnico com ponto sem complicar. */
function parsePriceInput(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const normalized = trimmed.includes(',') ? trimmed.replaceAll('.', '').replace(',', '.') : trimmed
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

/** Formata para o padrão visual oficial pt-BR (ex.: `1999.00000`/`1999`/
 * `1999,5` → `1.999,00`/`1.999,00`/`1.999,50`) -- usado no prefill do
 * formulário de Editar e ao sair do campo (`onBlur`). Valor não numérico
 * (campo vazio, ou ainda incompleto enquanto o usuário digita) é
 * devolvido sem alteração, nunca força um formato no meio da digitação. */
export function formatPriceDisplay(value: string): string {
  const parsed = parsePriceInput(value)
  return parsed === null
    ? value
    : new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(parsed)
}

/** Converte o valor exibido (qualquer formato aceito por
 * `parsePriceInput`) para o decimal técnico que a API espera (ponto,
 * sem separador de milhar) -- só na hora de montar o payload, nunca
 * durante a digitação. */
export function toApiDecimal(value: string): string {
  const parsed = parsePriceInput(value)
  return parsed === null ? value.trim() : parsed.toFixed(2)
}

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
