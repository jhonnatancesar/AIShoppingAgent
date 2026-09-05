import { useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { LogOut, Menu, X } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/auth/authContextValue'
import { BrandLogo } from '@/components/BrandLogo'
import { navGroupsFor, type NavItem } from '@/components/navGroups'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/ThemeToggle'
import { cn } from '@/lib/utils'

/** Shell único para USER e ADMIN (Subtask 8) -- a diferença entre os dois
 * nunca é um layout separado, só a presença (ou não) da seção
 * "Administração" na mesma navegação, decidida por `isAdmin` (permissão
 * real vinda da sessão, nunca uma escolha feita no login). */
export function AppLayout() {
  const { user, isAdmin, logout } = useAuth()
  const location = useLocation()
  const prefersReducedMotion = useReducedMotion()
  const [mobileOpen, setMobileOpen] = useState(false)
  const onAdminArea = location.pathname.startsWith('/admin')
  const navGroups = navGroupsFor(isAdmin)

  function renderSidebar(onClose?: () => void) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex h-[4.5rem] items-center justify-between gap-2 border-b border-sidebar-border px-5">
          <BrandLogo className="h-9" />
          {onClose ? <Button variant="ghost" size="icon" className="shrink-0" onClick={onClose} aria-label="Fechar menu"><X /></Button> : null}
        </div>
        <nav className="flex-1 space-y-5 overflow-y-auto p-3.5" aria-label="Navegação principal">
          {navGroups.map((group) => (
            <NavGroup key={group.label ?? 'root'} label={group.label} items={group.items} onNavigate={() => setMobileOpen(false)} />
          ))}
        </nav>
        <div className="border-t border-sidebar-border p-3">
          <div className="flex items-center gap-3 rounded-2xl border border-sidebar-border bg-sidebar-accent/45 p-3">
            <div className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">{user?.display_name?.slice(0, 2).toUpperCase() || 'US'}</div>
            <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{user?.display_name}</p><p className="truncate text-xs text-muted-foreground">{isAdmin ? 'Acesso administrativo' : 'Conta pessoal'}</p></div>
            <Button variant="ghost" size="icon" onClick={logout} aria-label="Sair"><LogOut /></Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-sidebar-border bg-sidebar lg:block">{renderSidebar()}</aside>
      {mobileOpen ? <button className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden" onClick={() => setMobileOpen(false)} aria-label="Fechar menu" /> : null}
      <aside className={cn('fixed inset-y-0 left-0 z-50 w-72 border-r border-sidebar-border bg-sidebar transition-transform duration-200 lg:hidden', mobileOpen ? 'translate-x-0' : '-translate-x-full')}>
        {renderSidebar(() => setMobileOpen(false))}
      </aside>
      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 grid h-16 grid-cols-[2.5rem_1fr_2.5rem] items-center border-b border-border/70 bg-background/90 px-4 backdrop-blur-xl sm:px-6 lg:flex lg:justify-between lg:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Abrir menu"><Menu /></Button>
          <BrandLogo className="mx-auto h-7 lg:hidden" />
          <div className="hidden items-center gap-2 lg:flex">
            {onAdminArea ? (
              <><span className="hidden text-sm text-muted-foreground sm:inline">Área administrativa</span><Badge variant="destructive">{user?.role}</Badge></>
            ) : (
              <span className="hidden text-sm text-muted-foreground lg:inline">Área do usuário</span>
            )}
          </div>
          <div className="justify-self-end"><ThemeToggle /></div>
        </header>
        <motion.main initial={prefersReducedMotion ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.24, ease: 'easeOut' }} className="mx-auto w-full max-w-6xl px-4 py-7 sm:px-6 lg:px-8 lg:py-11">
          <Outlet />
        </motion.main>
      </div>
    </div>
  )
}

function NavGroup({ label, items, onNavigate }: { label?: string; items: NavItem[]; onNavigate: () => void }) {
  return (
    <div className="space-y-1.5">
      {label ? <p className="px-3 text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-sidebar-foreground/40">{label}</p> : null}
      {items.map(({ to, label: itemLabel, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={onNavigate} className={({ isActive }) => cn('group flex min-h-10 items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-sidebar-foreground/65 transition-[color,background-color,transform] hover:bg-sidebar-accent/70 hover:text-sidebar-accent-foreground', isActive && 'bg-sidebar-accent text-sidebar-accent-foreground')}>
          <Icon className="size-4" />{itemLabel}
        </NavLink>
      ))}
    </div>
  )
}
