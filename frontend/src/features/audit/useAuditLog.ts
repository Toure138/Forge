import { useCallback, useEffect, useState } from 'react'
import { ApiError, type AuditEvent, listAuditEvents } from '../../lib/api'

export type AuditLogState =
  | { status: 'loading' }
  | { status: 'error'; code: string }
  | {
      status: 'ready'
      items: AuditEvent[]
      /** `before_id` of the next (older) page; null on the last page. */
      nextBeforeId: number | null
      loadingMore: boolean
      moreError: string | null
    }

function codeOf(error: unknown): string {
  return error instanceof ApiError ? error.code : 'unknown'
}

/** Audit log of the active organization, newest first, paginated (`audit:read`). */
export function useAuditLog(orgId: string) {
  const [state, setState] = useState<AuditLogState>({ status: 'loading' })

  useEffect(() => {
    let mounted = true
    listAuditEvents(orgId)
      .then((page) => {
        if (!mounted) return
        setState({
          status: 'ready',
          items: page.items,
          nextBeforeId: page.next_before_id,
          loadingMore: false,
          moreError: null,
        })
      })
      .catch((error: unknown) => mounted && setState({ status: 'error', code: codeOf(error) }))
    return () => {
      mounted = false
    }
  }, [orgId])

  const loadOlder = useCallback(async () => {
    if (state.status !== 'ready' || state.nextBeforeId === null || state.loadingMore) return
    const beforeId = state.nextBeforeId
    setState({ ...state, loadingMore: true, moreError: null })
    try {
      const page = await listAuditEvents(orgId, beforeId)
      setState((prev) =>
        prev.status === 'ready'
          ? {
              ...prev,
              items: [...prev.items, ...page.items],
              nextBeforeId: page.next_before_id,
              loadingMore: false,
            }
          : prev,
      )
    } catch (error) {
      setState((prev) =>
        prev.status === 'ready' ? { ...prev, loadingMore: false, moreError: codeOf(error) } : prev,
      )
    }
  }, [orgId, state])

  return { state, loadOlder }
}
