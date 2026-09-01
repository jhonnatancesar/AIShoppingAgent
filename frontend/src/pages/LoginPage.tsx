import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { motion } from 'motion/react'
import { Bot, LockKeyhole } from 'lucide-react'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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
      <motion.div initial={{ opacity: 0, y: 12, scale: .99 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ duration: .35, ease: 'easeOut' }} className="relative w-full max-w-sm">
      <Card className="border-border/80 bg-card/90 backdrop-blur-xl">
        <CardHeader className="items-center text-center">
          <div className="mb-3 grid size-12 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-glow"><Bot className="size-6" /></div>
          <CardTitle className="text-xl">AIShoppingAgent</CardTitle>
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
        {error ? (
          <p className="login-error" role="alert">
            {error}
          </p>
        ) : null}
        <Button className="w-full" size="lg" type="submit" disabled={pending}>
          <LockKeyhole />{pending ? 'Entrando…' : 'Entrar'}
        </Button>
        <p className="text-center text-xs leading-relaxed text-muted-foreground">
          Sua senha é a mesma criada pelo link enviado no Telegram.
        </p>
      </form></CardContent></Card></motion.div>
    </div>
  )
}
