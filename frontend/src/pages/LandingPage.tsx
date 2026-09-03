import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion, useInView } from 'motion/react'
import { useAuth } from '@/auth/AuthContext'
import { BrandLogo } from '@/components/BrandLogo'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const STORES = ['Amazon', 'Mercado Livre', 'Magalu', 'KaBuM!', 'Pichau', 'Terabyte']

const FEATURE_GROUPS = [
  {
    title: 'Do seu jeito',
    description: 'Diga qual produto você quer e, se fizer sentido, até quanto vale a pena pagar.',
  },
  {
    title: 'Sem ficar conferindo loja por loja',
    description: 'O GG Oferta acompanha os preços continuamente nas lojas que você escolher, sem você precisar voltar toda hora só para ver se mudou.',
  },
  {
    title: 'Onde for melhor pra você',
    description: 'Acompanhe pela Web ou receba um aviso no Telegram — as duas mostram a mesma coisa, do jeito que preferir.',
  },
  {
    title: 'Antes de decidir',
    description: 'Veja o histórico de preço e, quando a loja informar, as condições de parcelamento.',
  },
]

/** Subtask 10 (revisão de apresentação): Landing pública em `/`. Usuário
 * autenticado nunca vê esta página -- mesmo padrão já usado por
 * `LoginPage`/`RegisterPage` (redireciona para `/app`, ou a rota que
 * tentou abrir antes). */
export function LandingPage() {
  const { user } = useAuth()

  if (user) {
    return <Navigate to="/app" replace />
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <SiteHeader />
      <Hero />
      <Story />
      <Features />
      <Stores />
      <FinalCta />
      <SiteFooter />
    </div>
  )
}

function SiteHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-border/70 bg-background/80 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <Link to="/">
          <BrandLogo className="h-8 w-auto" />
        </Link>
        <div className="flex items-center gap-1 sm:gap-2">
          <ThemeToggle />
          <Button asChild variant="ghost">
            <Link to="/login">Entrar</Link>
          </Button>
          <Button asChild>
            <Link to="/cadastro">Criar conta</Link>
          </Button>
        </div>
      </div>
    </header>
  )
}

function Hero() {
  return (
    <section className="border-b border-border/70">
      <div className="mx-auto grid max-w-6xl gap-12 px-4 py-16 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:py-24 lg:px-8">
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
        >
          <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
            <span className="block font-normal text-muted-foreground">Você diz o que quer comprar.</span>
            <span className="block">A gente cuida de ficar de olho no preço.</span>
          </h1>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg">
            Sem abrir seis lojas todo dia pra ver se caiu. Quando aparecer uma oferta que faça
            sentido, a gente te mostra — pelo site ou no Telegram.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Button asChild size="lg">
              <Link to="/cadastro">Criar conta</Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link to="/login">Entrar</Link>
            </Button>
          </div>
        </motion.div>
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1, ease: 'easeOut' }}
        >
          <TelegramDemo />
        </motion.div>
      </div>
    </section>
  )
}

// --- Demonstração animada de conversa no Telegram --------------------------
//
// Reproduz o formato real de duas mensagens que o bot já envia hoje
// (`app.telegram.confirmation.describe_create_mission` e a confirmação de
// `✅ Missão criada!` em `app.telegram.router`), simplificadas para caber
// numa bolha de chat, e a estrutura real de um alerta de oferta
// (`app.telegram.notifications`: produto, loja, preço à vista, parcelamento,
// link "Ver anúncio"). O produto e o preço são fictícios -- por isso o card
// final é rotulado "Exemplo de oferta", nunca apresentado como dado real.
const DEMO_PRODUCT = 'Fone de ouvido bluetooth'
const DEMO_STORE = 'Amazon'
const DEMO_PRICE = 'R$ 179,90'
const DEMO_INSTALLMENT = '3x sem juros'

type DemoId = 'user' | 'bot1' | 'divider' | 'bot2' | 'card'
const ALL_DEMO_IDS: DemoId[] = ['user', 'bot1', 'divider', 'bot2', 'card']

