import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Plus, Search, ShieldCheck, ShoppingBag, Target } from 'lucide-react'
import { accountApi } from '@/api/account'
import { ApiError } from '@/api/client'
import { missionsApi } from '@/api/missions'
import { offersApi } from '@/api/offers'
import type { AccountProfile, AccountQuota, OfferSummary } from '@/api/types'
import { useAuth } from '../auth/authContextValue'
import { offerSummaryToCardData } from './offers/offerCardMapping'
import { OfferCard } from '@/components/OfferCard'
import { PageHeader } from '@/components/PageHeader'
import { QuotaUsageRow } from '@/components/QuotaSummary'
import { ErrorState, LoadingState } from '@/components/StatePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface HomeData {
  activeCount: number
  pausedCount: number
  totalMissions: number
  offers: OfferSummary[]
  account: AccountProfile
  quota: AccountQuota
}

const QUICK_ACTIONS = [
  { to: '/app/missions/new', label: 'Criar missão', description: 'Conte o que você quer acompanhar', icon: Plus },
  { to: '/app/search', label: 'Buscar produto', description: 'Compare o que já encontramos', icon: Search },
  { to: '/app/offers', label: 'Ver ofertas', description: 'Confira as melhores oportunidades', icon: ShoppingBag },
  { to: '/app/missions', label: 'Minhas missões', description: 'Veja tudo o que está no radar', icon: Target },
]

async function loadHomeData(): Promise<HomeData> {
  const [active, paused, all, offers, account, quota] = await Promise.all([
    missionsApi.list('active', 1, 0),
    missionsApi.list('paused', 1, 0),
    missionsApi.list('all', 1, 0),
    offersApi.list({ limit: 3, sort: 'recent' }),
    accountApi.get(),
    accountApi.getQuota(),
  ])
  if (!active || !paused || !all || !offers || !account || !quota) {
    throw new Error('Resposta inesperada do servidor.')
  }
  return {
    activeCount: active.total,
    pausedCount: paused.total,
    totalMissions: all.total,
    offers: offers.items,
    account,
    quota,
  }
}

export function AppHome() {
  const { user, isAdmin } = useAuth()
  const [data, setData] = useState<HomeData | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Só para o botão "Tentar novamente" pedir os mesmos dados de novo, sem
  // chamar a busca por referência de dentro do efeito.
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    loadHomeData().then(
      (loaded) => { if (!cancelled) setData(loaded) },
      (loadError) => {
        if (cancelled) return
        setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar sua área.')
      },
    )
    return () => { cancelled = true }
  }, [retryToken])

  if (error && !data) return <ErrorState title="Não foi possível carregar sua área" description={error} onRetry={() => setRetryToken((token) => token + 1)} />
  if (!data) return <LoadingState label="Carregando…" />

  return <AppHomeView displayName={user?.display_name} isAdmin={isAdmin} data={data} />
}

