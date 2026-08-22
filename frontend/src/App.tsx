import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { AdminLayout, AppLayout } from './components/Layout'
import { RequireAdmin, RequireAuth } from './components/ProtectedRoute'
import { AdminHome } from './pages/AdminHome'
import { AppHome } from './pages/AppHome'
import { LoginPage } from './pages/LoginPage'
import { NotFound } from './pages/NotFound'

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<Navigate to="/app" replace />} />
        <Route path="/login" element={<LoginPage />} />

        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route path="/app" element={<AppHome />} />
          </Route>
        </Route>

        <Route element={<RequireAdmin />}>
          <Route element={<AdminLayout />}>
            <Route path="/admin" element={<AdminHome />} />
          </Route>
        </Route>

        <Route path="*" element={<NotFound />} />
      </Routes>
    </AuthProvider>
  )
}
