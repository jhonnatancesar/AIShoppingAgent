/**
 * Guardas de rota (TASK-091). São conveniência de UX -- a proteção real
 * fica no backend (`app.webapp.dependency`), que sempre valida de novo;
 * estas guardas só evitam mostrar uma tela que o backend recusaria.
 */
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { LoadingState } from '@/components/StatePanel'

export function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return <div className="mx-auto mt-20 max-w-xl px-4"><LoadingState label="Validando sua sessão…" /></div>
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return <Outlet />
}

export function RequireAdmin() {
  const { user, loading, isAdmin } = useAuth()

  if (loading) {
    return <div className="mx-auto mt-20 max-w-xl px-4"><LoadingState label="Validando sua sessão…" /></div>
  }
  if (!user) {
    return <Navigate to="/login" replace />
  }
  if (!isAdmin) {
    return <Navigate to="/app" replace />
  }
  return <Outlet />
}