function TelegramDemo() {
  const prefersReducedMotion = useReducedMotion()
  const containerRef = useRef<HTMLDivElement>(null)
  const isInView = useInView(containerRef, { once: true, amount: 0.4 })
  const [visible, setVisible] = useState<DemoId[]>([])
  const [typing, setTyping] = useState(false)
  const [cycle, setCycle] = useState(0)

  useEffect(() => {
    // `useReducedMotion` resolve de forma assíncrona (via efeito interno do
    // motion/react, nunca no primeiro render/SSR) -- por isso este efeito
    // reage a ele em vez de só ler seu valor uma vez no `useState` inicial.
    // Sem isso, se o valor mudasse de `null`/`false` para `true` depois da
    // montagem, o widget ficaria vazio para sempre em vez de mostrar o
    // estado final estático exigido para `prefers-reduced-motion`.
    if (prefersReducedMotion) {
      setVisible(ALL_DEMO_IDS)
      setTyping(false)
      return
    }
    if (!isInView) return
    setVisible([])
    setTyping(false)
    const timers: ReturnType<typeof setTimeout>[] = []
    const at = (ms: number, run: () => void) => timers.push(setTimeout(run, ms))
    const show = (id: DemoId) => setVisible((current) => [...current, id])

    at(400, () => show('user'))
    at(1300, () => setTyping(true))
    at(2500, () => {
      setTyping(false)
      show('bot1')
    })
    at(3400, () => show('divider'))
    at(4300, () => setTyping(true))
    at(5500, () => {
      setTyping(false)
      show('bot2')
    })
    at(6300, () => show('card'))
    at(11500, () => setCycle((value) => value + 1))

    return () => timers.forEach(clearTimeout)
  }, [cycle, isInView, prefersReducedMotion])

  const has = (id: DemoId) => visible.includes(id)

  return (
    <div ref={containerRef} className="mx-auto w-full max-w-sm">
      <p className="mb-2 text-xs font-medium text-muted-foreground">
        Demonstração de uma conversa no Telegram
      </p>
      <div
        aria-hidden="true"
        className="overflow-hidden rounded-2xl border border-border/70 bg-card shadow-card"
      >
        <div className="flex items-center gap-2 border-b border-border/70 bg-muted/40 px-3.5 py-2">
          <BrandLogo className="h-4 w-auto" />
          <span className="ml-auto text-[10px] text-muted-foreground">bot</span>
        </div>
        <div className="flex min-h-[300px] flex-col justify-end gap-2.5 p-4">
          <AnimatePresence mode="popLayout">
            {has('user') && (
              <Bubble key="user" from="user">
                Quero um fone bluetooth até R$ 200
              </Bubble>
            )}
            {typing && !has('bot1') && <TypingBubble key="typing1" />}
            {has('bot1') && (
              <Bubble key="bot1" from="bot">
                Entendi! Vou acompanhar esse produto pra você.
              </Bubble>
            )}
            {has('divider') && <Divider key="divider">2 dias depois</Divider>}
            {typing && has('bot1') && !has('bot2') && <TypingBubble key="typing2" />}
            {has('bot2') && (
              <Bubble key="bot2" from="bot">
                Encontrei uma oferta que pode te interessar 👇
              </Bubble>
            )}
            {has('card') && <DemoOfferCard key="card" />}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}

function Bubble({ from, children }: { from: 'user' | 'bot'; children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      className={cn('flex', from === 'user' ? 'justify-end' : 'justify-start')}
    >
      <span
        className={cn(
          'max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-snug',
          from === 'user'
            ? 'rounded-br-sm bg-primary text-primary-foreground'
            : 'rounded-bl-sm bg-muted text-foreground',
        )}
      >
        {children}
      </span>
    </motion.div>
  )
}

function TypingBubble() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="flex justify-start"
    >
      <span className="inline-flex items-center gap-1 rounded-2xl rounded-bl-sm bg-muted px-3.5 py-3">
        {[0, 0.15, 0.3].map((delay) => (
          <motion.span
            key={delay}
            className="size-1.5 rounded-full bg-muted-foreground/60"
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1, repeat: Infinity, delay }}
          />
        ))}
      </span>
    </motion.div>
  )
}

function Divider({ children }: { children: ReactNode }) {
  return (
    <motion.p
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="py-0.5 text-center text-[11px] font-medium text-muted-foreground"
    >
      {children}
    </motion.p>
  )
}

function DemoOfferCard() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="rounded-xl border border-border bg-background/70 p-3.5"
    >
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Exemplo de oferta
      </p>
      <p className="mt-1 text-sm font-medium">{DEMO_PRODUCT}</p>
      <p className="text-xs text-muted-foreground">{DEMO_STORE}</p>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="text-lg font-semibold text-[#1a8ecf] dark:text-[#5cc4ff]">{DEMO_PRICE}</span>
        <span className="text-xs text-muted-foreground">{DEMO_INSTALLMENT}</span>
      </div>
      <span className="mt-2 inline-block text-xs font-medium text-primary">Ver oferta →</span>
    </motion.div>
  )
}

