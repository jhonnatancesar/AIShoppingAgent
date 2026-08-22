/**
 * Sessão atual da aplicação web (TASK-091). Carrega `GET
 * /api/v1/web-sessions/current` uma vez ao montar; 401 significa apenas
 * "sem sessão", não é tratado como erro de aplicação.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, api } from '../api/client'
import type { WebSessionUser } from '../api/types'

interface AuthContextValue {
  user: WebSessionUser | null
  loading: boolean
  isAdmin: boolean
  login: (username: string, password: string) => Promise<WebSessionUser | null>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

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

  useEffect(() => {
    refresh()
  }, [refresh])

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

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth precisa estar dentro de <AuthProvider>')
  }
  return context
}
