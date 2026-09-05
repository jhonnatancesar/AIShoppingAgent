import type { ApiError } from '@/api/client'
import type { QuotaErrorDetails } from '@/api/types'

function isQuotaErrorDetails(details: unknown): details is QuotaErrorDetails {
  return (
    !!details &&
    typeof details === 'object' &&
    'kind' in details &&
    'actions' in details &&
    Array.isArray((details as QuotaErrorDetails).actions)
  )
}

/** Extrai o detalhe estruturado de cota de um `ApiError`, se houver. */
export function quotaDetailsFromError(error: ApiError): QuotaErrorDetails | null {
  return isQuotaErrorDetails(error.details) ? error.details : null
}
