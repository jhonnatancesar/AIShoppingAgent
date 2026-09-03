import { Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { AppLayout } from './components/Layout'
import { RequireAdmin, RequireAuth } from './components/ProtectedRoute'
import { TooltipProvider } from './components/ui/tooltip'
import { ToastProvider } from './hooks/useToast'
import { AdminHome } from './pages/AdminHome'
import { AppHome } from './pages/AppHome'
import { LandingPage } from './pages/LandingPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { RecoverPasswordPage } from './pages/RecoverPasswordPage'
import { MissionCreatePage } from './pages/missions/MissionCreatePage'
import { MissionDetailPage } from './pages/missions/MissionDetailPage'
import { OfferDetailPage } from './pages/offers/OfferDetailPage'
import { OffersListPage } from './pages/offers/OffersListPage'
import { MissionsListPage } from './pages/missions/MissionsListPage'
import { NotFound } from './pages/NotFound'
import { ProductSearchPage } from './pages/search/ProductSearchPage'
import { AccountPage } from './pages/account/AccountPage'
import { FeedbackPage } from './pages/FeedbackPage'
import { CouponsPage } from './pages/CouponsPage'

export default function App() {
  return (
    <TooltipProvider delayDuration={200}>
      <ToastProvider>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/cadastro" element={<RegisterPage />} />
            <Route path="/recuperar" element={<RecoverPasswordPage />} />

            <Route element={<RequireAuth />}>
              <Route element={<AppLayout />}>
                <Route path="/app" element={<AppHome />} />
                <Route path="/app/search" element={<ProductSearchPage />} />
                <Route path="/app/missions" element={<MissionsListPage />} />
                <Route path="/app/missions/new" element={<MissionCreatePage />} />
                <Route path="/app/missions/:missionId" element={<MissionDetailPage />} />
                <Route path="/app/offers" element={<OffersListPage />} />
                <Route path="/app/offers/:offerId" element={<OfferDetailPage />} />
                <Route path="/app/coupons" element={<CouponsPage />} />
                <Route path="/app/account" element={<AccountPage />} />
                <Route path="/app/suporte" element={<FeedbackPage />} />

                <Route element={<RequireAdmin />}>
                  <Route path="/admin" element={<AdminHome />} />
                </Route>
              </Route>
            </Route>

            <Route path="*" element={<NotFound />} />
          </Routes>
        </AuthProvider>
      </ToastProvider>
    </TooltipProvider>
  )
}
