import { useAuth } from '../auth/AuthContext'

export function AdminHome() {
  const { user } = useAuth()

  return (
    <section>
      <h1>Painel DEV/ADMIN</h1>
      <p>
        Acesso exclusivo de <strong>{user?.display_name}</strong> (papel{' '}
        <code>{user?.role}</code>). Dashboard técnico, administração de dados e
        controles operacionais chegam nos próximos itens da V1.2 (9-11).
      </p>
    </section>
  )
}
