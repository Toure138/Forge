import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActiveOrganization, OrganizationSummary } from '../../lib/api'
import { type FetchCall, callsTo, json, jsonResponse, mockFetch } from '../../test/fetch'
import { ACTIVE_ORGANIZATION_KEY, useOrganizations } from './useOrganizations'

const ACME: OrganizationSummary = { id: 'org-acme', name: 'Acme', role: 'admin' }
const GLOBEX: OrganizationSummary = { id: 'org-globex', name: 'Globex', role: 'member' }
const PERMISSIONS: Record<string, ActiveOrganization['permissions']> = {
  'org-acme': ['agents:use', 'audit:read', 'members:manage', 'members:read', 'organization:read'],
  'org-globex': ['agents:use', 'organization:leave', 'organization:read'],
}

/** `GET /api/organization` answers the organization named by the header. */
function activeFor(call: FetchCall): Response {
  const orgId = call.headers.get('X-Organization-Id') ?? ''
  const summary = [ACME, GLOBEX].find((o) => o.id === orgId)
  if (summary === undefined) return jsonResponse({ code: 'organization_not_found' }, 404)
  return jsonResponse({ ...summary, permissions: PERMISSIONS[orgId] })
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('useOrganizations', () => {
  it('restores the remembered organization only if still a member', async () => {
    localStorage.setItem(ACTIVE_ORGANIZATION_KEY, 'org-globex')
    mockFetch({ 'GET /api/organizations': json([ACME, GLOBEX]), 'GET /api/organization': activeFor })

    const remembered = renderHook(() => useOrganizations())

    await waitFor(() => expect(remembered.result.current.state.status).toBe('ready'))
    expect(remembered.result.current.state).toMatchObject({ active: { id: 'org-globex' } })
    remembered.unmount()

    localStorage.setItem(ACTIVE_ORGANIZATION_KEY, 'org-left-since')
    const stale = renderHook(() => useOrganizations())

    await waitFor(() => expect(stale.result.current.state.status).toBe('ready'))
    expect(stale.result.current.state).toMatchObject({ active: { id: 'org-acme' } })
    expect(localStorage.getItem(ACTIVE_ORGANIZATION_KEY)).toBe('org-acme')
  })

  it('loads permissions of the active organization', async () => {
    const spy = mockFetch({
      'GET /api/organizations': json([ACME, GLOBEX]),
      'GET /api/organization': activeFor,
    })

    const { result } = renderHook(() => useOrganizations())

    await waitFor(() => expect(result.current.state.status).toBe('ready'))
    expect(result.current.state).toMatchObject({
      organizations: [ACME, GLOBEX],
      active: { id: 'org-acme', role: 'admin', permissions: PERMISSIONS['org-acme'] },
    })
    const [active] = callsTo(spy, 'GET /api/organization')
    expect(active.headers.get('X-Organization-Id')).toBe('org-acme')
  })

  it('switches to another organization and remembers it', async () => {
    mockFetch({ 'GET /api/organizations': json([ACME, GLOBEX]), 'GET /api/organization': activeFor })
    const { result } = renderHook(() => useOrganizations())
    await waitFor(() => expect(result.current.state.status).toBe('ready'))

    act(() => result.current.switchTo('org-globex'))

    await waitFor(() =>
      expect(result.current.state).toMatchObject({
        active: { id: 'org-globex', permissions: PERMISSIONS['org-globex'] },
      }),
    )
    expect(localStorage.getItem(ACTIVE_ORGANIZATION_KEY)).toBe('org-globex')
  })

  it('has no organization when the list is empty', async () => {
    localStorage.setItem(ACTIVE_ORGANIZATION_KEY, 'org-acme')
    mockFetch({ 'GET /api/organizations': json([]) })

    const { result } = renderHook(() => useOrganizations())

    await waitFor(() => expect(result.current.state.status).toBe('none'))
    expect(localStorage.getItem(ACTIVE_ORGANIZATION_KEY)).toBeNull()
  })

  it('refresh keeps the active organization, or falls back after leaving it', async () => {
    const spy = mockFetch({
      'GET /api/organizations': [json([ACME, GLOBEX]), json([ACME, GLOBEX]), json([ACME])],
      'GET /api/organization': activeFor,
    })
    const { result } = renderHook(() => useOrganizations())
    await waitFor(() => expect(result.current.state.status).toBe('ready'))
    act(() => result.current.switchTo('org-globex'))
    await waitFor(() => expect(result.current.state).toMatchObject({ active: { id: 'org-globex' } }))

    act(() => result.current.refresh())
    await waitFor(() => expect(callsTo(spy, 'GET /api/organizations')).toHaveLength(2))
    await waitFor(() => expect(result.current.state).toMatchObject({ active: { id: 'org-globex' } }))

    act(() => result.current.refresh())
    await waitFor(() =>
      expect(result.current.state).toMatchObject({ organizations: [ACME], active: { id: 'org-acme' } }),
    )
  })

  it('reports an error when the list cannot be loaded', async () => {
    mockFetch({ 'GET /api/organizations': json({ code: 'boom', message: 'x' }, 500) })

    const { result } = renderHook(() => useOrganizations())

    await waitFor(() => expect(result.current.state).toEqual({ status: 'error', code: 'boom' }))
  })
})
