import { useState } from 'react'
import type { ActiveOrganization, Invitation, Member, Role } from '../../lib/api'
import { messageFor } from '../../lib/errors'
import { formatDate } from '../../lib/format'
import { ROLES, ROLE_LABELS, canManageRole, isRole } from '../../lib/roles'
import { InviteForm } from './InviteForm'
import { useMembers } from './useMembers'

interface Props {
  organization: ActiveOrganization
  currentUserId: string
  /** Called when the user changed their own role: the parent reloads their permissions. */
  onOwnRoleChanged?: () => void
}

const cell = 'px-3 py-2 text-left align-middle'

/**
 * Members and pending invitations. What is offered follows the permissions (FR-44): roles the user
 * may manage (`members:manage`, `admins:manage`, `owners:manage`). The server stays the authority.
 */
export function MembersPanel({ organization, currentUserId, onOwnRoleChanged }: Props) {
  const { state, busy, actionError, changeRole, remove, revoke, addInvitation } = useMembers(
    organization.id,
  )
  const permissions = organization.permissions
  const canManage = (role: Role) => canManageRole(permissions, role)

  const onChangeRole = async (member: Member, role: Role) => {
    const ok = await changeRole(member.user_id, role)
    if (ok && member.user_id === currentUserId) onOwnRoleChanged?.()
  }

  return (
    <div className="space-y-8">
      {actionError !== null && (
        <p role="alert" className="text-sm text-red-600">
          {messageFor(actionError)}
        </p>
      )}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Membres</h2>
        {state.status === 'loading' && (
          <p className="text-sm text-slate-500">Chargement des membres…</p>
        )}
        {state.status === 'error' && (
          <p role="alert" className="text-sm text-red-600">
            {messageFor(state.code)}
          </p>
        )}
        {state.status === 'ready' && (
          <div className="overflow-x-auto">
            <table aria-label="Membres" className="w-full text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className={cell}>Nom</th>
                  <th className={cell}>Email</th>
                  <th className={cell}>Rôle</th>
                  <th className={cell}>Membre depuis</th>
                  <th className={cell}>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.members.map((member) => (
                  <MemberRow
                    // A new role from the server resets the row's local choice.
                    key={`${member.user_id}:${member.role}`}
                    member={member}
                    isSelf={member.user_id === currentUserId}
                    choices={canManage(member.role) ? ROLES.filter(canManage) : []}
                    busy={busy}
                    onChangeRole={(role) => void onChangeRole(member, role)}
                    onRemove={() => void remove(member.user_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Invitations en attente</h2>
        {permissions.includes('members:manage') && (
          <InviteForm orgId={organization.id} permissions={permissions} onInvited={addInvitation} />
        )}
        {state.status === 'ready' &&
          (state.invitations.length === 0 ? (
            <p className="text-sm text-slate-500">Aucune invitation en attente.</p>
          ) : (
            <div className="overflow-x-auto">
              <table aria-label="Invitations en attente" className="w-full text-sm">
                <thead className="border-b border-slate-200 text-slate-500">
                  <tr>
                    <th className={cell}>Email</th>
                    <th className={cell}>Rôle</th>
                    <th className={cell}>Invité par</th>
                    <th className={cell}>Expire le</th>
                    <th className={cell}>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {state.invitations.map((invitation) => (
                    <InvitationRow
                      key={invitation.id}
                      invitation={invitation}
                      canRevoke={canManage(invitation.role)}
                      busy={busy}
                      onRevoke={() => void revoke(invitation.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </section>
    </div>
  )
}

function MemberRow({
  member,
  isSelf,
  choices,
  busy,
  onChangeRole,
  onRemove,
}: {
  member: Member
  isSelf: boolean
  /** Roles that may be given to this member; empty when their role may not be changed. */
  choices: Role[]
  busy: boolean
  onChangeRole: (role: Role) => void
  onRemove: () => void
}) {
  const [role, setRole] = useState<Role>(member.role)
  const [confirming, setConfirming] = useState(false)
  const { name } = member

  return (
    <tr className="border-b border-slate-100">
      <td className={cell}>
        {name}
        {isSelf && <span className="text-slate-500"> (vous)</span>}
      </td>
      <td className={cell}>{member.email}</td>
      <td className={cell}>
        {choices.length === 0 ? (
          ROLE_LABELS[member.role]
        ) : (
          <div className="flex gap-2">
            <select
              aria-label={`Rôle de ${name}`}
              value={role}
              disabled={busy}
              onChange={(e) => isRole(e.target.value) && setRole(e.target.value)}
              className="rounded border border-slate-300 bg-white px-2 py-1"
            >
              {choices.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABELS[r]}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={busy || role === member.role}
              onClick={() => onChangeRole(role)}
              aria-label={`Changer le rôle de ${name}`}
              className="rounded border border-slate-300 px-2 py-1 disabled:opacity-50"
            >
              Changer le rôle
            </button>
          </div>
        )}
      </td>
      <td className={cell}>{formatDate(member.joined_at)}</td>
      <td className={cell}>
        {choices.length > 0 &&
          !isSelf &&
          (confirming ? (
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={onRemove}
                aria-label={`Confirmer le retrait de ${name}`}
                className="rounded bg-red-600 px-2 py-1 text-white disabled:opacity-50"
              >
                Confirmer le retrait
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="rounded border border-slate-300 px-2 py-1"
              >
                Annuler
              </button>
            </div>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(true)}
              aria-label={`Retirer ${name}`}
              className="rounded border border-red-300 px-2 py-1 text-red-700 disabled:opacity-50"
            >
              Retirer
            </button>
          ))}
      </td>
    </tr>
  )
}

function InvitationRow({
  invitation,
  canRevoke,
  busy,
  onRevoke,
}: {
  invitation: Invitation
  canRevoke: boolean
  busy: boolean
  onRevoke: () => void
}) {
  return (
    <tr className="border-b border-slate-100">
      <td className={cell}>{invitation.email}</td>
      <td className={cell}>{ROLE_LABELS[invitation.role]}</td>
      <td className={cell}>{invitation.invited_by.name}</td>
      <td className={cell}>{formatDate(invitation.expires_at)}</td>
      <td className={cell}>
        {canRevoke && (
          <button
            type="button"
            disabled={busy}
            onClick={onRevoke}
            aria-label={`Révoquer l'invitation de ${invitation.email}`}
            className="rounded border border-slate-300 px-2 py-1 disabled:opacity-50"
          >
            Révoquer
          </button>
        )}
      </td>
    </tr>
  )
}
