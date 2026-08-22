/**
 * Guardas de rota (TASK-091). São conveniência de UX -- a proteção real
 * fica no backend (`app.webapp.dependency`), que sempre valida de novo;
 * estas guardas só evitam mostrar uma tela que o backend recusaria.
 */
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return <p className="loading">Carregando…</p>
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return <Outlet />
}

export function RequireAdmin() {
  const { user, loading, isAdmin } = useAuth()

  if (loading) {
    return <p className="loading">Carregando…</p>
  }
  if (!user) {
    return <Navigate to="/login" replace />
  }
  if (!isAdmin) {
    return <Navigate to="/app" replace />
  }
  return <Outlet />
}
