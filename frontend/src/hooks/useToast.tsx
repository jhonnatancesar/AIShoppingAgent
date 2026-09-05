import { useCallback, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Toast, type ToastItem } from '@/components/ui/toast'
import { ToastContext, type ToastInput } from '@/hooks/toastContext'

const AUTO_DISMISS_MS = 5000

/** Substituto de `window.alert`/mensagens inline de sucesso-erro (Subtask
 * 12) -- fica montado uma vez na raiz do app; qualquer página chama
 * `useToast().toast(...)` em vez de reimplementar seu próprio bloco de
 * feedback. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const prefersReducedMotion = useReducedMotion()

  const dismiss = useCallback((id: string) => {
    setItems((current) => current.filter((item) => item.id !== id))
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
  }, [])

  const toast = useCallback(
    (input: ToastInput) => {
      const id = crypto.randomUUID()
      setItems((current) => [...current, { id, ...input }])
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), AUTO_DISMISS_MS),
      )
    },
    [dismiss],
  )

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:items-end">
        <AnimatePresence mode="popLayout">
          {items.map((item) => (
            <motion.div
              key={item.id}
              initial={prefersReducedMotion ? false : { opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.98 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              className="w-full max-w-sm"
            >
              <Toast toast={item} onDismiss={dismiss} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}
