import type { ComponentType } from 'react'
import { Home, LifeBuoy, Search, ShieldCheck, ShoppingBag, Target, Ticket, UserRound } from 'lucide-react'

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

export type { NavItem, NavGroupSpec }
