import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion, useInView } from 'motion/react'
import { useAuth } from '@/auth/authContextValue'
import { BrandLogo } from '@/components/BrandLogo'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { StoreName } from '@/components/StoreMark'

const STORES = [
  { code: 'amazon', name: 'Amazon' },
  { code: 'mercadolivre', name: 'Mercado Livre' },
  { code: 'magalu', name: 'Magalu' },
  { code: 'kabum', name: 'KaBuM!' },
  { code: 'pichau', name: 'Pichau' },
  { code: 'terabyte', name: 'Terabyte' },
]

const FEATURE_GROUPS = [
  {
    title: 'Um alerta do seu jeito',
    description: 'Escolha o produto, o preço desejado e as lojas que você considera confiáveis.',
  },
  {
    title: 'Comparação em um só lugar',
    description: 'Consulte as ofertas encontradas sem abrir várias abas nem refazer a mesma busca.',
  },
  {
    title: 'Avisos quando importam',
    description: 'Acompanhe pelo site ou receba no Telegram quando surgir uma oportunidade compatível.',
  },
  {
    title: 'Mais contexto para decidir',
    description: 'Veja o histórico do preço e as condições de pagamento informadas pela loja.',
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
      <div className="mx-auto flex h-[4.5rem] max-w-6xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <Link to="/">
          <BrandLogo className="hidden h-8 sm:inline-flex" />
          <BrandLogo className="h-8 sm:hidden" compact />
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
    <section className="relative overflow-hidden border-b border-border/70">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_82%_14%,color-mix(in_oklab,var(--primary)_10%,transparent),transparent_30%)]" />
      <div className="relative mx-auto grid max-w-6xl gap-12 px-4 py-16 sm:px-6 lg:grid-cols-[1.1fr_.9fr] lg:items-center lg:px-8 lg:py-24">
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
        >
          <h1 className="max-w-2xl text-4xl font-bold leading-[1.06] tracking-[-0.055em] text-balance sm:text-6xl">
            <span className="block text-foreground">Encontre o momento certo</span>
            <span className="mt-2 block text-primary">para comprar.</span>
          </h1>
          <p className="mt-6 max-w-xl text-base leading-7 text-muted-foreground sm:text-lg sm:leading-8">
            Escolha o produto, o valor que faz sentido e as lojas de confiança. O GG Oferta
            compara os preços e avisa quando aparecer uma oportunidade.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Button asChild size="lg">
              <Link to="/cadastro">Criar meu alerta</Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link to="/login">Já tenho conta</Link>
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
  const [visible, setVisible] = useState<DemoId[]>(['user'])
  const [typing, setTyping] = useState(false)
  const [cycle, setCycle] = useState(0)

  useEffect(() => {
    // Com `prefers-reduced-motion`, o estado final é derivado direto na
    // renderização (`effectiveVisible`/`effectiveTyping` abaixo) -- nunca
    // precisa de setState aqui, então este efeito não tem nada a fazer
    // nesse caso (`useReducedMotion` resolve de forma assíncrona, por
    // isso o efeito ainda precisa reagir à mudança de `null`/`false` para
    // `true`, só que sem tocar estado).
    if (prefersReducedMotion) return
    if (!isInView) return
    const timers: ReturnType<typeof setTimeout>[] = []
    const at = (ms: number, run: () => void) => timers.push(setTimeout(run, ms))
    const show = (id: DemoId) => setVisible((current) => [...current, id])

    // Mesmo mecanismo de temporizador do resto da sequência (nunca
    // setState direto no corpo síncrono do efeito) -- `0ms` ainda roda
    // antes do primeiro quadro visível, sem atraso perceptível.
    at(0, () => { setVisible(['user']); setTyping(false) })
    at(600, () => setTyping(true))
    at(1600, () => {
      setTyping(false)
      show('bot1')
    })
    at(2300, () => show('divider'))
    at(3000, () => setTyping(true))
    at(4000, () => {
      setTyping(false)
      show('bot2')
    })
    at(4700, () => show('card'))
    at(9000, () => setCycle((value) => value + 1))

    return () => timers.forEach(clearTimeout)
  }, [cycle, isInView, prefersReducedMotion])

  // `prefers-reduced-motion` nunca passa pelo efeito de animação acima
  // (retorna cedo) -- o estado final é derivado aqui, na renderização,
  // em vez de sincronizado por setState dentro do efeito.
  const effectiveVisible = prefersReducedMotion ? ALL_DEMO_IDS : visible
  const effectiveTyping = prefersReducedMotion ? false : typing
  const has = (id: DemoId) => effectiveVisible.includes(id)

  return (
    <div ref={containerRef} className="mx-auto w-full max-w-sm">
      <p className="mb-2 text-xs font-medium text-muted-foreground">
        Demonstração de uma conversa no Telegram
      </p>
      <div
        aria-hidden="true"
        className="overflow-hidden rounded-2xl border border-border/70 bg-card shadow-card"
      >
        <div className="flex items-center gap-2 border-b border-border/70 bg-muted/40 px-3.5 py-2.5">
          <BrandLogo className="h-6" compact />
          <span className="ml-auto text-[10px] text-muted-foreground">bot</span>
        </div>
        <div className="flex min-h-[300px] flex-col justify-end gap-2.5 p-4">
          <AnimatePresence mode="popLayout">
            {has('user') && (
              <Bubble key="user" from="user">
                Quero um fone bluetooth até R$ 200
              </Bubble>
            )}
            {effectiveTyping && !has('bot1') && <TypingBubble key="typing1" />}
            {has('bot1') && (
              <Bubble key="bot1" from="bot">
                Entendi! Vou acompanhar esse produto pra você.
              </Bubble>
            )}
            {has('divider') && <Divider key="divider">2 dias depois</Divider>}
            {effectiveTyping && has('bot1') && !has('bot2') && <TypingBubble key="typing2" />}
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
      <p className="mt-1 text-xs text-muted-foreground"><StoreName store="amazon">{DEMO_STORE}</StoreName></p>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="text-lg font-bold text-opportunity">{DEMO_PRICE}</span>
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
      <div className="mx-auto max-w-5xl space-y-16 px-4 sm:px-6 lg:space-y-24 lg:px-8">
        <StoryRow
          eyebrow="01"
          title="Conte o que você procura"
          description="Informe o produto e, se quiser, o preço que pretende pagar. Depois, escolha as lojas que deseja comparar."
          visual={<MissionPreview />}
        />
        <StoryRow
          reverse
          eyebrow="02"
          title="Receba o aviso na hora certa"
          description="Quando o preço chegar ao seu objetivo, a oportunidade aparece no site e também pode chegar pelo Telegram."
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
    <div className="grid items-center gap-8 lg:grid-cols-2 lg:gap-20">
      <div className={cn(reverse && 'lg:order-2')}>
        <span className="text-xs font-bold tracking-[0.2em] text-primary/70">{eyebrow}</span>
        <h2 className="mt-3 text-2xl font-bold tracking-[-0.035em] sm:text-3xl">{title}</h2>
        <p className="mt-3 max-w-md text-base leading-7 text-muted-foreground">{description}</p>
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
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
        <StoreName store="amazon">Amazon</StoreName>
        <StoreName store="magalu">Magalu</StoreName>
        <StoreName store="kabum">KaBuM!</StoreName>
      </div>
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
        <span className="text-xl font-bold text-opportunity">R$ 179,90</span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">↘️ Preço caiu — dentro do que você definiu</p>
    </div>
  )
}

