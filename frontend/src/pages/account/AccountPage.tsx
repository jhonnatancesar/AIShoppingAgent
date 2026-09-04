import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Bell, Check, Clock3, KeyRound, Link2, Mail, RefreshCw, Save, ShieldCheck, Unlink, UserRound } from 'lucide-react'
import { accountApi } from '@/api/account'
import { emailVerificationApi } from '@/api/auth'
import { ApiError } from '@/api/client'
import type { AccountOption, AccountProfile, AccountQuota } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { FormMessage } from '@/components/FormMessage'
import { PageHeader } from '@/components/PageHeader'
import { QuotaSummaryCard } from '@/components/QuotaSummary'
import { ErrorState, LoadingState } from '@/components/StatePanel'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useToast } from '@/hooks/useToast'

export function AccountPage() {
  const [account, setAccount] = useState<AccountProfile | null>(null)
  const [quota, setQuota] = useState<AccountQuota | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { refresh } = useAuth()

  const load = useCallback(async () => {
    setError(null)
    try {
      const [loadedAccount, loadedQuota] = await Promise.all([
        accountApi.get(),
        accountApi.getQuota(),
      ])
      setAccount(loadedAccount)
      setQuota(loadedQuota)
    } catch (loadError) {
      setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar sua conta.')
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (error && !account) return <ErrorState title="Conta indisponível" description={error} onRetry={load} />
  if (!account) return <LoadingState label="Carregando sua conta…" />

  return (
    <AccountView
      account={account}
      quota={quota}
      onProfileSaved={async (updated) => { setAccount(updated); await refresh() }}
      onNotificationsSaved={setAccount}
      onTelegramChanged={setAccount}
    />
  )
}

export function AccountView({
  account,
  quota,
  onProfileSaved,
  onNotificationsSaved,
  onTelegramChanged,
}: {
  account: AccountProfile
  quota?: AccountQuota | null
  onProfileSaved: (account: AccountProfile) => void | Promise<void>
  onNotificationsSaved: (account: AccountProfile) => void
  onTelegramChanged: (account: AccountProfile) => void
}) {
  return (
    <section>
      <PageHeader
        eyebrow="Sua área"
        title="Minha conta"
        description="Gerencie seus dados e preferências usando a mesma conta do Telegram."
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(19rem,.6fr)]">
        <div className="space-y-6">
          <ProfileForm account={account} onSaved={onProfileSaved} />
          <NotificationForm account={account} onSaved={onNotificationsSaved} />
          <PasswordForm />
        </div>
        <div className="space-y-6">
          {quota ? <QuotaSummaryCard quota={quota} /> : null}
          <AccountSummary account={account} onChanged={onTelegramChanged} />
        </div>
      </div>
    </section>
  )
}

function ProfileForm({ account, onSaved }: { account: AccountProfile; onSaved: (account: AccountProfile) => void | Promise<void> }) {
  const { toast } = useToast()
  const [displayName, setDisplayName] = useState(account.display_name)
  const [email, setEmail] = useState(account.email || '')
  const [stores, setStores] = useState(account.favorite_stores)
  const [categories, setCategories] = useState(account.preferred_categories)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const updated = await accountApi.updateProfile({
        display_name: displayName,
        email: email.trim() || null,
        favorite_stores: stores,
        preferred_categories: categories,
      })
      if (updated) await onSaved(updated)
      toast({ title: 'Perfil atualizado.', variant: 'success' })
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível salvar o perfil.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><UserRound className="size-5 text-primary" />Perfil</CardTitle><CardDescription>Informações visíveis na sua experiência pessoal.</CardDescription></CardHeader>
      <CardContent>
        <form className="space-y-6" onSubmit={submit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Nome"><Input value={displayName} maxLength={160} required onChange={(event) => setDisplayName(event.target.value)} /></Field>
            <Field label="E-mail"><Input type="email" value={email} maxLength={254} placeholder="Opcional" onChange={(event) => setEmail(event.target.value)} /></Field>
          </div>
          <OptionGroup title="Lojas preferidas" options={account.available_stores} selected={stores} onChange={setStores} />
          <OptionGroup title="Categorias preferidas" options={account.available_categories} selected={categories} onChange={setCategories} />
          <FormMessage tone="error">{error}</FormMessage>
          <div className="flex items-center justify-end gap-3"><Button disabled={saving}><Save />{saving ? 'Salvando…' : 'Salvar perfil'}</Button></div>
        </form>
      </CardContent>
    </Card>
  )
}

function NotificationForm({ account, onSaved }: { account: AccountProfile; onSaved: (account: AccountProfile) => void }) {
  const { toast } = useToast()
  const [priceDrops, setPriceDrops] = useState(account.notify_price_decreases)
  const [targetReached, setTargetReached] = useState(account.notify_target_reached)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const updated = await accountApi.updateNotifications({ notify_price_decreases: priceDrops, notify_target_reached: targetReached })
      if (updated) onSaved(updated)
      toast({ title: 'Preferências atualizadas.', variant: 'success' })
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível salvar as preferências.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Bell className="size-5 text-primary" />Notificações</CardTitle><CardDescription>As escolhas valem para os alertas enviados pelo Telegram.</CardDescription></CardHeader>
      <CardContent><form className="space-y-4" onSubmit={submit}><Toggle label="Quedas de preço" description="Avise quando uma oferta monitorada ficar mais barata." checked={priceDrops} onChange={setPriceDrops} /><Toggle label="Preço-alvo atingido" description="Avise quando o valor definido na missão for alcançado." checked={targetReached} onChange={setTargetReached} /><FormMessage tone="error">{error}</FormMessage><div className="flex items-center justify-end gap-3 pt-2"><Button disabled={saving}><Save />{saving ? 'Salvando…' : 'Salvar notificações'}</Button></div></form></CardContent>
    </Card>
  )
}

function PasswordForm() {
  const { toast } = useToast()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPasswordConfirmation, setNewPasswordConfirmation] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    if (newPassword !== newPasswordConfirmation) {
      setError('As senhas informadas não conferem.')
      setSaving(false)
      return
    }
    try {
      await accountApi.changePassword({ current_password: currentPassword, new_password: newPassword, new_password_confirmation: newPasswordConfirmation })
      setCurrentPassword('')
      setNewPassword('')
      setNewPasswordConfirmation('')
      toast({ title: 'Senha alterada.', description: 'As demais sessões foram encerradas.', variant: 'success' })
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível alterar a senha.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><KeyRound className="size-5 text-primary" />Alterar senha</CardTitle><CardDescription>Sua senha é a mesma usada no site e no Telegram.</CardDescription></CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={submit}>
          <Field label="Senha atual"><Input type="password" autoComplete="current-password" value={currentPassword} maxLength={128} required onChange={(event) => setCurrentPassword(event.target.value)} /></Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Nova senha"><Input type="password" autoComplete="new-password" value={newPassword} maxLength={128} required onChange={(event) => setNewPassword(event.target.value)} /></Field>
            <Field label="Confirmar nova senha"><Input type="password" autoComplete="new-password" value={newPasswordConfirmation} maxLength={128} required onChange={(event) => setNewPasswordConfirmation(event.target.value)} /></Field>
          </div>
          <FormMessage tone="error">{error}</FormMessage>
          <div className="flex items-center justify-end gap-3"><Button disabled={saving}><KeyRound />{saving ? 'Salvando…' : 'Alterar senha'}</Button></div>
        </form>
      </CardContent>
    </Card>
  )
}

