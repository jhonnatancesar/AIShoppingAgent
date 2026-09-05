import { createContext, useContext } from 'react'
import type { ToastItem } from '@/components/ui/toast'

export type ToastInput = Omit<ToastItem, 'id'>

export interface ToastContextValue {
  toast: (input: ToastInput) => void
}

export const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast precisa estar dentro de <ToastProvider>.')
  return context
}
