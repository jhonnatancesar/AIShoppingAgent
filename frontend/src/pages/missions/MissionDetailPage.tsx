import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { missionsApi } from '../../api/missions'
import type { MissionDetail } from '../../api/types'
import { STATUS_LABELS, STORE_LABELS } from './statusLabels'

const STORE_CODES = ['pichau', 'terabyte', 'amazon', 'kabum', 'magalu']

export function MissionDetailPage() {
  const { missionId } = useParams<{ missionId: string }>()
  const [mission, setMission] = useState<MissionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState(false)

  const load = useCallback(async () => {
    if (!missionId) return
    setError(null)
    try {
      const detail = await missionsApi.get(missionId)
      setMission(detail)
    } catch (loadError) {
      setError(
        loadError instanceof ApiError
          ? loadError.message
          : 'Não foi possível carregar a missão.',
      )
    }
  }, [missionId])

  useEffect(() => {
    load()
  }, [load])

  async function runAction(action: (id: string, version: number) => Promise<unknown>) {
    if (!mission) return
    setActionPending(true)
    setError(null)
    try {
      await action(mission.id, mission.state_version)
      await load()
    } catch (actionError) {
      if (actionError instanceof ApiError && actionError.code === 'mission_version_conflict') {
        // Optimistic locking: nunca força a alteração nem ignora o
        // conflito -- recarrega os dados atuais para que o usuário revise
        // antes de tentar de novo (a versão em tela já estava obsoleta).
        setError(
          'Esta missão foi alterada enquanto você estava nesta página. ' +
            'Atualizamos os dados abaixo para a versão mais recente -- revise antes de tentar novamente.',
        )
        await load()
        return
      }
      setError(
        actionError instanceof ApiError
          ? actionError.message
          : 'Não foi possível concluir a ação.',
      )
    } finally {
      setActionPending(false)
    }
  }

  function handleCancel() {
    if (!window.confirm('Cancelar esta missão? Essa ação não pode ser desfeita.')) {
      return
    }
    runAction(missionsApi.cancel)
  }

  if (error && !mission) {
    return (
      <section>
        <p className="form-error">{error}</p>
        <Link className="button button-secondary" to="/app/missions">
          Voltar para missões
        </Link>
      </section>
    )
  }

  if (!mission) {
    return <p className="loading">Carregando…</p>
  }

  return (
    <section>
      <div className="page-header">
        <h1>{mission.title}</h1>
        <span className={`status-badge status-${mission.status}`}>
          {STATUS_LABELS[mission.status]}
        </span>
      </div>

      {error ? <p className="form-error">{error}</p> : null}

      {mission.criteria ? (
        <div className="mission-section">
          <h2>Critério</h2>
          <p>Busca: {mission.criteria.search_query}</p>
          {mission.criteria.model ? <p>Modelo: {mission.criteria.model}</p> : null}
          <p>
            Tipo:{' '}
            {mission.criteria.request_kind === 'specific_product'
              ? 'produto específico'
              : mission.criteria.request_kind === 'product_family'
                ? 'família de produtos'
                : 'categoria genérica'}
          </p>
          {mission.criteria.target_amount ? (
            <p>
              Alvo: {mission.criteria.target_amount} {mission.criteria.target_currency}
            </p>
          ) : (
            <p>Sem preço-alvo definido.</p>
          )}
        </div>
      ) : null}

      {mission.criteria?.request_kind === 'product_family' ? (
        <VariantSelection
          key={`${mission.state_version}-${mission.criteria.variant_selection_mode}`}
          mission={mission}
          onSaved={load}
        />
      ) : null}

      {mission.criteria?.request_kind === 'generic_category' ? (
        <div className="mission-section">
          <h2>Refinamento opcional</h2>
          <p className="field-hint">
            Esta é uma missão de categoria. Ela continua ativa sem escolher um produto;
            use a edição da busca em uma evolução futura se quiser restringir os resultados.
          </p>
        </div>
      ) : null}

      <div className="mission-section">
        <h2>Lojas</h2>
        <p>
          {mission.sources.length === 0
            ? 'Nenhuma loja selecionada.'
            : mission.sources
                .map((source) => STORE_LABELS[source.store_code] ?? source.store_code)
                .join(', ')}
        </p>
      </div>

      {mission.schedule ? (
        <div className="mission-section">
          <h2>Agenda</h2>
          <p>A cada {mission.schedule.interval_minutes} minutos.</p>
        </div>
      ) : null}

      <div className="mission-actions">
        {mission.status === 'active' ? (
          <button
            className="button button-secondary"
            type="button"
            disabled={actionPending}
            onClick={() => runAction(missionsApi.pause)}
          >
            Pausar
          </button>
        ) : null}
        {mission.status === 'paused' ? (
          <button
            className="button"
            type="button"
            disabled={actionPending}
            onClick={() => runAction(missionsApi.resume)}
          >
            Retomar
          </button>
        ) : null}
        {mission.status === 'active' || mission.status === 'paused' ? (
          <button
            className="button button-danger"
            type="button"
            disabled={actionPending}
            onClick={handleCancel}
          >
            Cancelar
          </button>
        ) : null}
      </div>

      {mission.status === 'paused' ? (
        // `key` força remontar o formulário quando `state_version` muda
        // (inclusive após um recarregamento disparado por 409) -- os
        // campos locais (useState) precisam refletir os dados atuais do
        // servidor antes de uma nova tentativa, nunca os que o usuário
        // via antes do conflito.
        <EditMissionForm
          key={mission.state_version}
          mission={mission}
          onSaved={() => {
            load()
          }}
        />
      ) : null}

      <div className="mission-section">
        <h2>Histórico</h2>
        {mission.transitions.length === 0 ? (
          <p className="field-hint">Nenhuma transição registrada ainda.</p>
        ) : (
          <ul className="transition-list">
            {mission.transitions.map((transition, index) => (
              <li key={index}>
                {STATUS_LABELS[transition.from_status]} → {STATUS_LABELS[transition.to_status]}{' '}
                ({transition.transitioned_at})
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mission-section">
        <h2>Ofertas relevantes</h2>
        {mission.offers.length === 0 ? (
          <p className="field-hint">Nenhuma oferta relevante disponível ainda.</p>
        ) : (
          <ul className="offer-link-list">
            {mission.offers.map((offer) => (
              <li key={offer.id}>
                <Link to={`/app/offers/${offer.id}`}>
                  {offer.title} — {offer.store_name}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mission-actions">
        <Link className="button button-secondary" to="/app/missions">
          Voltar para missões
        </Link>
      </div>
    </section>
  )
}

function VariantSelection({
  mission,
  onSaved,
}: {
  mission: MissionDetail
  onSaved: () => void
}) {
  const [selected, setSelected] = useState<string[]>(
    mission.available_variants.filter((item) => item.selected).map((item) => item.product_id),
  )
  const [selectAll, setSelectAll] = useState(
    mission.criteria?.variant_selection_mode === 'all',
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await missionsApi.selectVariants(mission.id, {
        expected_state_version: mission.state_version,
        product_ids: selectAll ? [] : selected,
        select_all: selectAll,
      })
      onSaved()
    } catch (saveError) {
      setError(
        saveError instanceof ApiError
          ? saveError.message
          : 'Não foi possível salvar as variantes.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mission-section">
      <h2>Variantes encontradas</h2>
      {mission.available_variants.length === 0 ? (
        <p className="field-hint">Aguardando variantes identificadas com segurança.</p>
      ) : (
        <>
          <label>
            <input
              type="checkbox"
              checked={selectAll}
              onChange={(event) => setSelectAll(event.target.checked)}
            />
            Todas as variantes desta família
          </label>
          {!selectAll ? (
            <div className="checkbox-group">
              {mission.available_variants.map((variant) => (
                <label key={variant.product_id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(variant.product_id)}
                    onChange={() =>
                      setSelected((current) =>
                        current.includes(variant.product_id)
                          ? current.filter((item) => item !== variant.product_id)
                          : [...current, variant.product_id],
                      )
                    }
                  />
                  {variant.label}
                </label>
              ))}
            </div>
          ) : null}
          {error ? <p className="form-error">{error}</p> : null}
          <button
            className="button"
            type="button"
            disabled={saving || (!selectAll && selected.length === 0)}
            onClick={save}
          >
            {saving ? 'Salvando…' : 'Salvar variantes'}
          </button>
        </>
      )}
    </div>
  )
}

function EditMissionForm({
  mission,
  onSaved,
}: {
  mission: MissionDetail
  onSaved: () => void
}) {
  const [targetAmount, setTargetAmount] = useState(mission.criteria?.target_amount ?? '')
  const [targetCurrency, setTargetCurrency] = useState(
    mission.criteria?.target_currency ?? 'BRL',
  )
  const [clearTarget, setClearTarget] = useState(false)
  const [sourceCodes, setSourceCodes] = useState<string[]>(
    mission.sources.map((source) => source.store_code),
  )
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function toggleSource(code: string) {
    setSourceCodes((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code],
    )
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await missionsApi.edit(mission.id, {
        expected_state_version: mission.state_version,
        target_amount: clearTarget ? null : targetAmount.trim() || undefined,
        target_currency: clearTarget ? null : targetAmount.trim() ? targetCurrency : undefined,
        clear_target: clearTarget,
        source_codes: sourceCodes.length > 0 ? sourceCodes : undefined,
      })
      onSaved()
    } catch (submitError) {
      if (submitError instanceof ApiError && submitError.code === 'mission_version_conflict') {
        setError(
          'Esta missão foi alterada enquanto você estava nesta página. ' +
            'Atualizamos os dados para a versão mais recente -- revise antes de tentar novamente.',
        )
        onSaved() // recarrega a missão no componente pai; o formulário remonta com os dados atuais
        return
      }
      setError(
        submitError instanceof ApiError
          ? submitError.message
          : 'Não foi possível salvar as alterações.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mission-section">
      <h2>Editar critério</h2>
      <form onSubmit={handleSubmit}>
        <div className="field-row">
          <div className="field">
            <label htmlFor="edit_target_amount">Preço-alvo</label>
            <input
              id="edit_target_amount"
              inputMode="decimal"
              value={targetAmount}
              onChange={(event) => {
                setTargetAmount(event.target.value)
                setClearTarget(false)
              }}
              disabled={clearTarget}
            />
          </div>
          <div className="field">
            <label htmlFor="edit_target_currency">Moeda</label>
            <input
              id="edit_target_currency"
              value={targetCurrency}
              onChange={(event) => setTargetCurrency(event.target.value.toUpperCase())}
              maxLength={3}
              disabled={clearTarget || !targetAmount.trim()}
            />
          </div>
        </div>
        <label>
          <input
            type="checkbox"
            checked={clearTarget}
            onChange={(event) => setClearTarget(event.target.checked)}
          />
          Remover preço-alvo
        </label>

        <div className="field">
          <span>Lojas</span>
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

        {error ? <p className="form-error">{error}</p> : null}

        <div className="mission-actions">
          <button className="button" type="submit" disabled={saving}>
            {saving ? 'Salvando…' : 'Salvar alterações'}
          </button>
        </div>
      </form>
    </div>
  )
}
