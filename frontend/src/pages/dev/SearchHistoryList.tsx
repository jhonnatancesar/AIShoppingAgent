import { useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import type { SearchHistoryResponse } from '../../api/types'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { EmptyState, ErrorState, LoadingState } from '@/components/StatePanel'

const PAGE_SIZE = 20

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR')
}

/** Lista compartilhada por "Minhas pesquisas" e "Todas as pesquisas"
 * (Frente 5, correção de escopo) -- fonte real é `SearchReceipt`
 * (`GET /product-search/history/*`), nunca missão. `showOwner` decide
 * só se o `user_id` de cada linha aparece (irrelevante em "Minhas
 * pesquisas", já que é sempre o mesmo usuário logado). */
export function SearchHistoryList({
  fetchPage,
  showOwner,
  emptyDescription,
}: {
  fetchPage: (limit: number, offset: number) => Promise<SearchHistoryResponse | null>
  showOwner: boolean
  emptyDescription: string
}) {
  const [offset, setOffset] = useState(0)
  const [result, setResult] = useState<SearchHistoryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    fetchPage(PAGE_SIZE, offset).then(
      (response) => {
        if (cancelled) return
        setResult(response)
        setError(null)
      },
      (loadError) => {
        if (cancelled) return
        setResult(null)
        setError(loadError instanceof ApiError ? loadError.message : 'Não foi possível carregar as pesquisas.')
      },
    )
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `fetchPage` é estável por chamador (closure de módulo), só `offset`/`retryToken` mudam a busca
  }, [offset, retryToken])

  if (error) {
    return <ErrorState title="Não foi possível carregar as pesquisas" description={error} onRetry={() => setRetryToken((token) => token + 1)} />
  }
  if (!result) {
    return <LoadingState label="Carregando pesquisas…" />
  }
  if (result.items.length === 0) {
    return <EmptyState title="Nenhuma pesquisa ainda" description={emptyDescription} />
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between text-sm text-muted-foreground">
        <span>{result.total} pesquisa(s)</span>
        <span>{result.offset + 1}–{Math.min(result.offset + result.items.length, result.total)} de {result.total}</span>
      </div>
      <div className="space-y-2">
        {result.items.map((item) => (
          <Card key={item.id}>
            <CardContent className="flex flex-col gap-1.5 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{item.query_text ?? '(pesquisa registrada antes do histórico de texto)'}</p>
                {showOwner ? <p className="truncate font-mono text-xs text-muted-foreground">usuário {item.user_id}</p> : null}
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">{formatDateTime(item.created_at)}</span>
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="mt-7 flex justify-center gap-2">
        <Button variant="outline" disabled={result.offset === 0} onClick={() => setOffset(Math.max(0, result.offset - result.limit))}>Anterior</Button>
        <Button variant="outline" disabled={result.offset + result.limit >= result.total} onClick={() => setOffset(result.offset + result.limit)}>Próxima</Button>
      </div>
    </div>
  )
}