// --- Narrativa (substitui "Como funciona" genérico) -------------------------

function Story() {
  return (
    <section className="py-20 sm:py-24">
      <div className="mx-auto max-w-5xl space-y-16 px-4 sm:px-6 lg:px-8 lg:space-y-20">
        <StoryRow
          eyebrow="01"
          title="Você conta o que está procurando"
          description="Um produto, um preço que faz sentido pra você e as lojas que preferir. Só isso."
          visual={<MissionPreview />}
        />
        <StoryRow
          reverse
          eyebrow="02"
          title="A gente fica de olho, sem te incomodar"
          description="O GG Oferta acompanha o preço nas lojas certas. Nada de você voltar todo dia só pra conferir."
          visual={<PriceDropPreview />}
        />
      </div>
    </section>
  )
}

function StoryRow({
  eyebrow,
  title,
  description,
  visual,
  reverse,
}: {
  eyebrow: string
  title: string
  description: string
  visual: ReactNode
  reverse?: boolean
}) {
  return (
    <div className="grid items-center gap-8 lg:grid-cols-2 lg:gap-16">
      <div className={cn(reverse && 'lg:order-2')}>
        <span className="text-sm font-semibold text-primary/70">{eyebrow}</span>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h2>
        <p className="mt-3 max-w-md text-base leading-relaxed text-muted-foreground">{description}</p>
      </div>
      <div className={cn('flex justify-center', reverse && 'lg:order-1')}>{visual}</div>
    </div>
  )
}

function MissionPreview() {
  return (
    <div className="w-full max-w-xs rounded-2xl border border-border/70 bg-card p-5 shadow-card">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Exemplo</p>
      <p className="mt-2 text-sm font-medium">🔎 Fone de ouvido bluetooth</p>
      <p className="mt-1 text-sm text-muted-foreground">🎯 até R$ 200</p>
      <p className="mt-1 text-sm text-muted-foreground">🏪 Amazon, Magalu, KaBuM!</p>
    </div>
  )
}

function PriceDropPreview() {
  return (
    <div className="w-full max-w-xs rounded-2xl border border-border/70 bg-card p-5 shadow-card">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Exemplo</p>
      <p className="mt-2 text-sm font-medium">Fone de ouvido bluetooth</p>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-sm text-muted-foreground line-through">R$ 219,90</span>
        <span className="text-xl font-semibold text-[#1a8ecf] dark:text-[#5cc4ff]">R$ 179,90</span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">↘️ Preço caiu — dentro do que você definiu</p>
    </div>
  )
}

// --- Recursos (agrupados, sem sensação de catálogo) -------------------------

function Features() {
  return (
    <section className="border-t border-border/70 bg-muted/20 py-20">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
        <div className="grid gap-x-12 gap-y-10 sm:grid-cols-2">
          {FEATURE_GROUPS.map(({ title, description }) => (
            <div key={title} className="border-l-2 border-primary/30 pl-5">
              <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function Stores() {
  return (
    <section className="py-14">
      <div className="mx-auto max-w-5xl px-4 text-center sm:px-6 lg:px-8">
        <p className="text-sm text-muted-foreground">Lojas que o GG Oferta acompanha</p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2.5">
          {STORES.map((store) => (
            <span
              key={store}
              className="rounded-full border border-border bg-card px-3.5 py-1.5 text-sm text-foreground/80"
            >
              {store}
            </span>
          ))}
        </div>
      </div>
    </section>
  )
}

function FinalCta() {
  return (
    <section className="border-t border-border/70 bg-muted/20 py-20">
      <div className="mx-auto max-w-xl px-4 text-center sm:px-6 lg:px-8">
        <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Já sabe o que quer comprar?</h2>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">
          Deixa o GG Oferta acompanhar o preço por você.
        </p>
        <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link to="/cadastro">Criar conta</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link to="/login">Entrar</Link>
          </Button>
        </div>
      </div>
    </section>
  )
}

function SiteFooter() {
  return (
    <footer className="border-t border-border/70">
      <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 px-4 py-10 text-sm text-muted-foreground sm:flex-row sm:justify-between sm:px-6 lg:px-8">
        <BrandLogo className="h-6 w-auto" />
        <nav className="flex items-center gap-5">
          <Link to="/login" className="hover:text-foreground">Entrar</Link>
          <Link to="/cadastro" className="hover:text-foreground">Criar conta</Link>
        </nav>
      </div>
    </footer>
  )
}
