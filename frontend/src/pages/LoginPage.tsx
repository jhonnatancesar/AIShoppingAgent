import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

interface LocationState {
  from?: { pathname?: string }
}

export function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (user) {
    const state = location.state as LocationState | null
    const destination = state?.from?.pathname || '/app'
    return <Navigate to={destination} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(username, password)
      const state = location.state as LocationState | null
      const destination = state?.from?.pathname || '/app'
      navigate(destination, { replace: true })
    } catch (submitError) {
      if (submitError instanceof ApiError) {
        setError(submitError.message)
      } else {
        setError('Não foi possível entrar. Tente novamente.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>AIShoppingAgent</h1>
        <label htmlFor="username">Usuário</label>
        <input
          id="username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          required
        />
        <label htmlFor="password">Senha</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        {error ? (
          <p className="login-error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Entrando…' : 'Entrar'}
        </button>
        <p className="login-hint">
          Sua senha é a mesma criada pelo link enviado no Telegram.
        </p>
      </form>
    </div>
  )
}
