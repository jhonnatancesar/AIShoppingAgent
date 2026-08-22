import { Link } from 'react-router-dom'

export function NotFound() {
  return (
    <section>
      <h1>Página não encontrada</h1>
      <p>
        <Link to="/app">Voltar para o início</Link>
      </p>
    </section>
  )
}