function AccountSummary({ account, onChanged }: { account: AccountProfile; onChanged: (account: AccountProfile) => void }) {
  const { toast } = useToast()
  const [command, setCommand] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function startLink() {
    setBusy(true); setError(null)
    try {
      const challenge = await accountApi.startTelegramLink()
      if (challenge) { setCommand(challenge.command); onChanged(challenge.account); toast({ title: 'Código gerado.', variant: 'success' }) }
    } catch (startError) {
      setError(startError instanceof ApiError ? startError.message : 'Não foi possível gerar o código.')
    } finally { setBusy(false) }
  }

  async function refreshLink() {
    setBusy(true); setError(null)
    try { const updated = await accountApi.get(); if (updated) onChanged(updated) }
    catch (refreshError) { setError(refreshError instanceof ApiError ? refreshError.message : 'Não foi possível atualizar o vínculo.') }
    finally { setBusy(false) }
  }

  async function unlinkTelegram() {
    setBusy(true); setError(null)
    try {
      const updated = await accountApi.unlinkTelegram()
      if (updated) { setCommand(null); onChanged(updated); toast({ title: 'Telegram desvinculado.', variant: 'success' }) }
    } catch (unlinkError) {
      setError(unlinkError instanceof ApiError ? unlinkError.message : 'Não foi possível desvincular.')
    } finally { setBusy(false) }
  }

  const statusLabel = account.telegram_link_status === 'linked' ? 'Vinculado' : account.telegram_link_status === 'pending' ? 'Vinculação pendente' : 'Não vinculado'
  return <Card className="h-fit xl:sticky xl:top-24"><CardHeader><CardTitle>Conta</CardTitle><CardDescription>Identidade e integrações protegidas.</CardDescription></CardHeader><CardContent className="space-y-4"><Summary icon={UserRound} label="Usuário" value={account.username ? `@${account.username}` : 'Não definido'} /><Summary icon={ShieldCheck} label="Perfil de acesso" value={account.role} />{account.email ? <EmailVerificationStatus account={account} onChanged={onChanged} /> : null}<Summary icon={Link2} label="Telegram" value={statusLabel} badge={account.telegram_link_status === 'linked'} />{account.telegram_link_status === 'pending' ? <div className="rounded-lg border border-warning/30 bg-warning/8 p-3 text-xs text-muted-foreground"><p className="flex items-center gap-2 font-medium text-warning"><Clock3 className="size-4" />Vinculação pendente</p><p className="mt-1">Envie o comando temporário no chat privado do bot. Ele expira em até 10 minutos e funciona uma única vez.</p></div> : null}{command ? <div className="space-y-2 rounded-lg border border-primary/25 bg-primary/7 p-3"><p className="text-xs text-muted-foreground">No chat privado do bot, envie exatamente:</p><code className="block break-all rounded bg-background p-2 text-xs text-foreground">{command}</code></div> : null}<div className="grid gap-2">{account.telegram_link_status === 'linked' ? (
    <AlertDialog>
      <AlertDialogTrigger asChild><Button type="button" variant="outline" disabled={busy}><Unlink />Desvincular Telegram</Button></AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Desvincular o Telegram?</AlertDialogTitle>
          <AlertDialogDescription>Suas missões e dados Web serão preservados. Você pode vincular novamente quando quiser.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Voltar</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={unlinkTelegram}>Desvincular</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  ) : <Button type="button" variant="outline" disabled={busy} onClick={startLink}><Link2 />{account.telegram_link_status === 'pending' ? 'Gerar novo código' : 'Vincular Telegram'}</Button>}<Button type="button" variant="ghost" disabled={busy} onClick={refreshLink}><RefreshCw />Atualizar status</Button></div><FormMessage tone="error">{error}</FormMessage><div className="rounded-lg border border-border bg-muted/35 p-3 text-xs text-muted-foreground">Conta criada em {new Date(account.created_at).toLocaleDateString('pt-BR')}. O Telegram é opcional; desvincular não remove sua conta, missões ou acesso Web.</div></CardContent></Card>
}