export function AppHomeView({
  displayName,
  isAdmin,
  data,
}: {
  displayName?: string
  isAdmin: boolean
  data: HomeData
}) {
  const isNewUser = data.totalMissions === 0
  const nearLimitItems = [
    { label: 'Missões ativas', item: data.quota.active_missions },
    { label: 'Lojas monitoradas', item: data.quota.store_slots },
    { label: 'Pesquisas hoje', item: data.quota.daily_searches },
  ].filter(({ item }) => item.near_limit)

  const telegramLabel =
    data.account.telegram_link_status === 'linked'
      ? 'Telegram vinculado'
      : data.account.telegram_link_status === 'pending'
        ? 'Vinculação do Telegram pendente'
        : 'Telegram não vinculado'

  return (
    <section>
      <PageHeader eyebrow="Visão geral" title={`Olá, ${displayName || 'bem-vindo'}`} description="Veja de uma vez o que está no seu radar e as ofertas que apareceram desde a última visita." />

      <div className="mb-7 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {QUICK_ACTIONS.map(({ to, label, description, icon: Icon }) => (
          <Button key={to} variant="outline" className="h-auto min-h-28 flex-col items-start gap-2.5 px-4 py-4 text-left sm:min-h-32" asChild>
            <Link to={to}>
              <span className="grid size-9 place-items-center rounded-xl bg-primary/10 text-primary"><Icon /></span>
              <span><span className="block text-sm">{label}</span><span className="mt-1 hidden text-xs font-normal leading-5 text-muted-foreground sm:block">{description}</span></span>
            </Link>
          </Button>
        ))}
      </div>

      {isAdmin ? (
        <p className="mb-6 text-sm text-muted-foreground">
          Você tem acesso administrativo. <Link className="inline-flex items-center gap-1 font-medium text-primary hover:underline" to="/admin"><ShieldCheck className="size-3.5" />Ir para o painel ADMIN</Link>
        </p>
      ) : null}

      {isNewUser ? (
        <Card className="border-dashed border-primary/20 bg-[linear-gradient(135deg,color-mix(in_oklab,var(--primary)_5%,var(--card)),var(--card)_48%)]">
          <CardContent className="flex flex-col items-center gap-3.5 p-8 text-center sm:p-10">
            <div className="grid size-12 place-items-center rounded-2xl bg-primary/12 text-primary ring-1 ring-primary/15"><Target className="size-5" /></div>
            <CardTitle className="text-xl font-bold tracking-[-0.025em]">O que você quer colocar no radar?</CardTitle>
            <CardDescription className="max-w-lg text-sm leading-6">Conte o produto, o preço que seria bom para você e em quais lojas vale procurar. O resto fica com a gente.</CardDescription>
            <Button asChild size="lg"><Link to="/app/missions/new">Criar minha primeira missão <ArrowRight /></Link></Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader className="gap-1"><CardDescription>Missões ativas</CardDescription><CardTitle className="text-3xl">{data.activeCount}</CardTitle></CardHeader>
            </Card>
            <Card>
              <CardHeader className="gap-1"><CardDescription>Missões pausadas</CardDescription><CardTitle className="text-3xl">{data.pausedCount}</CardTitle></CardHeader>
            </Card>
            <Card>
              <CardHeader className="gap-1">
                <CardDescription>Telegram</CardDescription>
                <div className="flex items-center gap-2">
                  <Badge variant={data.account.telegram_link_status === 'linked' ? 'success' : data.account.telegram_link_status === 'pending' ? 'warning' : 'outline'}>
                    {telegramLabel}
                  </Badge>
                </div>
              </CardHeader>
            </Card>
          </div>

          {nearLimitItems.length > 0 ? (
            <Card className="mb-6 border-warning/30">
              <CardHeader><CardTitle className="text-base">Perto do limite</CardTitle><CardDescription>Alguns limites da sua conta estão quase no máximo.</CardDescription></CardHeader>
              <CardContent className="space-y-4">
                {nearLimitItems.map(({ label, item }) => <QuotaUsageRow key={label} label={label} item={item} />)}
              </CardContent>
            </Card>
          ) : null}

          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold tracking-tight">Últimas ofertas relevantes</h2>
            {data.offers.length > 0 ? <Link className="text-sm font-medium text-primary hover:underline" to="/app/offers">Ver todas</Link> : null}
          </div>
          {data.offers.length === 0 ? (
            <Card className="border-dashed">
              <CardContent className="flex flex-col items-center gap-2 p-8 text-center">
                <CardDescription className="max-w-md">Nada novo por enquanto. Quando surgir uma oferta que combine com suas missões, ela aparece aqui.</CardDescription>
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4 sm:grid-cols-3">
              {data.offers.map((offer, index) => (
                <OfferCard key={offer.id} offer={offerSummaryToCardData(offer)} action={{ label: 'Ver detalhes', to: `/app/offers/${offer.id}` }} index={index} />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}
