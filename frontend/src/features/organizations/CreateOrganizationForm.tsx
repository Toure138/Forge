import { type FormEvent, useId, useState } from 'react'
import { type OrganizationSummary, createOrganization } from '../../lib/api'
import { errorMessage } from '../../lib/errors'

/** Same bounds as `OrganizationCreate.name` (backend, after stripping spaces). */
export const ORGANIZATION_NAME_MAX = 100

export function CreateOrganizationForm({
  onCreated,
}: {
  onCreated: (organization: OrganizationSummary) => void
}) {
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const inputId = useId()
  const errorId = useId()

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (trimmed === '') {
      setError("Le nom de l'organisation est obligatoire.")
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const organization = await createOrganization(trimmed)
      setName('')
      onCreated(organization)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form noValidate onSubmit={(e) => void onSubmit(e)} className="space-y-2">
      <label htmlFor={inputId} className="block text-sm font-medium">
        Nom de l'organisation
      </label>
      <div className="flex gap-2">
        <input
          id={inputId}
          value={name}
          maxLength={ORGANIZATION_NAME_MAX}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={error !== null}
          aria-describedby={error !== null ? errorId : undefined}
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2"
        />
        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50"
        >
          Créer l'organisation
        </button>
      </div>
      {error !== null && (
        <p id={errorId} role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </form>
  )
}