function EmailVerificationStatus({ account, onChanged }: { account: AccountProfile; onChanged: (account: AccountProfile) => void }) {
  const { toast } = useToast()
  const [challengeId, setChallengeId] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const verified = Boolean(account.email_verified_at)

  async function requestVerification() {
    setBusy(true); setError(null)
    try {
      const issued = await emailVerificationApi.request()
      if (issued) { setChallengeId(issued.challenge_id); toast({ title: 'Código de verificação enviado.', variant: 'success' }) }
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : 'Não foi possível enviar o código.')
    } finally { setBusy(false) }
  }

  async function confirmVerification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!challengeId) return
    setBusy(true); setError(null)
    try {
      await emailVerificationApi.confirm(challengeId, code)
      const updated = await accountApi.get()
      if (updated) onChanged(updated)
      setChallengeId(null)
      setCode('')
      toast({ title: 'E-mail verificado.', variant: 'success' })
    } catch (confirmError) {
      setError(confirmError instanceof ApiError ? confirmError.message : 'Código inválido ou expirado.')
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-2">
      <Summary icon={Mail} label="E-mail" value={account.email || ''} badge={verified} />
      {!verified && account.email_verification_available && !challengeId ? (
        <Button type="button" variant="outline" size="sm" disabled={busy} onClick={requestVerification}>Verificar e-mail</Button>
      ) : null}
      {!verified && !account.email_verification_available ? (
        <p className="text-xs text-muted-foreground">Verificação por e-mail ainda não está disponível.</p>
      ) : null}
      {challengeId ? (
        <form className="flex items-center gap-2" onSubmit={confirmVerification}>
          <Input value={code} maxLength={16} placeholder="Código" onChange={(event) => setCode(event.target.value)} />
          <Button type="submit" size="sm" disabled={busy}>Confirmar</Button>
        </form>
      ) : null}
      <FormMessage tone="error">{error}</FormMessage>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="space-y-2 text-sm font-medium"><span>{label}</span>{children}</label> }

function OptionGroup({ title, options, selected, onChange }: { title: string; options: AccountOption[]; selected: string[]; onChange: (value: string[]) => void }) {
  function toggle(code: string) { onChange(selected.includes(code) ? selected.filter((item) => item !== code) : [...selected, code]) }
  return <fieldset><legend className="mb-2 text-sm font-medium">{title}</legend><div className="flex flex-wrap gap-2">{options.map((option) => <button type="button" key={option.code} aria-pressed={selected.includes(option.code)} onClick={() => toggle(option.code)} className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm transition-colors aria-pressed:border-primary aria-pressed:bg-primary/10 aria-pressed:text-primary">{selected.includes(option.code) ? <Check className="size-3.5" /> : null}{option.label}</button>)}</div></fieldset>
}

function Toggle({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-border p-4"><span><span className="block text-sm font-medium">{label}</span><span className="mt-1 block text-xs text-muted-foreground">{description}</span></span><input className="size-4 accent-primary" type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>
}

function Summary({ icon: Icon, label, value, badge }: { icon: typeof UserRound; label: string; value: string; badge?: boolean }) {
  return <div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><Icon className="size-4" /></div><div className="min-w-0 flex-1"><p className="text-xs text-muted-foreground">{label}</p><p className="truncate text-sm font-medium">{value}</p></div>{badge ? <Badge>Ativo</Badge> : null}</div>
}
