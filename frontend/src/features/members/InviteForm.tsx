import { type FormEvent, useId, useState } from 'react'
import { type Invitation, type Permission, type Role, createInvitation } from '../../lib/api'
import { errorMessage } from '../../lib/errors'
import { ROLE_LABELS, isRole, manageableRoles } from '../../lib/roles'

/** Same check as `InvitationCreate.email` (backend). */
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const EMAIL_MAX = 320

interface Props {
  orgId: string
  permissions: readonly Permission[]
  onInvited: (invitation: Invitation) => void
}

/** Invite by email, with a role the user may give (FR-18; the server re-checks it). */
export function InviteForm({ orgId, permissions, onInvited }: Props) {
  const roles = manageableRoles(permissions)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>(roles.includes('member') ? 'member' : (roles[0] ?? 'guest'))
  const [error, setError] = useState<string | null>(null)
  const [invited, setInvited] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const emailId = useId()
  const roleId = useId()

  if (roles.length === 0) return null

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setInvited(null)
    const address = email.trim()
    if (!EMAIL_PATTERN.test(address)) {
      setError('Adresse email invalide.')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const invitation = await createInvitation(orgId, address, role)
      setEmail('')
      setInvited(invitation.email)
      onInvited(invitation)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form noValidate onSubmit={(e) => void onSubmit(e)} className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-60 flex-1 flex-col gap-1">
          <label htmlFor={emailId} className="text-sm font-medium">
            Adresse email
          </label>
          <input
            id={emailId}
            type="email"
            value={email}
            maxLength={EMAIL_MAX}
            autoComplete="off"
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={error !== null}
            className="rounded-lg border border-slate-300 px-3 py-2"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={roleId} className="text-sm font-medium">
            Rôle
          </label>
          <select
            id={roleId}
            value={role}
            onChange={(e) => isRole(e.target.value) && setRole(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2 py-2"
          >
            {roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50"
        >
          Inviter
        </button>
      </div>
      {error !== null && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      {invited !== null && (
        <p role="status" className="text-sm text-green-700">
          Invitation créée pour {invited}. Prévenez cette personne vous-même : Forge n'envoie aucun
          email. Elle verra l'invitation en se connectant avec cette adresse.
        </p>
      )}
    </form>
  )
}
