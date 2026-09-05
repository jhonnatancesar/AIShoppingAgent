import { useEffect, useState, type FormEvent } from 'react'
import { Plus, Settings2, Trash2 } from 'lucide-react'
import { adminApi, type AdminMission, type AdminUser, type Lifecycle } from '@/api/admin'
import { ApiError } from '@/api/client'
import { FormMessage } from '@/components/FormMessage'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/toastContext'
import { AdminStatusBadge, ConfirmActionButton } from './adminComponents'

const ROLES = ['USER', 'DEV', 'ADMIN'] as const

export function AdminUsersPage() {
  const [query, setQuery] = useState('')
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Só para "Pesquisar"/ações que precisam recarregar pedirem os mesmos
  // dados de novo, sem chamar a busca por referência de dentro do efeito.
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    adminApi.users(query).then(
      (response) => {
        if (cancelled) return
        setUsers(response?.items ?? [])
        setError(null)
      },
      (loadError) => {
        if (cancelled) return
        setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar os usuários.')
      },
    )
    return () => { cancelled = true }
  }, [query, reloadToken])

  return <AdminUsersView query={query} onQueryChange={setQuery} users={users} error={error} onReload={() => setReloadToken((token) => token + 1)} />
}

export function AdminUsersView({
  query,
  onQueryChange,
  users,
  error,
  onReload,
}: {
  query: string
  onQueryChange: (value: string) => void
  users: AdminUser[] | null
  error: string | null
  onReload: () => void
}) {
  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onReload()
  }

  return (
    <section>
      <PageHeader eyebrow="Administração" title="Usuários" description="Gerencie acessos, limites e missões de cada conta." actions={<CreateUserDialog onCreated={onReload} />} />

      <form className="mb-2 flex flex-col gap-2 sm:flex-row" onSubmit={submitSearch}>
        <Input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Pesquisar nome, usuário ou e-mail" />
        <Button type="submit">Pesquisar</Button>
      </form>
      <p className="mb-5 text-xs text-muted-foreground">Mostramos até 200 contas por busca. Se alguém não aparecer, tente um termo mais específico.</p>

      {error && !users ? (
        <ErrorState title="Não foi possível carregar os usuários" description={error} onRetry={onReload} />
      ) : !users ? (
        <LoadingState label="Carregando usuários…" />
      ) : users.length === 0 ? (
        <EmptyState title="Nenhum usuário encontrado" description="Tente outro termo de busca." />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-card [&>*+*]:border-t [&>*+*]:border-border">
          {users.map((user) => <UserRow key={user.id} user={user} onChanged={onReload} />)}
        </div>
      )}
    </section>
  )
}

