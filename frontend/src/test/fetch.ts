/** Test helpers: a routed fake `fetch` (`vi.spyOn(globalThis, 'fetch')`), never the real backend. */
import { type MockInstance, vi } from 'vitest'

export interface FetchCall {
  method: string
  /** Path without the query string, e.g. `/api/organizations`. */
  path: string
  query: URLSearchParams
  headers: Headers
  /** Parsed JSON body, or `undefined` when there is none. */
  body: unknown
  signal: AbortSignal | undefined
}

export type Handler = (call: FetchCall) => Response | Promise<Response>

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Handler answering `body` as JSON. */
export const json =
  (body: unknown, status = 200): Handler =>
  () =>
    jsonResponse(body, status)

/** Handler answering 204 No Content. */
export const noContent = (): Handler => () => new Response(null, { status: 204 })

/** Handler answering the backend error format `{code, message}`. */
export const apiError =
  (status: number, code: string, message = 'Raw server message.'): Handler =>
  () =>
    jsonResponse({ code, message }, status)

/** Handler answering a Server-Sent Events body. */
export const sse =
  (body: string): Handler =>
  () =>
    new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })

export function toCall(input: RequestInfo | URL, init?: RequestInit): FetchCall {
  const url = new URL(String(input), 'http://localhost')
  const raw = init?.body
  return {
    method: (init?.method ?? 'GET').toUpperCase(),
    path: url.pathname,
    query: url.searchParams,
    headers: new Headers(init?.headers),
    body: typeof raw === 'string' ? (JSON.parse(raw) as unknown) : undefined,
    signal: init?.signal ?? undefined,
  }
}

export type FetchSpy = MockInstance<typeof fetch>

/**
 * Requests no route answered. `request()` turns the rejection into a `network_error`, which a test
 * could miss: `src/test/setup.ts` fails the test after the fact if this list is not empty.
 */
export const unexpectedRequests: string[] = []

/**
 * Routes each request to `routes["METHOD /path"]` (path without query). A handler may be a list:
 * its entries answer successive calls, the last one is kept. An unknown route fails the test
 * (see `unexpectedRequests`).
 */
export function mockFetch(routes: Record<string, Handler | Handler[]>): FetchSpy {
  const counters = new Map<string, number>()
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const call = toCall(input, init)
    const key = `${call.method} ${call.path}`
    const route = routes[key]
    if (route === undefined) {
      unexpectedRequests.push(key)
      throw new Error(`Unexpected request: ${key}`)
    }
    if (!Array.isArray(route)) return route(call)
    const index = counters.get(key) ?? 0
    counters.set(key, index + 1)
    const handler = route[Math.min(index, route.length - 1)]
    return handler(call)
  })
}

/** Every request seen by the spy, parsed. */
export function calls(spy: FetchSpy): FetchCall[] {
  return spy.mock.calls.map(([input, init]) => toCall(input, init))
}

/** Requests seen by the spy for one route (`"METHOD /path"`). */
export function callsTo(spy: FetchSpy, key: string): FetchCall[] {
  return calls(spy).filter((c) => `${c.method} ${c.path}` === key)
}
