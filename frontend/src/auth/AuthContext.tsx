/**
 * Sessão atual da aplicação web (TASK-091). Carrega `GET
 * /api/v1/web-sessions/current` uma vez ao montar; 401 significa apenas
 * "sem sessão", não é tratado como erro de aplicação.
 */
import {
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, api } from '../api/client'
import type { WebSessionUser } from '../api/types'
import { AuthContext } from './authContextValue'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<WebSessionUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const current = await api.get<WebSessionUser>('/web-sessions/current')
      setUser(current)
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) {
        throw error
      }
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  // Mesma consulta de `refresh` (duplicada de propósito, não chamada por
  // referência): o efeito só roda uma vez ao montar, então o corpo fica
  // inline aqui -- chamar `refresh()" de dentro de um `useEffect` dispara
  // o lint `set-state-in-effect` (React Compiler rastreia que a função
  // referenciada muda estado). `refresh` continua exportada intacta para
  // quem precisa re-consultar a sessão fora de um efeito (`AccountPage`,
  // `RegisterPage`).
  useEffect(() => {
    let cancelled = false
    api.get<WebSessionUser>('/web-sessions/current').then(
      (current) => {
        if (cancelled) return
        setUser(current)
        setLoading(false)
      },
      (error) => {
        if (cancelled) return
        if (!(error instanceof ApiError) || error.status !== 401) throw error
        setUser(null)
        setLoading(false)
      },
    )
    return () => { cancelled = true }
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const current = await api.post<WebSessionUser>('/web-sessions', { username, password })
    setUser(current)
    return current
  }, [])

  const logout = useCallback(async () => {
    await api.del('/web-sessions/current')
    setUser(null)
  }, [])

  const isAdmin = user ? user.role === 'ADMIN' || user.role === 'DEV' : false

  return (
    <AuthContext.Provider value={{ user, loading, isAdmin, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  )
}
