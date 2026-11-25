import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  type ActiveOrganization,
  type OrganizationSummary,
  getActiveOrganization,
  listMyOrganizations,
} from '../../lib/api'

/**
 * Last organization chosen, used as the default of a new tab. Not a secret, and always revalidated
 * against the list of the user's organizations. The active organization itself is per tab (R10):
 * it lives in this hook's state, and every request names it (`X-Organization-Id`).
 */
export const ACTIVE_ORGANIZATION_KEY = 'forge.activeOrganizationId'

type Ready = {
  status: 'ready'
  organizations: OrganizationSummary[]
  active: ActiveOrganization
  switching: boolean
}

export type OrganizationsState =
  | { status: 'loading' }
  | { status: 'error'; code: string }
  | { status: 'none' }
  | Ready

export interface Organizations {
  state: OrganizationsState
  /** Makes `organizationId` the active organization of this tab. */
  switchTo: (organizationId: string) => void
  /** Reloads the list and the active organization (preferring `organizationId` when given). */
  refresh: (organizationId?: string) => void
}

function readRemembered(): string | null {
  try {
    return localStorage.getItem(ACTIVE_ORGANIZATION_KEY)
  } catch {
    return null
  }
}

function remember(organizationId: string | null): void {
  try {
    if (organizationId === null) localStorage.removeItem(ACTIVE_ORGANIZATION_KEY)
    else localStorage.setItem(ACTIVE_ORGANIZATION_KEY, organizationId)
  } catch {
    // Storage unavailable (private mode): the choice is simply not remembered.
  }
}

function codeOf(error: unknown): string {
  return error instanceof ApiError ? error.code : 'unknown'
}

/** The user's organizations and the one to make active: `wanted` if still a member, else the first. */
async function fetchOrganizations(wanted: string | null): Promise<OrganizationsState> {
  try {
    const organizations = await listMyOrganizations()
    if (organizations.length === 0) return { status: 'none' }
    const target = organizations.find((o) => o.id === wanted) ?? organizations[0]
    const active = await getActiveOrganization(target.id)
    return { status: 'ready', organizations, active, switching: false }
  } catch (error) {
    return { status: 'error', code: codeOf(error) }
  }
}

export function useOrganizations(): Organizations {
  const [state, setState] = useState<OrganizationsState>({ status: 'loading' })
  // The active organization of THIS tab (another tab may have changed localStorage since).
  const activeId = useRef<string | null>(null)
  // Only the latest load or switch may update the state.
  const generation = useRef(0)

  const apply = useCallback((next: OrganizationsState) => {
    if (next.status === 'ready') {
      activeId.current = next.active.id
      remember(next.active.id)
    } else if (next.status === 'none') {
      activeId.current = null
      remember(null)
    }
    setState(next)
  }, [])

  const load = useCallback(
    (preferredId?: string) => {
      const current = ++generation.current
      void fetchOrganizations(preferredId ?? activeId.current ?? readRemembered()).then((next) => {
        if (current === generation.current) apply(next)
      })
    },
    [apply],
  )

  useEffect(() => {
    let mounted = true
    const current = ++generation.current
    void fetchOrganizations(readRemembered()).then((next) => {
      if (mounted && current === generation.current) apply(next)
    })
    return () => {
      mounted = false
    }
  }, [apply])

  const switchTo = useCallback(
    (organizationId: string) => {
      const current = ++generation.current
      setState((prev) => (prev.status === 'ready' ? { ...prev, switching: true } : prev))
      getActiveOrganization(organizationId)
        .then((active) => {
          if (current !== generation.current) return
          activeId.current = active.id
          remember(active.id)
          setState((prev) =>
            prev.status === 'ready' ? { ...prev, active, switching: false } : prev,
          )
        })
        .catch(() => {
          // No longer a member, or another failure: start again from the server's list.
          if (current === generation.current) load()
        })
    },
    [load],
  )

  return { state, switchTo, refresh: load }
}
