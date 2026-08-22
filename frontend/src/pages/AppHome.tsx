import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export function AppHome() {
  const { user } = useAuth()

  return (
    <section>
      <h1>Olá, {user?.display_name}</h1>
      <p>
        Acompanhe suas missões de monitoramento de preço pela web -- as
        mesmas que você já controla pelo Telegram.
      </p>
      <p>
        <Link className="button" to="/app/missions">
          Ver minhas missões
        </Link>
      </p>
    </section>
  )
}
