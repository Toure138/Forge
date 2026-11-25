import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiError, callsTo, json, mockFetch, noContent, sse } from '../test/fetch'
import {
  ApiError,
  LOGIN_URL,
  createOrganization,
  getActiveOrganization,
  getSession,
  leaveOrganization,
  listAuditEvents,
  listMyOrganizations,
  logout,
  removeMember,
  setCsrfToken,
  setOnUnauthenticated,
  streamAgent,
} from './api'

const ORG = '11111111-1111-4111-8111-111111111111'

afterEach(() => {
  setCsrfToken(null)
  setOnUnauthenticated(null)
  vi.restoreAllMocks()
})

describe('api client', () => {
  it('adds X-CSRF-Token on unsafe methods', async () => {
    const spy = mockFetch({
      'POST /api/organizations': json({ id: ORG, name: 'Acme', role: 'owner' }, 201),
      'GET /api/organizations': json([]),
      'DELETE /api/organization/members/u1': noContent(),
    })
    setCsrfToken('csrf-1')

    await createOrganization('Acme')
    await removeMember(ORG, 'u1')
    await listMyOrganizations()

    const [create] = callsTo(spy, 'POST /api/organizations')
    expect(create.headers.get('X-CSRF-Token')).toBe('csrf-1')
    expect(create.body).toEqual({ name: 'Acme' })
    const [remove] = callsTo(spy, 'DELETE /api/organization/members/u1')
    expect(remove.headers.get('X-CSRF-Token')).toBe('csrf-1')
    const [list] = callsTo(spy, 'GET /api/organizations')
    expect(list.headers.has('X-CSRF-Token')).toBe(false)
  })

  it('adds X-Organization-Id when orgId is given', async () => {
    const spy = mockFetch({
      'GET /api/organization': json({ id: ORG, name: 'Acme', role: 'admin', permissions: [] }),
      'GET /api/organizations': json([]),
      'GET /api/organization/audit': json({ items: [], next_before_id: null }),
    })

    const active = await getActiveOrganization(ORG)
    await listMyOrganizations()
    await listAuditEvents(ORG, 42)

    expect(active.name).toBe('Acme')
    expect(callsTo(spy, 'GET /api/organization')[0].headers.get('X-Organization-Id')).toBe(ORG)
    expect(callsTo(spy, 'GET /api/organizations')[0].headers.has('X-Organization-Id')).toBe(false)
    const [audit] = callsTo(spy, 'GET /api/organization/audit')
    expect(audit.headers.get('X-Organization-Id')).toBe(ORG)
    expect(audit.query.get('before_id')).toBe('42')
  })

  it('throws ApiError with status and code', async () => {
    mockFetch({ 'POST /api/organization/leave': apiError(409, 'last_owner') })

    const error = await leaveOrganization(ORG).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, code: 'last_owner' })
  })

  it('maps FastAPI validation errors and network failures to stable codes', async () => {
    mockFetch({
      'POST /api/organizations': json({ detail: [{ loc: ['body', 'name'] }] }, 422),
    })
    await expect(createOrganization('')).rejects.toMatchObject({
      status: 422,
      code: 'validation_error',
    })

    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(listMyOrganizations()).rejects.toMatchObject({ status: 0, code: 'network_error' })
  })

  it('calls onUnauthenticated on 401', async () => {
    mockFetch({ 'GET /api/auth/session': apiError(401, 'not_authenticated') })
    const onUnauthenticated = vi.fn()
    setOnUnauthenticated(onUnauthenticated)

    await expect(getSession()).rejects.toMatchObject({ status: 401 })

    expect(onUnauthenticated).toHaveBeenCalledOnce()
  })

  it('does not call onUnauthenticated on other errors', async () => {
    mockFetch({ 'POST /api/auth/logout': apiError(403, 'csrf_failed') })
    const onUnauthenticated = vi.fn()
    setOnUnauthenticated(onUnauthenticated)

    await expect(logout()).rejects.toMatchObject({ code: 'csrf_failed' })

    expect(onUnauthenticated).not.toHaveBeenCalled()
  })

  it('streams typed agent events in the active organization', async () => {
    const spy = mockFetch({
      'POST /api/agents/assistant/stream': sse(
        'event: metadata\ndata: {"thread_id":"t-1"}\n\n' +
          'event: token\ndata: {"text":"Bon"}\n\n' +
          'event: tool\ndata: {"name":"get_current_time","status":"start"}\n\n' +
          'event: surprise\ndata: {"x":1}\n\n' +
          'event: token\ndata: not json\n\n' +
          'event: done\ndata: {}\n\n',
      ),
    })
    setCsrfToken('csrf-1')

    const events = []
    for await (const ev of streamAgent(ORG, 'assistant', 'Salut', 't-0')) events.push(ev)

    expect(events).toEqual([
      { type: 'metadata', thread_id: 't-1' },
      { type: 'token', text: 'Bon' },
      { type: 'tool', name: 'get_current_time', status: 'start' },
      { type: 'done' },
    ])
    const [call] = callsTo(spy, 'POST /api/agents/assistant/stream')
    expect(call.headers.get('X-Organization-Id')).toBe(ORG)
    expect(call.headers.get('X-CSRF-Token')).toBe('csrf-1')
    expect(call.body).toEqual({ message: 'Salut', thread_id: 't-0' })
  })

  it('refuses an agent stream with a JSON error before it opens', async () => {
    mockFetch({ 'POST /api/agents/assistant/stream': apiError(403, 'permission_denied') })

    const stream = streamAgent(ORG, 'assistant', 'Salut')

    await expect(stream.next()).rejects.toMatchObject({ status: 403, code: 'permission_denied' })
  })

  it('LOGIN_URL is /api/auth/login', () => {
    expect(LOGIN_URL).toBe('/api/auth/login')
  })
})
