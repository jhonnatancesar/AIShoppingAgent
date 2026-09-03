import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { KeyRound, Mail, Send } from 'lucide-react'
import { ApiError } from '../api/client'
import { recoveryApi, type VerificationChannel } from '../api/auth'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

type Step = 'identifier' | 'channel' | 'code' | 'done'

const CHANNEL_LABELS: Record<VerificationChannel, string> = {
  telegram: 'Telegram',
  email: 'E-mail',
}

/** Recuperação de senha (Subtask 9) -- mesmo fluxo compartilhado usado
 * pelo /recuperar e pelo /alterar_senha do Telegram: identificar conta
 * sem expor sua existência, escolher canal só entre os realmente
 * disponíveis, confirmar código + nova senha juntos. Nunca pede a senha
 * em nenhum outro lugar além desta página. */
export function RecoverPasswordPage() {
  const [step, setStep] = useState<Step>('identifier')
  const [identifier, setIdentifier] = useState('')
  const [channels, setChannels] = useState<VerificationChannel[]>([])
  const [channel, setChannel] = useState<VerificationChannel | null>(null)
  const [challengeId, setChallengeId] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPasswordConfirmation, setNewPasswordConfirmation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [noChannels, setNoChannels] = useState(false)
  const [pending, setPending] = useState(false)

  async function submitIdentifier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      const result = await recoveryApi.channels(identifier)
      const available = result?.channels ?? []
      if (available.length === 0) {
        setNoChannels(true)
        return
      }
      setChannels(available)
      if (available.length === 1) {
        await startRecovery(available[0])
      } else {
        setStep('channel')
      }
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível continuar. Tente novamente.')
    } finally {
      setPending(false)
    }
  }

  async function startRecovery(chosenChannel: VerificationChannel) {
    setError(null)
    setPending(true)
    try {
      const issued = await recoveryApi.request(identifier, chosenChannel)
      if (issued) {
        setChannel(chosenChannel)
        setChallengeId(issued.challenge_id)
        setStep('code')
      }
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível enviar o código. Tente novamente.')
    } finally {
      setPending(false)
    }
  }

  async function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    if (newPassword !== newPasswordConfirmation) {
      setError('As senhas informadas não conferem.')
      return
    }
    if (!challengeId) return
    setPending(true)
    try {
      await recoveryApi.confirm({
        challenge_id: challengeId,
        code,
        new_password: newPassword,
        new_password_confirmation: newPasswordConfirmation,
      })
      setStep('done')
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível concluir. Tente novamente.')
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
          <img src="/logo-icon.png" alt="GG Oferta" className="mb-3 size-12" width={48} height={48} />
          <CardTitle className="text-xl">Recuperar senha</CardTitle>
          <CardDescription>
            {step === 'identifier' && 'Informe seu usuário ou e-mail.'}
            {step === 'channel' && 'Escolha como quer receber o código.'}
            {step === 'code' && `Informe o código${channel ? ` enviado por ${CHANNEL_LABELS[channel]}` : ''} e sua nova senha.`}
            {step === 'done' && 'Senha redefinida.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {step === 'identifier' && !noChannels ? (
            <form className="space-y-4" onSubmit={submitIdentifier}>
              <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="identifier">Usuário ou e-mail</label>
              <Input id="identifier" name="identifier" maxLength={254} value={identifier} onChange={(event) => setIdentifier(event.target.value)} required /></div>
              {error ? <p className="login-error" role="alert">{error}</p> : null}
              <Button className="w-full" size="lg" type="submit" disabled={pending}><Send />{pending ? 'Buscando…' : 'Continuar'}</Button>
              <p className="text-center text-xs text-muted-foreground"><Link className="font-medium text-primary hover:underline" to="/login">Voltar para o login</Link></p>
            </form>
          ) : null}

          {noChannels ? (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground" role="status">
                Se essa conta existir e tiver um canal de recuperação disponível, você poderá solicitar um código.
                No momento não há um canal disponível para essa conta.
              </p>
              <p className="text-center text-xs text-muted-foreground"><Link className="font-medium text-primary hover:underline" to="/login">Voltar para o login</Link></p>
            </div>
          ) : null}

          {step === 'channel' ? (
            <div className="space-y-4">
              <div className="grid gap-2">
                {channels.map((option) => (
                  <button
                    key={option}
                    type="button"
                    disabled={pending}
                    onClick={() => startRecovery(option)}
                    className={cn('flex items-center gap-3 rounded-lg border border-border px-4 py-3 text-sm font-medium transition-colors hover:border-primary hover:bg-primary/5')}
                  >
                    {option === 'telegram' ? <Send className="size-4" /> : <Mail className="size-4" />}
                    {CHANNEL_LABELS[option]}
                  </button>
                ))}
              </div>
              {error ? <p className="login-error" role="alert">{error}</p> : null}
            </div>
          ) : null}

          {step === 'code' ? (
            <form className="space-y-4" onSubmit={submitCode}>
              <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="code">Código</label>
              <Input id="code" name="code" inputMode="numeric" maxLength={16} value={code} onChange={(event) => setCode(event.target.value)} required /></div>
              <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="new-password">Nova senha</label>
              <Input id="new-password" name="new-password" type="password" autoComplete="new-password" maxLength={128} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></div>
              <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="new-password-confirmation">Confirmar nova senha</label>
              <Input id="new-password-confirmation" name="new-password-confirmation" type="password" autoComplete="new-password" maxLength={128} value={newPasswordConfirmation} onChange={(event) => setNewPasswordConfirmation(event.target.value)} required /></div>
              {error ? <p className="login-error" role="alert">{error}</p> : null}
              <Button className="w-full" size="lg" type="submit" disabled={pending}><KeyRound />{pending ? 'Confirmando…' : 'Redefinir senha'}</Button>
            </form>
          ) : null}

          {step === 'done' ? (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground" role="status">Sua senha foi redefinida. As demais sessões foram encerradas.</p>
              <Button className="w-full" size="lg" asChild><Link to="/login">Ir para o login</Link></Button>
            </div>
          ) : null}
        </CardContent>
      </Card></motion.div>
    </div>
  )
}
