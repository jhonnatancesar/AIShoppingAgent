import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { missionsApi } from '../../api/missions'
import { StoreSelectionField, TargetPriceFields, toApiDecimal } from './MissionFormFields'
import type { QuotaErrorDetails } from '@/api/types'
import { FormMessage } from '@/components/FormMessage'
import { PageHeader } from '@/components/PageHeader'
import { QuotaExceededNotice, quotaDetailsFromError } from '@/components/QuotaExceededNotice'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

export function MissionCreatePage() {
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [model, setModel] = useState('')
  const [targetAmount, setTargetAmount] = useState('')
  const [targetCurrency, setTargetCurrency] = useState('BRL')
  const [sourceCodes, setSourceCodes] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [quotaError, setQuotaError] = useState<QuotaErrorDetails | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setQuotaError(null)
    setSubmitting(true)
    try {
      const mission = await missionsApi.create({
        search_query: searchQuery,
        model: model.trim() || null,
        target_amount: targetAmount.trim() ? toApiDecimal(targetAmount) : null,
        target_currency: targetAmount.trim() ? targetCurrency : null,
        source_codes: sourceCodes,
      })
      if (!mission) {
        throw new Error('Resposta inesperada do servidor.')
      }
      navigate(`/app/missions/${mission.id}`, { replace: true })
    } catch (submitError) {
      if (submitError instanceof ApiError) {
        const details = quotaDetailsFromError(submitError)
        if (details) {
          // TASK-107: nunca mostra "erro genérico" quando a cota bloqueia
          // a criação -- o motivo e as ações vêm do próprio backend.
          setQuotaError(details)
          setError(submitError.message)
        } else {
          setError(submitError.message)
        }
      } else {
        setError('Não foi possível criar a missão. Tente novamente.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section>
      <PageHeader eyebrow="Monitoramento" title="Nova missão" description="Diga o que você quer que o GG Oferta acompanhe -- o produto, quanto vale a pena pagar e onde procurar." />
      <Card className="max-w-3xl"><CardContent className="space-y-5 pt-6"><form className="space-y-5" onSubmit={handleSubmit}>
        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="search_query">O que você está procurando?</label>
          <Input
            id="search_query"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="ex.: RTX 5070 Ti"
            required
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="model">Modelo/variante (opcional)</label>
          <Input
            id="model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="ex.: 9950X3D"
          />
        </div>

        <TargetPriceFields
          targetAmount={targetAmount}
          onTargetAmountChange={setTargetAmount}
          targetCurrency={targetCurrency}
          onTargetCurrencyChange={setTargetCurrency}
        />

        <StoreSelectionField
          selected={sourceCodes}
          onChange={setSourceCodes}
          hint="Vazio = busca em todas as 6 lojas da V1."
        />

        {quotaError ? (
          <QuotaExceededNotice message={error ?? ''} details={quotaError} />
        ) : (
          <FormMessage tone="error">{error}</FormMessage>
        )}

        <Button type="submit" disabled={submitting}>
          {submitting ? 'Criando…' : 'Criar missão'}
        </Button>
      </form></CardContent></Card>
    </section>
  )
}
