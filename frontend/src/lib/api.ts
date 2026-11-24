/** Typed client for the FastAPI backend. Mirrors backend/app/schemas. */
import { readSSE } from './sse'

export interface AgentInfo {
  name: string
  description: string
}

export type StreamEvent =
  | { type: 'metadata'; thread_id: string }
  | { type: 'token'; text: string }
  | { type: 'tool'; name: string; status: 'start' | 'end' }
  | { type: 'error'; message: string }
  | { type: 'done' }

const BASE = '/api'

export async function listAgents(): Promise<AgentInfo[]> {
  const res = await fetch(`${BASE}/agents`)
  if (!res.ok) throw new Error(`GET /agents failed: ${res.status}`)
  return res.json()
}

export async function* streamAgent(
  agent: string,
  message: string,
  threadId?: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${BASE}/agents/${agent}/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, thread_id: threadId }),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`POST /stream failed: ${res.status}`)
  for await (const msg of readSSE(res.body)) {
    yield { type: msg.event, ...JSON.parse(msg.data) } as StreamEvent
  }
}
