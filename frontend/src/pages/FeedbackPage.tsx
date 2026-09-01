import { useState, type ComponentProps, type FormEvent, type ReactNode } from 'react'
import { LifeBuoy, Send, Store } from 'lucide-react'
import { feedbackApi, type FeedbackKind } from '@/api/feedback'
import { ApiError } from '@/api/client'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export function FeedbackPage() {
  return (
    <section>
      <PageHeader
        eyebrow="Ajuda"
        title="Suporte e sugestões"
        description="Relate um problema ou sugira uma loja para pesquisarmos."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <SupportForm />
        <StoreSuggestionForm />
      </div>
    </section>
  )
}

function SupportForm() {
  const [category, setCategory] = useState<FeedbackKind>('bug')
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSending(true)
    setFeedback(null)
    try {
      await feedbackApi.submit({ kind: category, message })
      setMessage('')
      setFeedback('Recebemos sua mensagem. Obrigado por avisar!')
    } catch (error) {
      setFeedback(error instanceof ApiError ? error.message : 'Não foi possível enviar.')
    } finally {
      setSending(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><LifeBuoy className="size-5 text-primary" />Suporte</CardTitle>
        <CardDescription>Relate um erro ou peça ajuda com algo que não está funcionando.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={submit}>
          <Field label="Categoria">
            <div className="flex gap-2">
              <CategoryOption label="Erro/Bug" active={category === 'bug'} onClick={() => setCategory('bug')} />
              <CategoryOption label="Outro suporte" active={category === 'support'} onClick={() => setCategory('support')} />
            </div>
          </Field>
          <Field label="Descrição">
            <Textarea value={message} required maxLength={4000} placeholder="Descreva o que aconteceu…" onChange={(event) => setMessage(event.target.value)} />
          </Field>
          <div className="flex items-center justify-between gap-3">
            <Feedback message={feedback} />
            <Button disabled={sending || !message.trim()}><Send />{sending ? 'Enviando…' : 'Enviar'}</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function StoreSuggestionForm() {
  const [storeName, setStoreName] = useState('')
  const [storeUrl, setStoreUrl] = useState('')
  const [comment, setComment] = useState('')
  const [sending, setSending] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSending(true)
    setFeedback(null)
    try {
      await feedbackApi.submit({
        kind: 'store_suggestion',
        store_name: storeName,
        store_url: storeUrl.trim() || null,
        message: comment.trim() || null,
      })
      setStoreName('')
      setStoreUrl('')
      setComment('')
      setFeedback('Sugestão registrada. Obrigado!')
    } catch (error) {
      setFeedback(error instanceof ApiError ? error.message : 'Não foi possível enviar.')
    } finally {
      setSending(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Store className="size-5 text-primary" />Sugerir loja</CardTitle>
        <CardDescription>Ainda não pesquisamos nessa loja? Nos avise.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={submit}>
          <Field label="Nome da loja"><Input value={storeName} required maxLength={160} onChange={(event) => setStoreName(event.target.value)} /></Field>
          <Field label="Link (opcional)"><Input type="url" value={storeUrl} maxLength={2048} placeholder="https://…" onChange={(event) => setStoreUrl(event.target.value)} /></Field>
          <Field label="Comentário (opcional)">
            <Textarea value={comment} maxLength={4000} placeholder="Algo mais que queira contar?" onChange={(event) => setComment(event.target.value)} />
          </Field>
          <div className="flex items-center justify-between gap-3">
            <Feedback message={feedback} />
            <Button disabled={sending || !storeName.trim()}><Send />{sending ? 'Enviando…' : 'Enviar'}</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function CategoryOption({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'rounded-full border border-border px-3 py-1.5 text-sm transition-colors',
        active && 'border-primary bg-primary/10 text-primary',
      )}
    >
      {label}
    </button>
  )
}

function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  return (
    <textarea
      rows={4}
      className={cn(
        'flex w-full rounded-lg border border-input bg-background/70 px-3 py-2 text-sm shadow-xs outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="space-y-2 text-sm font-medium"><span>{label}</span>{children}</label>
}

function Feedback({ message }: { message: string | null }) {
  return <span className="text-sm text-muted-foreground" role="status">{message}</span>
}