function CreateUserDialog({ onCreated }: { onCreated: () => void }) {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<string>('USER')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function resetAndClose() {
    setOpen(false)
    setDisplayName(''); setUsername(''); setEmail(''); setPassword(''); setRole('USER'); setError(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await adminApi.createUser({ display_name: displayName, username, email: email.trim() || null, password, role })
      toast({ title: 'Usuário criado.', variant: 'success' })
      onCreated()
      resetAndClose()
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível criar o usuário.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button><Plus />Adicionar</Button></DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>Criar usuário</DialogTitle></DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="new_display_name">Nome</label><Input id="new_display_name" value={displayName} required onChange={(event) => setDisplayName(event.target.value)} /></div>
          <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="new_username">Usuário</label><Input id="new_username" value={username} required onChange={(event) => setUsername(event.target.value)} /></div>
          <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="new_email">E-mail (opcional)</label><Input id="new_email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></div>
          <div className="space-y-1.5"><label className="text-sm font-medium" htmlFor="new_password">Senha temporária</label><Input id="new_password" type="password" value={password} required onChange={(event) => setPassword(event.target.value)} /></div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Papel</label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>{ROLES.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <FormMessage tone="error">{error}</FormMessage>
          <DialogFooter><Button type="submit" disabled={saving}>{saving ? 'Criando…' : 'Criar usuário'}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function UserRow({ user, onChanged }: { user: AdminUser; onChanged: () => void }) {
  const { toast } = useToast()
  const [missions, setMissions] = useState<AdminMission[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function changeRole(role: string) {
    try {
      await adminApi.updateUser(user.id, { role, reason: 'Alteração confirmada no painel' })
      toast({ title: 'Papel atualizado.', variant: 'success' })
      onChanged()
    } catch (roleError) {
      setError(roleError instanceof ApiError ? roleError.message : 'Não foi possível trocar o papel.')
    }
  }

  async function changeLifecycle(status: Lifecycle) {
    try {
      await adminApi.updateUser(user.id, { lifecycle_status: status, reason: 'Ação confirmada no painel' })
      toast({ title: 'Status atualizado.', variant: 'success' })
      onChanged()
    } catch (lifecycleError) {
      setError(lifecycleError instanceof ApiError ? lifecycleError.message : 'Não foi possível alterar o status.')
    }
  }

  async function loadMissions() {
    try {
      setMissions(await adminApi.missions(user.id) ?? [])
    } catch (missionsError) {
      setError(missionsError instanceof ApiError ? missionsError.message : 'Não foi possível carregar as missões.')
    }
  }

  return (
    <div className="bg-card transition-colors hover:bg-muted/25">
      <div className="space-y-3 p-5 sm:p-6">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-48 flex-1">
            <p className="font-semibold tracking-[-0.015em]">{user.display_name}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">@{user.username || 'removido'} · {user.mission_count === 1 ? '1 missão' : `${user.mission_count} missões`}</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">Cota: {quotaLabel(user.max_active_missions_override)} missões · {quotaLabel(user.max_store_slots_override)} lojas · {quotaLabel(user.max_daily_searches_override)} pesquisas/dia</p>
          </div>
          <AdminStatusBadge value={user.lifecycle_status} />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <RoleSelect role={user.role} onChange={changeRole} />
          <QuotaDialog user={user} onSaved={onChanged} />
          <Button size="sm" variant="outline" onClick={loadMissions}><Settings2 />Missões</Button>
          {user.lifecycle_status === 'active' ? (
            <>
              <ConfirmActionButton
                triggerLabel="Desativar" title={`Desativar ${user.display_name}?`}
                description="A conta fica inativa; sessões são revogadas e missões paradas." onConfirm={() => changeLifecycle('inactive')}
              />
              <ConfirmActionButton
                triggerLabel="Bloquear" destructive title={`Bloquear ${user.display_name}?`}
                description="A conta é bloqueada; sessões são revogadas e missões paradas." onConfirm={() => changeLifecycle('blocked')}
              />
            </>
          ) : (
            <ConfirmActionButton
              triggerLabel="Ativar" title={`Ativar ${user.display_name}?`}
              description="A conta volta a ficar ativa." onConfirm={() => changeLifecycle('active')}
            />
          )}
          <DeleteUserButton user={user} onDeleted={onChanged} />
        </div>

        <FormMessage tone="error">{error}</FormMessage>

        {missions ? (
          <div className="space-y-2 border-t border-border pt-3">
            {missions.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nenhuma missão.</p>
            ) : (
              missions.map((mission) => <AdminMissionRow key={mission.id} mission={mission} userId={user.id} onChanged={(next) => setMissions(next)} />)
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function RoleSelect({ role, onChange }: { role: string; onChange: (role: string) => void }) {
  const [pendingRole, setPendingRole] = useState<string | null>(null)
  return (
    <>
      <Select value={role} onValueChange={(value) => setPendingRole(value)}>
        <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
        <SelectContent>{ROLES.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent>
      </Select>
      {pendingRole && pendingRole !== role ? (
        <RoleChangeConfirm role={pendingRole} onConfirm={() => { onChange(pendingRole); setPendingRole(null) }} onCancel={() => setPendingRole(null)} />
      ) : null}
    </>
  )
}

function RoleChangeConfirm({ role, onConfirm, onCancel }: { role: string; onConfirm: () => void; onCancel: () => void }) {
  const [open, setOpen] = useState(true)
  return (
    <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (!next) onCancel() }}>
      <DialogContent>
        <DialogHeader><DialogTitle>Alterar papel para {role}?</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground">O acesso da conta muda imediatamente.</p>
        <DialogFooter>
          <Button variant="outline" onClick={() => { setOpen(false); onCancel() }}>Voltar</Button>
          <Button onClick={() => { setOpen(false); onConfirm() }}>Confirmar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function QuotaDialog({ user, onSaved }: { user: AdminUser; onSaved: () => void }) {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [activeMissions, setActiveMissions] = useState(quotaFieldState(user.max_active_missions_override))
  const [storeSlots, setStoreSlots] = useState(quotaFieldState(user.max_store_slots_override))
  const [dailySearches, setDailySearches] = useState(quotaFieldState(user.max_daily_searches_override))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    const active = resolveQuotaField(activeMissions, 'Missões ativas')
    const stores = resolveQuotaField(storeSlots, 'Lojas monitoradas')
    const searches = resolveQuotaField(dailySearches, 'Pesquisas por dia')
    if (active === 'invalid' || stores === 'invalid' || searches === 'invalid') {
      setError('Informe um número inteiro maior que zero, ou marque "usar padrão".')
      return
    }
    setSaving(true)
    try {
      await adminApi.updateUser(user.id, {
        max_active_missions_override: active,
        max_store_slots_override: stores,
        max_daily_searches_override: searches,
        reason: 'Cota ajustada no painel',
      })
      toast({ title: 'Cotas atualizadas.', variant: 'success' })
      onSaved()
      setOpen(false)
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível salvar as cotas.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button size="sm" variant="outline"><Settings2 />Cotas</Button></DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>Cotas de {user.display_name}</DialogTitle></DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <QuotaField label="Missões ativas" state={activeMissions} onChange={setActiveMissions} />
          <QuotaField label="Lojas monitoradas" state={storeSlots} onChange={setStoreSlots} />
          <QuotaField label="Pesquisas por dia" state={dailySearches} onChange={setDailySearches} />
          <FormMessage tone="error">{error}</FormMessage>
          <DialogFooter><Button type="submit" disabled={saving}>{saving ? 'Salvando…' : 'Salvar cotas'}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

interface QuotaFieldState { useDefault: boolean; value: string }
function quotaFieldState(current?: number | null): QuotaFieldState {
  return current == null ? { useDefault: true, value: '' } : { useDefault: false, value: String(current) }
}
function resolveQuotaField(state: QuotaFieldState, _label: string): number | null | 'invalid' {
  if (state.useDefault) return null
  const parsed = Number(state.value)
  if (!Number.isInteger(parsed) || parsed <= 0) return 'invalid'
  return parsed
}

function QuotaField({ label, state, onChange }: { label: string; state: QuotaFieldState; onChange: (state: QuotaFieldState) => void }) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium">{label}</label>
      <div className="flex items-center gap-2">
        <Input
          inputMode="numeric"
          value={state.value}
          disabled={state.useDefault}
          onChange={(event) => onChange({ ...state, value: event.target.value })}
        />
        <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            className="size-4 rounded border-input"
            checked={state.useDefault}
            onChange={(event) => onChange({ ...state, useDefault: event.target.checked })}
          />
          Usar padrão
        </label>
      </div>
    </div>
  )
}

function quotaLabel(value?: number | null) {
  return value == null ? 'padrão' : String(value)
}

function DeleteUserButton({ user, onDeleted }: { user: AdminUser; onDeleted: () => void }) {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const expected = user.username || user.id

  async function submit() {
    setDeleting(true)
    setError(null)
    try {
      await adminApi.deleteUser(user.id, { confirmation, reason: 'Remoção confirmada no painel' })
      toast({ title: 'Conta removida.', variant: 'success' })
      onDeleted()
      setOpen(false)
      setConfirmation('')
    } catch (deleteError) {
      setError(deleteError instanceof ApiError ? deleteError.message : 'Não foi possível remover a conta.')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (!next) { setConfirmation(''); setError(null) } }}>
      <DialogTrigger asChild><Button size="sm" variant="destructive"><Trash2 />Remover</Button></DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>Remover a conta de {user.display_name}?</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground">Esta ação não pode ser desfeita. Digite <span className="font-medium text-foreground">{expected}</span> para confirmar.</p>
        <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={expected} />
        <FormMessage tone="error">{error}</FormMessage>
        <DialogFooter>
          <Button variant="destructive" disabled={deleting || confirmation !== expected} onClick={submit}>{deleting ? 'Removendo…' : 'Remover definitivamente'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AdminMissionRow({ mission, userId, onChanged }: { mission: AdminMission; userId: string; onChanged: (missions: AdminMission[]) => void }) {
  const { toast } = useToast()

  async function runCommand(command: 'pause' | 'resume' | 'cancel') {
    try {
      await adminApi.missionCommand(mission.id, { command, expected_state_version: mission.state_version, reason: 'Ação confirmada no painel' })
      toast({ title: 'Comando aplicado.', variant: 'success' })
      onChanged(await adminApi.missions(userId) ?? [])
    } catch (commandError) {
      toast({ title: 'Não foi possível aplicar o comando.', description: commandError instanceof ApiError ? commandError.message : undefined, variant: 'destructive' })
    }
  }

  async function runCollect() {
    try {
      await adminApi.trigger({ mission_id: mission.id, confirmation: true, reason: 'Ação confirmada no painel' })
      toast({ title: 'Coleta disparada.', variant: 'success' })
    } catch (collectError) {
      toast({ title: 'Não foi possível disparar a coleta.', description: collectError instanceof ApiError ? collectError.message : undefined, variant: 'destructive' })
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <span>{mission.title} · {mission.status}</span>
      <div className="flex flex-wrap gap-1">
        {mission.status === 'active' ? <Button size="sm" variant="ghost" onClick={() => runCommand('pause')}>Pausar</Button> : null}
        {mission.status === 'paused' ? <Button size="sm" variant="ghost" onClick={() => runCommand('resume')}>Retomar</Button> : null}
        {mission.status === 'active' ? (
          <ConfirmActionButton
            triggerLabel="Coletar" variant="ghost" title={`Disparar coleta para ${mission.title}?`}
            description="Uma coleta controlada é disparada agora, fora do agendamento normal." onConfirm={runCollect}
          />
        ) : null}
        {!['cancelled', 'completed', 'expired'].includes(mission.status) ? (
          <ConfirmActionButton
            triggerLabel="Cancelar" variant="ghost" destructive title={`Cancelar ${mission.title}?`}
            description="Esta ação não pode ser desfeita." onConfirm={() => runCommand('cancel')}
          />
        ) : null}
      </div>
    </div>
  )
}
