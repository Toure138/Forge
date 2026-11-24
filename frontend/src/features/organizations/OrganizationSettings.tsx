import { type FormEvent, useId, useState } from 'react'
import { type ActiveOrganization, leaveOrganization, renameOrganization } from '../../lib/api'
import { errorMessage } from '../../lib/errors'
import { ROLE_LABELS } from '../../lib/roles'
import { ORGANIZATION_NAME_MAX } from './CreateOrganizationForm'

interface Props {
  organization: ActiveOrganization
  /** Called after a rename or a departure: the parent reloads the organizations. */
  onChanged: () => void
}

/** The active organization: rename (`organization:rename`) and leave (`organization:leave`). */
export function OrganizationSettings({ organization, onChanged }: Props) {
  const canRename = organization.permissions.includes('organization:rename')
  const canLeave = organization.permissions.includes('organization:leave')

  return (
    <div className="space-y-8">
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Organisation</h2>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-slate-500">Nom</dt>
          <dd>{organization.name}</dd>
          <dt className="text-slate-500">Votre rôle</dt>
          <dd>{ROLE_LABELS[organization.role]}</dd>
        </dl>
      </section>
      {canRename && <RenameForm organization={organization} onRenamed={onChanged} />}
      {canLeave && <LeaveSection organization={organization} onLeft={onChanged} />}
    </div>
  )
}

function RenameForm({
  organization,
  onRenamed,
}: {
  organization: ActiveOrganization
  onRenamed: () => void
}) {
  const [name, setName] = useState(organization.name)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const inputId = useId()

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setDone(false)
    const trimmed = name.trim()
    if (trimmed === '') {
      setError("Le nom de l'organisation est obligatoire.")
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await renameOrganization(organization.id, trimmed)
      setDone(true)
      onRenamed()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="space-y-2">
      <h3 className="font-semibold">Renommer l'organisation</h3>
      <form noValidate onSubmit={(e) => void onSubmit(e)} className="space-y-2">
        <label htmlFor={inputId} className="block text-sm font-medium">
          Nouveau nom
        </label>
        <div className="flex gap-2">
          <input
            id={inputId}
            value={name}
            maxLength={ORGANIZATION_NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={error !== null}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2"
          />
          <button
            type="submit"
            disabled={submitting}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50"
          >
            Renommer
          </button>
        </div>
        {error !== null && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        {done && (
          <p role="status" className="text-sm text-green-700">
            Organisation renommée.
          </p>
        )}
      </form>
    </section>
  )
}

function LeaveSection({
  organization,
  onLeft,
}: {
  organization: ActiveOrganization
  onLeft: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onConfirm = async () => {
    setError(null)
    setSubmitting(true)
    try {
      await leaveOrganization(organization.id)
      onLeft()
    } catch (err) {
      setError(errorMessage(err))
      setConfirming(false)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="space-y-2">
      <h3 className="font-semibold">Quitter l'organisation</h3>
      {!confirming ? (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="rounded-lg border border-red-300 px-4 py-2 text-red-700"
        >
          Quitter l'organisation
        </button>
      ) : (
        <div className="space-y-2 rounded-lg border border-red-200 bg-red-50 p-3">
          <p className="text-sm">
            Vous n'aurez plus accès à « {organization.name} » tant qu'on ne vous aura pas invité à
            nouveau.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={submitting}
              onClick={() => void onConfirm()}
              className="rounded-lg bg-red-600 px-4 py-2 text-white disabled:opacity-50"
            >
              Confirmer le départ
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={() => setConfirming(false)}
              className="rounded-lg border border-slate-300 px-4 py-2"
            >
              Annuler
            </button>
          </div>
        </div>
      )}
      {error !== null && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </section>
  )
}
