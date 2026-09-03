import { cn } from '@/lib/utils'

export interface ToggleGroupOption<T extends string> {
  value: T
  label: string
}

interface ToggleGroupBaseProps<T extends string> {
  options: ToggleGroupOption<T>[]
  className?: string
  'aria-label'?: string
}

type ToggleGroupProps<T extends string> =
  | (ToggleGroupBaseProps<T> & { type: 'single'; value: T; onChange: (value: T) => void })
  | (ToggleGroupBaseProps<T> & { type: 'multiple'; value: T[]; onChange: (value: T[]) => void })

/** Grupo de opções em formato de pílula (Subtask 12) -- substitui as
 * reimplementações independentes que existiam em várias telas (seleção
 * única de categoria, seleção múltipla de lojas) por um único componente
 * com dois modos, mesmo visual em ambos. */
export function ToggleGroup<T extends string>(props: ToggleGroupProps<T>) {
  const { options, className, type, value, onChange } = props
  const isActive = (option: T) => (type === 'single' ? value === option : value.includes(option))
  const toggle = (option: T) => {
    if (type === 'single') {
      onChange(option)
      return
    }
    onChange(value.includes(option) ? value.filter((item) => item !== option) : [...value, option])
  }

  return (
    <div role="group" aria-label={props['aria-label']} className={cn('flex flex-wrap gap-2', className)}>
      {options.map((option) => {
        const active = isActive(option.value)
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => toggle(option.value)}
            className={cn(
              'rounded-full border border-border px-3 py-1.5 text-sm font-medium text-foreground/80 transition-colors hover:border-primary/50',
              active && 'border-primary bg-primary/10 text-primary hover:border-primary',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
