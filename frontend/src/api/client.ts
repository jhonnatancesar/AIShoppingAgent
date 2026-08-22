/**
 * Cliente HTTP mínimo da API (TASK-091). Mesma origem que a SPA (o backend
 * serve o build estático), então o cookie httpOnly de sessão sempre vai
 * junto (`credentials: 'include'`) sem CORS a configurar.
 *
 * CSRF (double-submit cookie): o backend emite `aishopping_csrf`, legível
 * por JS de propósito; toda requisição mutável (POST/PUT/PATCH/DELETE)
 * ecoa esse valor no header `X-CSRF-Token`. O backend rejeita se o header
 * não bater com o cookie -- um site cross-origin não consegue ler o
 * cookie nem montar esse header.
 */

const CSRF_COOKIE_NAME = 'aishopping_csrf'
const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export interface ApiErrorBody {
  code?: string
  message?: string
  details?: unknown
}

export class ApiError extends Error {
  status: number
  code: string
  details: unknown

  constructor(status: number, code: string, message: string, details: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

function readCsrfCookie(): string | null {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${CSRF_COOKIE_NAME}=([^;]*)`),
  )
  return match ? decodeURIComponent(match[1]) : null
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T | null> {
  const method = (options.method || 'GET').toUpperCase()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  if (MUTATING_METHODS.has(method)) {
    const csrfToken = readCsrfCookie()
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken
    }
  }
  const response = await fetch(`/api/v1${path}`, {
    credentials: 'include',
    headers,
    ...options,
  })

  if (response.status === 204) {
    return null
  }

  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    const errorBody: ApiErrorBody = (payload && payload.error) || {}
    throw new ApiError(
      response.status,
      errorBody.code || 'unknown_error',
      errorBody.message || 'Ocorreu um erro inesperado.',
      errorBody.details ?? null,
    )
  }

  return payload as T
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(path: string, body?: unknown) => request<T>(path, { method: 'DELETE', body: body === undefined ? undefined : JSON.stringify(body) }),
}
