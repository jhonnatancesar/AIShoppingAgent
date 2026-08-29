import { useState, type ComponentType } from 'react'
import { motion } from 'motion/react'
import { Bot, Home, LogOut, Menu, Search, ShieldCheck, ShoppingBag, Target, UserRound, X } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '@/auth/AuthContext'
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
  { to: '/app/account', label: 'Minha conta', icon: UserRound },
]

const ADMIN_NAV: NavItem[] = [
  { to: '/admin', label: 'Dashboard', icon: ShieldCheck, end: true },
  { to: '/app', label: 'Área do usuário', icon: Home },
]

export function AppLayout() {
  const { isAdmin } = useAuth()
  const nav = isAdmin ? [...USER_NAV, { to: '/admin', label: 'Administração', icon: ShieldCheck }] : USER_NAV
  return <Shell nav={nav} subtitle="Compras inteligentes" />
}
export function AdminLayout() { return <Shell nav={ADMIN_NAV} subtitle="Operação e dados" admin /> }

function Shell({ nav, subtitle, admin = false }: { nav: NavItem[]; subtitle: string; admin?: boolean }) {
  const { user, logout } = useAuth()
  const [mobileOpen, setMobileOpen] = useState(false)

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center gap-3 border-b border-sidebar-border px-5">
        <div className="grid size-9 place-items-center rounded-xl bg-primary text-primary-foreground shadow-glow"><Bot className="size-5" /></div>
        <div className="min-w-0"><p className="truncate text-sm font-semibold tracking-tight">AIShoppingAgent</p><p className="truncate text-xs text-muted-foreground">{subtitle}</p></div>
      </div>
      <nav className="flex-1 space-y-1 p-3" aria-label="Navegação principal">
        {nav.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} onClick={() => setMobileOpen(false)} className={({ isActive }) => cn('flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-sidebar-foreground/65 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground', isActive && 'bg-sidebar-accent text-sidebar-accent-foreground shadow-sm')}>
            <Icon className="size-4" />{label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-sidebar-border p-3">
        <div className="flex items-center gap-3 rounded-xl bg-sidebar-accent/60 p-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-full bg-primary/12 text-xs font-semibold text-primary">{user?.display_name?.slice(0, 2).toUpperCase() || 'US'}</div>
          <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{user?.display_name}</p><p className="truncate text-xs text-muted-foreground">{admin ? 'Administrador' : 'Conta pessoal'}</p></div>
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
          <div className="hidden items-center gap-2 lg:flex"><span className="text-sm text-muted-foreground">Área {admin ? 'administrativa' : 'do usuário'}</span>{admin ? <Badge variant="destructive">DEV</Badge> : null}</div>
          <ThemeToggle />
        </header>
        <motion.main initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, ease: 'easeOut' }} className="mx-auto w-full max-w-7xl px-4 py-7 sm:px-6 lg:px-8 lg:py-10">
          <Outlet />
        </motion.main>
      </div>
    </div>
  )
}
