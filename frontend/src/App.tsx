import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { AdminLayout, AppLayout } from './components/Layout'
import { RequireAdmin, RequireAuth } from './components/ProtectedRoute'
import { AdminHome } from './pages/AdminHome'
import { AppHome } from './pages/AppHome'
import { LoginPage } from './pages/LoginPage'
import { MissionCreatePage } from './pages/missions/MissionCreatePage'
import { MissionDetailPage } from './pages/missions/MissionDetailPage'
import { OfferDetailPage } from './pages/offers/OfferDetailPage'
import { MissionsListPage } from './pages/missions/MissionsListPage'
import { NotFound } from './pages/NotFound'
import { ProductSearchPage } from './pages/search/ProductSearchPage'

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<Navigate to="/app" replace />} />
        <Route path="/login" element={<LoginPage />} />

        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route path="/app" element={<AppHome />} />
            <Route path="/app/search" element={<ProductSearchPage />} />
            <Route path="/app/missions" element={<MissionsListPage />} />
            <Route path="/app/missions/new" element={<MissionCreatePage />} />
            <Route path="/app/missions/:missionId" element={<MissionDetailPage />} />
            <Route path="/app/offers/:offerId" element={<OfferDetailPage />} />
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
