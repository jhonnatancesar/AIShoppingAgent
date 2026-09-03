import { useState, type FormEvent } from 'react'
import { Navigate, Link, useLocation } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { LockKeyhole } from 'lucide-react'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { BrandLogo } from '@/components/BrandLogo'
import { FormMessage } from '@/components/FormMessage'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

interface LocationState {
  from?: { pathname?: string }
}

/** Login único (Subtask 8): sem escolha de perfil/role -- o servidor
 * autentica e devolve o papel real da conta; o destino pós-login é
 * sempre `/app` (ou a página que o usuário tentou abrir antes de ser
 * redirecionado para cá), nunca uma rota "de admin" escolhida aqui. */
export function LoginPage() {
  const { user, login } = useAuth()
  const location = useLocation()
  const prefersReducedMotion = useReducedMotion()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  if (user) {
    const state = location.state as LocationState | null
    const destination = state?.from?.pathname || '/app'
    return <Navigate to={destination} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      await login(username, password)
    } catch (submitError) {
      if (submitError instanceof ApiError) {
        setError(submitError.message)
      } else {
        setError('Não foi possível entrar. Tente novamente.')
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden bg-background px-4 py-12 text-foreground">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,color-mix(in_oklab,var(--primary)_12%,transparent),transparent_42%)]" />
      <div className="absolute right-4 top-4"><ThemeToggle /></div>
      <motion.div initial={prefersReducedMotion ? false : { opacity: 0, y: 12, scale: .99 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ duration: .35, ease: 'easeOut' }} className="relative w-full max-w-sm">
      <Card className="border-border/80 bg-card/90 backdrop-blur-xl">
        <CardHeader className="items-center text-center">
          <BrandLogo className="mb-3 h-10 w-auto" />
          <CardDescription>Acesse suas missões e ofertas monitoradas.</CardDescription>
        </CardHeader>
        <CardContent><form className="space-y-4" onSubmit={handleSubmit}>
        <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="username">Usuário</label>
        <Input
          id="username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          required
        /></div>
        <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="password">Senha</label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        /></div>
        <FormMessage tone="error">{error}</FormMessage>
        <Button className="w-full" size="lg" type="submit" disabled={pending}>
          <LockKeyhole />{pending ? 'Entrando…' : 'Entrar'}
        </Button>
        <p className="text-center text-xs leading-relaxed text-muted-foreground">
          Sua senha é a mesma criada pelo link enviado no Telegram.
        </p>
        <p className="text-center text-xs leading-relaxed text-muted-foreground">
          Esqueceu a senha? <Link className="font-medium text-primary hover:underline" to="/recuperar">Recuperar acesso</Link>
        </p>
        <p className="text-center text-sm text-muted-foreground">
          Ainda não tem conta? <Link className="font-medium text-primary hover:underline" to="/cadastro">Criar conta</Link>
        </p>
      </form></CardContent></Card></motion.div>
    </div>
  )
}
