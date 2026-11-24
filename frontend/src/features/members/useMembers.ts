import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  type Invitation,
  type Member,
  type Role,
  changeMemberRole,
  listInvitations,
  listMembers,
  removeMember,
  revokeInvitation,
} from '../../lib/api'

export type MembersState =
  | { status: 'loading' }
  | { status: 'error'; code: string }
  | { status: 'ready'; members: Member[]; invitations: Invitation[] }

type Ready = Extract<MembersState, { status: 'ready' }>

function codeOf(error: unknown): string {
  return error instanceof ApiError ? error.code : 'unknown'
}

async function fetchMembers(orgId: string): Promise<MembersState> {
  try {
    const [members, invitations] = await Promise.all([listMembers(orgId), listInvitations(orgId)])
    return { status: 'ready', members, invitations }
  } catch (error) {
    return { status: 'error', code: codeOf(error) }
  }
}

/** Members and pending invitations of the active organization (`members:read`). */
export function useMembers(orgId: string) {
  const [state, setState] = useState<MembersState>({ status: 'loading' })
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    void fetchMembers(orgId).then((next) => mounted && setState(next))
    return () => {
      mounted = false
    }
  }, [orgId])

  const update = (fn: (ready: Ready) => Ready) =>
    setState((prev) => (prev.status === 'ready' ? fn(prev) : prev))

  /** Runs an action; on failure keeps the translated code, and reloads if the target is gone. */
  const run = useCallback(
    async (action: () => Promise<void>): Promise<boolean> => {
      setBusy(true)
      setActionError(null)
      try {
        await action()
        return true
      } catch (error) {
        setActionError(codeOf(error))
        if (error instanceof ApiError && error.status === 404) {
          setState(await fetchMembers(orgId))
        }
        return false
      } finally {
        setBusy(false)
      }
    },
    [orgId],
  )

  const changeRole = useCallback(
    (userId: string, role: Role) =>
      run(async () => {
        const changed = await changeMemberRole(orgId, userId, role)
        update((s) => ({
          ...s,
          members: s.members.map((m) => (m.user_id === userId ? changed : m)),
        }))
      }),
    [orgId, run],
  )

  const remove = useCallback(
    (userId: string) =>
      run(async () => {
        await removeMember(orgId, userId)
        update((s) => ({ ...s, members: s.members.filter((m) => m.user_id !== userId) }))
      }),
    [orgId, run],
  )

  const revoke = useCallback(
    (invitationId: string) =>
      run(async () => {
        await revokeInvitation(orgId, invitationId)
        update((s) => ({ ...s, invitations: s.invitations.filter((i) => i.id !== invitationId) }))
      }),
    [orgId, run],
  )

  const addInvitation = useCallback((invitation: Invitation) => {
    update((s) => ({ ...s, invitations: [...s.invitations, invitation] }))
  }, [])

  return { state, busy, actionError, changeRole, remove, revoke, addInvitation }
}
