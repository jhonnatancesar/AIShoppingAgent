import { createContext, useContext } from 'react'
import type { WebSessionUser } from '../api/types'

export interface AuthContextValue {
  user: WebSessionUser | null
  loading: boolean
  isAdmin: boolean
  login: (username: string, password: string) => Promise<WebSessionUser | null>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth precisa estar dentro de <AuthProvider>')
  }
  return context
}
