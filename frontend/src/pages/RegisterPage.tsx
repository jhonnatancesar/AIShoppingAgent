import { useState, type FormEvent } from 'react'
import { Navigate, Link, useLocation } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { UserPlus } from 'lucide-react'
import { ApiError } from '../api/client'
import { registrationApi } from '../api/auth'
import { useAuth } from '../auth/AuthContext'
import { FormMessage } from '@/components/FormMessage'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

/** Cadastro Web self-service (Subtask 9) -- só usuário/e-mail/senha,
 * nunca role/preferências aqui (preferências ficam para depois, em
 * "Minha conta"). Sessão já vem criada pelo backend na resposta; só
 * falta `refresh()` para o AuthContext enxergar o usuário logado. */
export function RegisterPage() {
  const { user, refresh } = useAuth()
  const location = useLocation()
  const prefersReducedMotion = useReducedMotion()
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirmation, setPasswordConfirmation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [done, setDone] = useState(false)

  if (user || done) {
    const state = location.state as { from?: { pathname?: string } } | null
    return <Navigate to={state?.from?.pathname || '/app'} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    if (password !== passwordConfirmation) {
      setError('As senhas informadas não conferem.')
      return
    }
    setPending(true)
    try {
      await registrationApi.register({ username, email, password, password_confirmation: passwordConfirmation })
      await refresh()
      setDone(true)
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível criar sua conta. Tente novamente.')
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
          <img src="/logo-icon.png" alt="GG Oferta" className="mb-3 size-12" width={48} height={48} />
          <CardTitle className="text-xl">Criar conta</CardTitle>
          <CardDescription>Pesquise produtos e acompanhe preços no GG Oferta.</CardDescription>
        </CardHeader>
        <CardContent><form className="space-y-4" onSubmit={handleSubmit}>
        <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="username">Usuário</label>
        <Input id="username" name="username" autoComplete="username" maxLength={32} value={username} onChange={(event) => setUsername(event.target.value)} required /></div>
        <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="email">E-mail</label>
        <Input id="email" name="email" type="email" autoComplete="email" maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} required /></div>
        <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="password">Senha</label>
        <Input id="password" name="password" type="password" autoComplete="new-password" maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required /></div>
        <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="password-confirmation">Confirmar senha</label>
        <Input id="password-confirmation" name="password-confirmation" type="password" autoComplete="new-password" maxLength={128} value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} required /></div>
        <FormMessage tone="error">{error}</FormMessage>
        <Button className="w-full" size="lg" type="submit" disabled={pending}>
          <UserPlus />{pending ? 'Criando…' : 'Criar conta'}
        </Button>
        <p className="text-center text-xs leading-relaxed text-muted-foreground">
          Já tem conta? <Link className="font-medium text-primary hover:underline" to="/login">Entrar</Link>
        </p>
      </form></CardContent></Card></motion.div>
    </div>
  )
}
