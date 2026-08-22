import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { ArrowRight, BellRing, Search, Target } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function AppHome() {
  const { user } = useAuth()

  return (
    <section>
      <PageHeader eyebrow="Visão geral" title={`Olá, ${user?.display_name || 'bem-vindo'}`} description="Acompanhe na Web as mesmas missões e ofertas que você controla pelo Telegram." />
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .08, duration: .3 }}>
        <Card className="overflow-hidden border-primary/20 bg-gradient-to-br from-card via-card to-primary/8">
          <CardContent className="grid gap-8 p-6 sm:p-8 lg:grid-cols-[1fr_auto] lg:items-center">
            <div><div className="mb-5 grid size-11 place-items-center rounded-xl bg-primary/12 text-primary"><Target className="size-5" /></div><h2 className="text-xl font-semibold tracking-tight sm:text-2xl">Encontre o melhor momento para comprar.</h2><p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground sm:text-base">Crie uma missão, selecione as lojas e deixe o agente organizar ofertas relevantes para você.</p></div>
            <Button asChild size="lg"><Link to="/app/search">Pesquisar produtos <ArrowRight /></Link></Button>
          </CardContent>
        </Card>
      </motion.div>
      <div className="mt-5 grid gap-4 sm:grid-cols-3">
        {[{ icon: Search, title: 'Busca multiloja', text: 'Amazon, Pichau, KaBuM! e Terabyte.' }, { icon: BellRing, title: 'Alertas conectados', text: 'Web e Telegram acompanham a mesma missão.' }, { icon: Target, title: 'Ofertas relevantes', text: 'Seleção por relevância, condição e vendedor.' }].map(({ icon: Icon, title, text }) => (
          <Card key={title}><CardHeader><div className="mb-2 grid size-9 place-items-center rounded-lg bg-muted text-muted-foreground"><Icon className="size-4" /></div><CardTitle className="text-sm">{title}</CardTitle><CardDescription>{text}</CardDescription></CardHeader></Card>
        ))}
      </div>
    </section>
  )
}
