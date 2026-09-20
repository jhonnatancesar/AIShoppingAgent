import type { ComponentType } from 'react'
import { Code2, Home, LifeBuoy, Search, ShieldCheck, ShoppingBag, Target, Ticket, UserRound } from 'lucide-react'

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

const DEV_NAV: NavItem[] = [
  { to: '/app/dev/pesquisas', label: 'Pesquisas (DEV)', icon: Code2 },
]

interface NavGroupSpec { label?: string; items: NavItem[] }

/** Única fonte da navegação exibida (Subtask 8, estendida na Frente 5) --
 * pura e testável sem Router/AuthContext: USER só enxerga `USER_NAV`;
 * ADMIN (permissão real da sessão, nunca escolha do login) enxerga
 * USER_NAV + "Administração"; DEV enxerga tudo isso mais "Pesquisas
 * (DEV)", nunca um layout/nav totalmente separado. */
export function navGroupsFor(isAdmin: boolean, isDev: boolean): NavGroupSpec[] {
  const groups: NavGroupSpec[] = [{ label: isAdmin ? 'Principal' : undefined, items: USER_NAV }]
  if (isAdmin) groups.push({ label: 'Administração', items: ADMIN_NAV })
  if (isDev) groups.push({ label: 'DEV', items: DEV_NAV })
  return groups
}

export type { NavItem, NavGroupSpec }
