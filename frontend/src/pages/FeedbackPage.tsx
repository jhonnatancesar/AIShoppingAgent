import { useState, type FormEvent, type ReactNode } from 'react'
import { LifeBuoy, Send, Store } from 'lucide-react'
import { feedbackApi, type FeedbackKind } from '@/api/feedback'
import { ApiError } from '@/api/client'
import { FormMessage } from '@/components/FormMessage'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup } from '@/components/ui/toggle-group'
import { useToast } from '@/hooks/useToast'

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

const CATEGORY_OPTIONS: { value: FeedbackKind; label: string }[] = [
  { value: 'bug', label: 'Erro/Bug' },
  { value: 'support', label: 'Outro suporte' },
]

function SupportForm() {
  const { toast } = useToast()
  const [category, setCategory] = useState<FeedbackKind>('bug')
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSending(true)
    setError(null)
    try {
      await feedbackApi.submit({ kind: category, message })
      setMessage('')
      toast({ title: 'Recebemos sua mensagem.', description: 'Obrigado por avisar!', variant: 'success' })
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível enviar.')
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
            <ToggleGroup type="single" aria-label="Categoria" options={CATEGORY_OPTIONS} value={category} onChange={setCategory} />
          </Field>
          <Field label="Descrição">
            <Textarea value={message} required maxLength={4000} rows={4} placeholder="Descreva o que aconteceu…" onChange={(event) => setMessage(event.target.value)} />
          </Field>
          <div className="flex items-center justify-between gap-3">
            <FormMessage tone="error">{error}</FormMessage>
            <Button disabled={sending || !message.trim()}><Send />{sending ? 'Enviando…' : 'Enviar'}</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function StoreSuggestionForm() {
  const { toast } = useToast()
  const [storeName, setStoreName] = useState('')
  const [storeUrl, setStoreUrl] = useState('')
  const [comment, setComment] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSending(true)
    setError(null)
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
      toast({ title: 'Sugestão registrada.', description: 'Obrigado!', variant: 'success' })
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : 'Não foi possível enviar.')
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
            <Textarea value={comment} maxLength={4000} rows={4} placeholder="Algo mais que queira contar?" onChange={(event) => setComment(event.target.value)} />
          </Field>
          <div className="flex items-center justify-between gap-3">
            <FormMessage tone="error">{error}</FormMessage>
            <Button disabled={sending || !storeName.trim()}><Send />{sending ? 'Enviando…' : 'Enviar'}</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="space-y-2 text-sm font-medium"><span>{label}</span>{children}</label>
}
