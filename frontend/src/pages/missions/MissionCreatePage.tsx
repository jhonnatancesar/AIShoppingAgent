import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { missionsApi } from '../../api/missions'
import { STORE_LABELS } from './statusLabels'
import type { QuotaErrorDetails } from '@/api/types'
import { PageHeader } from '@/components/PageHeader'
import { QuotaExceededNotice, quotaDetailsFromError } from '@/components/QuotaExceededNotice'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

const STORE_CODES = ['pichau', 'terabyte', 'amazon', 'kabum', 'magalu', 'mercadolivre']

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

  function toggleSource(code: string) {
    setSourceCodes((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code],
    )
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setQuotaError(null)
    setSubmitting(true)
    try {
      const mission = await missionsApi.create({
        search_query: searchQuery,
        model: model.trim() || null,
        target_amount: targetAmount.trim() || null,
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
      <PageHeader eyebrow="Monitoramento" title="Nova missão" description="Descreva o produto e escolha onde o agente deve procurar." />
      <Card className="max-w-3xl"><CardContent className="pt-6"><form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="search_query">O que você está procurando?</label>
          <input
            id="search_query"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="ex.: RTX 5070 Ti"
            required
          />
        </div>

        <div className="field">
          <label htmlFor="model">Modelo/variante (opcional)</label>
          <input
            id="model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="ex.: 9950X3D"
          />
        </div>

        <div className="field-row">
          <div className="field">
            <label htmlFor="target_amount">Preço-alvo (opcional)</label>
            <input
              id="target_amount"
              inputMode="decimal"
              value={targetAmount}
              onChange={(event) => setTargetAmount(event.target.value)}
              placeholder="ex.: 4500.00"
            />
          </div>
          <div className="field">
            <label htmlFor="target_currency">Moeda</label>
            <input
              id="target_currency"
              value={targetCurrency}
              onChange={(event) => setTargetCurrency(event.target.value.toUpperCase())}
              maxLength={3}
              disabled={!targetAmount.trim()}
            />
          </div>
        </div>

        <div className="field">
          <span>Lojas (vazio = todas as 4 da V1)</span>
          <div className="checkbox-group">
            {STORE_CODES.map((code) => (
              <label key={code}>
                <input
                  type="checkbox"
                  checked={sourceCodes.includes(code)}
                  onChange={() => toggleSource(code)}
                />
                {STORE_LABELS[code]}
              </label>
            ))}
          </div>
        </div>

        {quotaError ? (
          <QuotaExceededNotice message={error ?? ''} details={quotaError} />
        ) : error ? (
          <p className="form-error">{error}</p>
        ) : null}

        <div className="mission-actions">
          <Button type="submit" disabled={submitting}>
            {submitting ? 'Criando…' : 'Criar missão'}
          </Button>
        </div>
      </form></CardContent></Card>
    </section>
  )
}
