import { useId } from 'react'
import type { MyInvitation, OrganizationSummary } from '../../lib/api'
import { messageFor } from '../../lib/errors'
import { formatDate } from '../../lib/format'
import { ROLE_LABELS } from '../../lib/roles'
import { useMyInvitations } from './useMyInvitations'

interface Props {
  /** Called once the user has joined the organization of an accepted invitation. */
  onAccepted: (organization: OrganizationSummary) => void
  /** Inside the app (FR-22): show nothing at all while loading or when there is no invitation. */
  hideWhenEmpty?: boolean
}

export function MyInvitations({ onAccepted, hideWhenEmpty = false }: Props) {
  const { state, busyId, actionError, accept, decline } = useMyInvitations()
  const headingId = useId()

  const hasItems = state.status === 'ready' && state.invitations.length > 0
  if (hideWhenEmpty && !hasItems && actionError === null) return null

  const onAccept = async (invitation: MyInvitation) => {
    const joined = await accept(invitation.id)
    if (joined !== null) onAccepted(joined)
  }

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="text-lg font-semibold">
        Invitations en attente
      </h2>
      {state.status === 'loading' && (
        <p className="text-sm text-slate-500">Chargement des invitations…</p>
      )}
      {state.status === 'error' && (
        <p role="alert" className="text-sm text-red-600">
          {messageFor(state.code)}
        </p>
      )}
      {actionError !== null && (
        <p role="alert" className="text-sm text-red-600">
          {messageFor(actionError)}
        </p>
      )}
      {state.status === 'ready' && state.invitations.length === 0 && (
        <p className="text-sm text-slate-500">Aucune invitation en attente.</p>
      )}
      {hasItems && (
        <ul className="space-y-2">
          {state.invitations.map((invitation) => (
            <InvitationItem
              key={invitation.id}
              invitation={invitation}
              busy={busyId !== null}
              onAccept={() => void onAccept(invitation)}
              onDecline={() => void decline(invitation.id)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function InvitationItem({
  invitation,
  busy,
  onAccept,
  onDecline,
}: {
  invitation: MyInvitation
  busy: boolean
  onAccept: () => void
  onDecline: () => void
}) {
  const nameId = useId()
  const name = invitation.organization_name
  return (
    <li
      aria-labelledby={nameId}
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 p-3"
    >
      <div className="text-sm">
        <p id={nameId} className="font-medium">
          {name}
        </p>
        <p className="text-slate-600">
          Rôle : {ROLE_LABELS[invitation.role]} · invité par {invitation.invited_by_name} · expire
          le {formatDate(invitation.expires_at)}
        </p>
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onAccept}
          aria-label={`Accepter l'invitation à ${name}`}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          Accepter
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onDecline}
          aria-label={`Refuser l'invitation à ${name}`}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
        >
          Refuser
        </button>
      </div>
    </li>
  )
}
