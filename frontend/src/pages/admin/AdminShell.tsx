import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '@/lib/utils'

const ADMIN_TABS = [
  { to: '/admin', label: 'Dashboard', end: true },
  { to: '/admin/users', label: 'Usuários', end: false },
  { to: '/admin/feedback', label: 'Feedback', end: false },
]

/** Sub-navegação da área Admin (Subtask 16) -- não é um segundo shell:
 * fica dentro do mesmo `AppLayout`/sidebar já existente ("Administração"),
 * só organiza as 3 sub-rotas (`/admin`, `/admin/users`,
 * `/admin/feedback`) em abas simples, mesmo padrão visual do resto do
 * produto. */
export function AdminShell() {
  return (
    <div>
      <nav aria-label="Áreas administrativas" className="mb-6 flex gap-1 border-b border-border/70">
        {ADMIN_TABS.map(({ to, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                '-mb-px border-b-2 border-transparent px-3 py-2.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground',
                isActive && 'border-primary text-foreground',
              )
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
