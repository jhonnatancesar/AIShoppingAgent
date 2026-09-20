import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'

const DEV_SEARCHES_TABS = [
  { to: '/app/dev/pesquisas', label: 'Minhas pesquisas', end: true },
  { to: '/app/dev/pesquisas/todas', label: 'Todas as pesquisas', end: false },
]

/** Sub-navegação da view DEV "Pesquisas" (Frente 5) -- mesmo padrão
 * visual de `AdminShell`, mas fora da árvore `/admin`: `RequireDev` é
 * mais estrito que `RequireAdmin` (ADMIN puro não entra aqui). Fonte
 * real é `SearchReceipt` (`GET /product-search/history/*`) -- pesquisas
 * de fato realizadas em `/app/search`, nunca missões. */
export function DevSearchesShell() {
  return (
    <section>
      <PageHeader eyebrow="Ferramenta interna" title="Pesquisas" description="Histórico das pesquisas realizadas, mesmo sem criar uma missão: as suas e, na segunda aba, as de todos os usuários." />
      <nav aria-label="Abas de pesquisas" className="mb-6 flex gap-1 border-b border-border/70">
        {DEV_SEARCHES_TABS.map(({ to, label, end }) => (
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
    </section>
  )
}
