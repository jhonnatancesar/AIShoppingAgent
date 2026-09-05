import type { ReactNode } from 'react'
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
import { Button, type ButtonProps } from '@/components/ui/button'
import { adminStatusLabel } from './adminLabels'

/** Badge de status genérico do Admin (Subtask 16) -- mesma heurística já
 * usada informalmente antes (saudável/ativo = success, falha/bloqueado =
 * destructive, resto = secondary), agora com os tokens do design system. */
export function AdminStatusBadge({ value }: { value: string }) {
  const variant = ['healthy', 'running', 'active'].includes(value)
    ? 'success'
    : ['failed', 'blocked'].includes(value)
      ? 'destructive'
      : 'secondary'
  return <Badge variant={variant}>{adminStatusLabel(value)}</Badge>
}

/** Substitui `window.confirm` em toda a área Admin (Subtask 16): botão
 * que abre um `AlertDialog` de confirmação antes de disparar `onConfirm`
 * -- mesmo padrão já usado em Missões/Minha Conta (Subtasks 14/15), o
 * diálogo fecha sozinho ao confirmar (Radix), `onConfirm` só executa a
 * ação em si. */
export function ConfirmActionButton({
  triggerLabel,
  triggerIcon: Icon,
  variant = 'outline',
  size = 'sm',
  title,
  description,
  confirmLabel = 'Confirmar',
  destructive = false,
  onConfirm,
  disabled,
}: {
  triggerLabel: ReactNode
  triggerIcon?: React.ComponentType<{ className?: string }>
  variant?: ButtonProps['variant']
  size?: ButtonProps['size']
  title: string
  description: string
  confirmLabel?: string
  destructive?: boolean
  onConfirm: () => void | Promise<void>
  disabled?: boolean
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button type="button" variant={variant} size={size} disabled={disabled}>
          {Icon ? <Icon className="size-4" /> : null}
          {triggerLabel}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Voltar</AlertDialogCancel>
          <AlertDialogAction variant={destructive ? 'destructive' : 'default'} onClick={onConfirm}>
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