// --- Recursos (agrupados, sem sensação de catálogo) -------------------------

function Features() {
  return (
    <section className="border-y border-border/70 bg-muted/35 py-20">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
        <div className="grid gap-x-12 gap-y-10 sm:grid-cols-2">
          {FEATURE_GROUPS.map(({ title, description }) => (
            <div key={title} className="border-l-2 border-primary/35 pl-5">
              <h2 className="text-lg font-bold tracking-[-0.02em]">{title}</h2>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
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
        <h2 className="text-2xl font-bold tracking-[-0.035em]">Compare nas lojas que você já conhece</h2>
        <p className="mt-2 text-sm text-muted-foreground">As ofertas ficam organizadas para você avaliar preço, loja e condição de pagamento.</p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2.5">
          {STORES.map((store) => (
            <span
              key={store.code}
              className="rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium text-foreground/80 shadow-xs"
            >
              <StoreName store={store.code}>{store.name}</StoreName>
            </span>
          ))}
        </div>
      </div>
    </section>
  )
}

function FinalCta() {
  return (
    <section className="border-t border-primary/20 bg-primary py-20 text-primary-foreground">
      <div className="mx-auto max-w-xl px-4 text-center sm:px-6 lg:px-8">
        <h2 className="text-2xl font-bold tracking-[-0.035em] sm:text-3xl">Pronto para acompanhar seu próximo preço?</h2>
        <p className="mt-3 text-base leading-relaxed text-primary-foreground/75">
          Crie seu primeiro alerta em poucos passos e compare as oportunidades com calma.
        </p>
        <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button asChild size="lg" variant="secondary">
            <Link to="/cadastro">Criar meu alerta</Link>
          </Button>
          <Button asChild size="lg" variant="outline" className="border-primary-foreground/30 bg-transparent text-primary-foreground hover:bg-primary-foreground/10 hover:text-primary-foreground">
            <Link to="/login">Já tenho conta</Link>
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
        <BrandLogo className="h-7" />
        <nav className="flex items-center gap-5">
          <Link to="/login" className="hover:text-foreground">Entrar</Link>
          <Link to="/cadastro" className="hover:text-foreground">Criar conta</Link>
        </nav>
      </div>
    </footer>
  )
}
