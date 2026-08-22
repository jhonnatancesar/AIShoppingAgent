import { Link, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

interface NavItem {
  to: string
  label: string
}

const USER_NAV: NavItem[] = [{ to: '/app', label: 'Início' }]

const ADMIN_NAV: NavItem[] = [
  { to: '/admin', label: 'Dashboard' },
  { to: '/app', label: '← Voltar para /app' },
]

export function AppLayout() {
  return <Shell nav={USER_NAV} title="AIShoppingAgent" />
}

export function AdminLayout() {
  return <Shell nav={ADMIN_NAV} title="AIShoppingAgent — admin" adminBadge />
}

function Shell({
  nav,
  title,
  adminBadge,
}: {
  nav: NavItem[]
  title: string
  adminBadge?: boolean
}) {
  const { user, logout } = useAuth()

  return (
    <div className="shell">
      <header className="shell-header">
        <span className="shell-title">
          {title}
          {adminBadge ? <span className="admin-badge">DEV/ADMIN</span> : null}
        </span>
        <nav className="shell-nav">
          {nav.map((item) => (
            <Link key={item.to} to={item.to}>
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="shell-user">
          {user ? <span>{user.display_name}</span> : null}
          <button type="button" onClick={logout}>
            Sair
          </button>
        </div>
      </header>
      <main className="shell-main">
        <Outlet />
      </main>
    </div>
  )
}
