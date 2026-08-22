import { useAuth } from '../auth/AuthContext'

export function AppHome() {
  const { user } = useAuth()

  return (
    <section>
      <h1>Olá, {user?.display_name}</h1>
      <p>
        Esta é a fundação da aplicação web (TASK-091, item 1 da V1.2).
        Gerenciamento de missões, ofertas, histórico e comparação entre
        lojas chegam nos próximos itens da V1.2.
      </p>
    </section>
  )
}
