/**
 * Typed client for the FastAPI backend. Types mirror backend/app/schemas (the Pydantic schemas are
 * the source of truth): UUIDs and datetimes are strings in JSON.
 *
 * Every call goes through `request()`, which:
 * - sends the session cookie (same origin, through the Vite proxy in dev);
 * - adds `X-CSRF-Token` on unsafe methods (token kept in this module's memory only, never in
 *   `localStorage` / `sessionStorage`; set by `setCsrfToken()` from `GET /api/auth/session`);
 * - adds `X-Organization-Id` when an `orgId` is given (always passed explicitly by the caller);
 * - calls the `onUnauthenticated` handler on any 401, then throws an `ApiError`.
 */
import { readSSE } from './sse'

// --- Contract types (backend/app/schemas) ---------------------------------------------------

/** `app/schemas/common.py` */
export type Role = 'owner' | 'admin' | 'member' | 'guest'
export type Permission =
  | 'organization:read'
  | 'organization:leave'
  | 'organization:rename'
  | 'agents:use'
  | 'members:read'
  | 'members:manage'
  | 'admins:manage'
  | 'owners:manage'
  | 'audit:read'

/** `app/schemas/auth.py` : `UserOut`, `SessionOut` */
export interface User {
  id: string
  email: string
  name: string
  picture_url: string | null
}
export interface SessionInfo {
  user: User
  csrf_token: string
}

/** `app/schemas/organizations.py` */
export interface OrganizationSummary {
  id: string
  name: string
  role: Role
}
export interface ActiveOrganization extends OrganizationSummary {
  /** Sorted alphabetically by the server. */
  permissions: Permission[]
}

/** `app/schemas/members.py` */
export interface Member {
  user_id: string
  name: string
  email: string
  picture_url: string | null
  role: Role
  joined_at: string
}

/** `app/schemas/invitations.py` */
export interface UserRef {
  user_id: string
  name: string
}
export interface Invitation {
  id: string
  /** Normalized (lower-case) address. */
  email: string
  role: Role
  invited_by: UserRef
  created_at: string
  expires_at: string
}
export interface MyInvitation {
  id: string
  organization_id: string
  organization_name: string
  role: Role
  invited_by_name: string
  expires_at: string
}

/** `app/schemas/audit.py` */
export type AuditAction =
  | 'organization.created'
  | 'organization.renamed'
  | 'invitation.created'
  | 'invitation.accepted'
  | 'invitation.declined'
  | 'invitation.revoked'
  | 'invitation.expired_on_use'
  | 'member.role_changed'
  | 'member.removed'
  | 'member.left'
  | 'permission.denied'
export type AuditTargetType = 'organization' | 'member' | 'invitation'
export type AuditOutcome = 'success' | 'denied'
export type AuditDetailValue = string | number | boolean | null
export interface AuditActor {
  user_id: string | null
  name: string | null
}
export interface AuditEvent {
  id: number
  occurred_at: string
  actor: AuditActor
  action: AuditAction
  target_type: AuditTargetType | null
  target_id: string | null
  outcome: AuditOutcome
  details: Record<string, AuditDetailValue>
}
export interface AuditPage {
  /** Newest first. */
  items: AuditEvent[]
  /** Pass it as `before_id` for the next (older) page; null on the last page. */
  next_before_id: number | null
}

/** `app/schemas/agents.py` */
export interface AgentInfo {
  name: string
  description: string
}

/** `app/schemas/errors.py` */
export interface ErrorBody {
  code: string
  message: string
}

/** SSE events of `POST /api/agents/{name}/stream` (docs/architecture.md). */
export type StreamEvent =
  | { type: 'metadata'; thread_id: string }
  | { type: 'token'; text: string }
  | { type: 'tool'; name: string; status: 'start' | 'end' }
  | { type: 'error'; message: string }
  | { type: 'done' }

// --- Errors ----------------------------------------------------------------------------------

/**
 * A refused or failed call. `code` is the backend's `ErrorBody.code`, or one of the client codes:
 * `validation_error` (FastAPI 422), `network_error` (status 0), `http_error` (unexpected body).
 * `message` is for developers only: the UI shows `messageFor(code)` (lib/errors.ts, FR-47).
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

// --- Request helper --------------------------------------------------------------------------

const BASE = '/api'
export const LOGIN_URL = `${BASE}/auth/login`

let csrfToken: string | null = null
let onUnauthenticated: (() => void) | null = null

/** Token returned by `GET /api/auth/session`; kept in memory only. `null` clears it. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

/** Called on every 401 (session expired or revoked); `null` removes the handler. */
export function setOnUnauthenticated(handler: (() => void) | null): void {
  onUnauthenticated = handler
}

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'

interface RequestOptions {
  method?: Method
  orgId?: string
  body?: unknown
  query?: Record<string, string | number | undefined>
  signal?: AbortSignal
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isAbort(error: unknown): boolean {
  return isObject(error) && error.name === 'AbortError'
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // Not JSON (proxy error page, empty body): handled below.
  }
  if (isObject(body) && typeof body.code === 'string') {
    const message = typeof body.message === 'string' ? body.message : ''
    return new ApiError(res.status, body.code, message)
  }
  if (res.status === 422) return new ApiError(422, 'validation_error', 'Validation error.')
  return new ApiError(res.status, 'http_error', `HTTP ${res.status}`)
}

