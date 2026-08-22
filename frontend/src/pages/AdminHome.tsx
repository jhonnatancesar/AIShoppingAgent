import { useAuth } from '../auth/AuthContext'
import { PageHeader } from '@/components/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

export function AdminHome() {
  const { user } = useAuth()

  return (
    <section>
      <PageHeader eyebrow="Ambiente DEV" title="Painel administrativo" description="Fundação visual pronta para receber os módulos administrativos da V1.2." actions={<Badge variant="destructive">ADMIN</Badge>} />
      <Card><CardContent className="pt-6 text-sm leading-relaxed text-muted-foreground">
        Acesso exclusivo de <strong>{user?.display_name}</strong> (papel{' '}
        <code>{user?.role}</code>). Dashboard técnico, administração de dados e
        controles operacionais chegam nos próximos itens da V1.2 (9-11).
      </CardContent></Card>
    </section>
  )
}
