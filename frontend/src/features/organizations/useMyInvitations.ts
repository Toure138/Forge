import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  type MyInvitation,
  type OrganizationSummary,
  acceptInvitation,
  declineInvitation,
  listMyInvitations,
} from '../../lib/api'

export type MyInvitationsState =
  | { status: 'loading' }
  | { status: 'error'; code: string }
  | { status: 'ready'; invitations: MyInvitation[] }

function codeOf(error: unknown): string {
  return error instanceof ApiError ? error.code : 'unknown'
}

/** 404 (gone) and 410 (expired): the invitation can no longer be used, drop it from the list. */
function isGone(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 410)
}

/** The signed-in user's pending invitations (`GET /api/invitations/mine`, FR-22). */
export function useMyInvitations() {
  const [state, setState] = useState<MyInvitationsState>({ status: 'loading' })
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    listMyInvitations()
      .then((invitations) => mounted && setState({ status: 'ready', invitations }))
      .catch((error: unknown) => mounted && setState({ status: 'error', code: codeOf(error) }))
    return () => {
      mounted = false
    }
  }, [])

  const drop = (invitationId: string) =>
    setState((prev) =>
      prev.status === 'ready'
        ? { ...prev, invitations: prev.invitations.filter((i) => i.id !== invitationId) }
        : prev,
    )

  const run = useCallback(async <T>(invitationId: string, action: () => Promise<T>) => {
    setBusyId(invitationId)
    setActionError(null)
    try {
      const result = await action()
      drop(invitationId)
      return result
    } catch (error) {
      setActionError(codeOf(error))
      if (isGone(error)) drop(invitationId)
      return null
    } finally {
      setBusyId(null)
    }
  }, [])

  const accept = useCallback(
    (invitationId: string): Promise<OrganizationSummary | null> =>
      run(invitationId, () => acceptInvitation(invitationId)),
    [run],
  )

  const decline = useCallback(
    async (invitationId: string): Promise<void> => {
      await run(invitationId, () => declineInvitation(invitationId))
    },
    [run],
  )

  return { state, busyId, actionError, accept, decline }
}