async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrfToken !== null) headers['X-CSRF-Token'] = csrfToken
  if (options.orgId !== undefined) headers['X-Organization-Id'] = options.orgId

  let url = `${BASE}${path}`
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) query.set(key, String(value))
  }
  if (query.size > 0) url += `?${query.toString()}`

  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: 'same-origin',
      signal: options.signal,
    })
  } catch (error) {
    if (isAbort(error)) throw error
    throw new ApiError(0, 'network_error', 'Network error.')
  }
  if (res.status === 401) onUnauthenticated?.()
  if (!res.ok) throw await toApiError(res)
  return res
}

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const res = await request(path, options)
  return (await res.json()) as T
}

async function requestVoid(path: string, options: RequestOptions = {}): Promise<void> {
  await request(path, options)
}

const id = encodeURIComponent

// --- Session ---------------------------------------------------------------------------------

export function getSession(): Promise<SessionInfo> {
  return requestJson('/auth/session')
}

export function logout(): Promise<void> {
  return requestVoid('/auth/logout', { method: 'POST' })
}

// --- My organizations and invitations (no active organization) -------------------------------

export function listMyOrganizations(): Promise<OrganizationSummary[]> {
  return requestJson('/organizations')
}

export function createOrganization(name: string): Promise<OrganizationSummary> {
  return requestJson('/organizations', { method: 'POST', body: { name } })
}

export function listMyInvitations(): Promise<MyInvitation[]> {
  return requestJson('/invitations/mine')
}

export function acceptInvitation(invitationId: string): Promise<OrganizationSummary> {
  return requestJson(`/invitations/${id(invitationId)}/accept`, { method: 'POST' })
}

export function declineInvitation(invitationId: string): Promise<void> {
  return requestVoid(`/invitations/${id(invitationId)}/decline`, { method: 'POST' })
}

// --- Active organization (X-Organization-Id) -------------------------------------------------

export function getActiveOrganization(orgId: string): Promise<ActiveOrganization> {
  return requestJson('/organization', { orgId })
}

export function renameOrganization(orgId: string, name: string): Promise<ActiveOrganization> {
  return requestJson('/organization', { method: 'PATCH', orgId, body: { name } })
}

export function leaveOrganization(orgId: string): Promise<void> {
  return requestVoid('/organization/leave', { method: 'POST', orgId })
}

export function listMembers(orgId: string): Promise<Member[]> {
  return requestJson('/organization/members', { orgId })
}

export function changeMemberRole(orgId: string, userId: string, role: Role): Promise<Member> {
  return requestJson(`/organization/members/${id(userId)}`, {
    method: 'PATCH',
    orgId,
    body: { role },
  })
}

export function removeMember(orgId: string, userId: string): Promise<void> {
  return requestVoid(`/organization/members/${id(userId)}`, { method: 'DELETE', orgId })
}

export function listInvitations(orgId: string): Promise<Invitation[]> {
  return requestJson('/organization/invitations', { orgId })
}

export function createInvitation(orgId: string, email: string, role: Role): Promise<Invitation> {
  return requestJson('/organization/invitations', {
    method: 'POST',
    orgId,
    body: { email, role },
  })
}

export function revokeInvitation(orgId: string, invitationId: string): Promise<void> {
  return requestVoid(`/organization/invitations/${id(invitationId)}`, {
    method: 'DELETE',
    orgId,
  })
}

export function listAuditEvents(orgId: string, beforeId?: number): Promise<AuditPage> {
  return requestJson('/organization/audit', { orgId, query: { before_id: beforeId } })
}

// --- Agents (spec 000, in the active organization: `agents:use`) -----------------------------

export function listAgents(orgId: string): Promise<AgentInfo[]> {
  return requestJson('/agents', { orgId })
}

/** A typed event, or `null` for an unknown event or a malformed payload (ignored). */
function toStreamEvent(event: string, data: string): StreamEvent | null {
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch {
    return null
  }
  if (!isObject(payload)) return null
  switch (event) {
    case 'metadata':
      return typeof payload.thread_id === 'string'
        ? { type: 'metadata', thread_id: payload.thread_id }
        : null
    case 'token':
      return typeof payload.text === 'string' ? { type: 'token', text: payload.text } : null
    case 'tool': {
      const { name, status } = payload
      return typeof name === 'string' && (status === 'start' || status === 'end')
        ? { type: 'tool', name, status }
        : null
    }
    case 'error':
      return { type: 'error', message: typeof payload.message === 'string' ? payload.message : '' }
    case 'done':
      return { type: 'done' }
    default:
      return null
  }
}

/**
 * Runs `agent` and yields its SSE events. `threadId`: omit it for a new conversation, or pass the
 * one of a previous `metadata` event (created by the server) to continue it. Refusals (401, 403,
 * 404 `agent_not_found` / `conversation_not_found`, 422...) are JSON errors thrown as `ApiError`
 * before the stream opens; the `error` event is for failures while the agent runs.
 */
export async function* streamAgent(
  orgId: string,
  agent: string,
  message: string,
  threadId?: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await request(`/agents/${id(agent)}/stream`, {
    method: 'POST',
    orgId,
    body: threadId === undefined ? { message } : { message, thread_id: threadId },
    signal,
  })
  if (res.body === null) throw new ApiError(res.status, 'http_error', 'Empty stream.')
  for await (const msg of readSSE(res.body)) {
    const event = toStreamEvent(msg.event, msg.data)
    if (event !== null) yield event
  }
}
