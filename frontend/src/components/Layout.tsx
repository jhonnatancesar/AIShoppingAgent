import { useState, type ComponentType } from 'react'
import { motion } from 'motion/react'
import { Home, LifeBuoy, LogOut, Menu, Search, ShieldCheck, ShoppingBag, Target, Ticket, UserRound, X } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/auth/AuthContext'
import { BrandLogo } from '@/components/BrandLogo'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/ThemeToggle'
import { cn } from '@/lib/utils'

interface NavItem { to: string; label: string; icon: ComponentType<{ className?: string }>; end?: boolean }

const USER_NAV: NavItem[] = [
  { to: '/app', label: 'Início', icon: Home, end: true },
  { to: '/app/search', label: 'Pesquisar', icon: Search },
  { to: '/app/offers', label: 'Ofertas', icon: ShoppingBag },
  { to: '/app/missions', label: 'Missões', icon: Target },
  { to: '/app/coupons', label: 'Cupons', icon: Ticket },
  { to: '/app/suporte', label: 'Suporte', icon: LifeBuoy },
  { to: '/app/account', label: 'Minha conta', icon: UserRound },
]

const ADMIN_NAV: NavItem[] = [
  { to: '/admin', label: 'Administração', icon: ShieldCheck, end: true },
]

interface NavGroupSpec { label?: string; items: NavItem[] }

/** Única fonte da navegação exibida (Subtask 8) -- pura e testável sem
 * Router/AuthContext: USER só enxerga `USER_NAV`; ADMIN (permissão real
 * da sessão, nunca escolha do login) enxerga USER_NAV + a seção
 * "Administração", nunca um layout/nav totalmente separado. */
export function navGroupsFor(isAdmin: boolean): NavGroupSpec[] {
  if (!isAdmin) return [{ items: USER_NAV }]
  return [
    { label: 'Principal', items: USER_NAV },
    { label: 'Administração', items: ADMIN_NAV },
  ]
}

/** Shell único para USER e ADMIN (Subtask 8) -- a diferença entre os dois
 * nunca é um layout separado, só a presença (ou não) da seção
 * "Administração" na mesma navegação, decidida por `isAdmin` (permissão
 * real vinda da sessão, nunca uma escolha feita no login). */
export function AppLayout() {
  const { user, isAdmin, logout } = useAuth()
  const location = useLocation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const onAdminArea = location.pathname.startsWith('/admin')
  const navGroups = navGroupsFor(isAdmin)

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-16 flex-col justify-center gap-0.5 border-b border-sidebar-border px-5">
        <BrandLogo className="h-7 w-auto" />
        <p className="truncate text-xs text-muted-foreground">Compras inteligentes</p>
      </div>
      <nav className="flex-1 space-y-4 p-3" aria-label="Navegação principal">
        {navGroups.map((group) => (
          <NavGroup key={group.label ?? 'root'} label={group.label} items={group.items} onNavigate={() => setMobileOpen(false)} />
        ))}
      </nav>
      <div className="border-t border-sidebar-border p-3">
        <div className="flex items-center gap-3 rounded-xl bg-sidebar-accent/60 p-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-full bg-primary/12 text-xs font-semibold text-primary">{user?.display_name?.slice(0, 2).toUpperCase() || 'US'}</div>
          <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{user?.display_name}</p><p className="truncate text-xs text-muted-foreground">{isAdmin ? 'Acesso administrativo' : 'Conta pessoal'}</p></div>
          <Button variant="ghost" size="icon" onClick={logout} aria-label="Sair"><LogOut /></Button>
        </div>
      </div>
    </div>
  )

  return (
    <div className="min-h-screen bg-background text-foreground">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-sidebar-border bg-sidebar lg:block">{sidebar}</aside>
      {mobileOpen ? <button className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden" onClick={() => setMobileOpen(false)} aria-label="Fechar menu" /> : null}
      <aside className={cn('fixed inset-y-0 left-0 z-50 w-72 border-r border-sidebar-border bg-sidebar transition-transform duration-200 lg:hidden', mobileOpen ? 'translate-x-0' : '-translate-x-full')}>
        <Button variant="ghost" size="icon" className="absolute right-3 top-3 z-10" onClick={() => setMobileOpen(false)} aria-label="Fechar menu"><X /></Button>{sidebar}
      </aside>
      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-border/70 bg-background/80 px-4 backdrop-blur-xl sm:px-6 lg:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Abrir menu"><Menu /></Button>
          <div className="hidden items-center gap-2 lg:flex">
            {onAdminArea ? <><span className="text-sm text-muted-foreground">Área administrativa</span><Badge variant="destructive">{user?.role}</Badge></> : <span className="text-sm text-muted-foreground">Área do usuário</span>}
          </div>
          <ThemeToggle />
        </header>
        <motion.main initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, ease: 'easeOut' }} className="mx-auto w-full max-w-7xl px-4 py-7 sm:px-6 lg:px-8 lg:py-10">
          <Outlet />
        </motion.main>
      </div>
    </div>
  )
}

function NavGroup({ label, items, onNavigate }: { label?: string; items: NavItem[]; onNavigate: () => void }) {
  return (
    <div className="space-y-1">
      {label ? <p className="px-3 text-xs font-semibold uppercase tracking-wider text-sidebar-foreground/40">{label}</p> : null}
      {items.map(({ to, label: itemLabel, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={onNavigate} className={({ isActive }) => cn('flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-sidebar-foreground/65 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground', isActive && 'bg-sidebar-accent text-sidebar-accent-foreground shadow-sm')}>
          <Icon className="size-4" />{itemLabel}
        </NavLink>
      ))}
    </div>
  )
}
