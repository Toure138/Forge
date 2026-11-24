import { useEffect, useState } from 'react'
import { type AgentInfo, ApiError, listAgents } from '../../lib/api'

export type AgentsState =
  | { status: 'loading' }
  /** 403 `permission_denied`: no agent is shared with the user (FR-45). */
  | { status: 'forbidden' }
  | { status: 'error'; code: string }
  | { status: 'ready'; agents: AgentInfo[] }

export function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && error.code === 'permission_denied'
}

/** Agents of the active organization (`agents:use`). */
export function useAgents(orgId: string): AgentsState {
  const [state, setState] = useState<AgentsState>({ status: 'loading' })

  useEffect(() => {
    let mounted = true
    listAgents(orgId)
      .then((agents) => mounted && setState({ status: 'ready', agents }))
      .catch((error: unknown) => {
        if (!mounted) return
        if (isForbidden(error)) setState({ status: 'forbidden' })
        else setState({ status: 'error', code: error instanceof ApiError ? error.code : 'unknown' })
      })
    return () => {
      mounted = false
    }
  }, [orgId])

  return state
}
