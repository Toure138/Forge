import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, streamAgent } from '../../lib/api'
import { isForbidden } from './useAgents'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  tools?: string[]
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/**
 * One conversation with `agent` in the organization `orgId`. The conversation id (`thread_id`) is
 * created by the server and taken from the `metadata` event; it is never invented here. Unmounting
 * (another agent, another organization) aborts the stream in progress.
 */
export function useChat(orgId: string, agent: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  /** Error code, translated by the component (lib/errors.ts). */
  const [error, setError] = useState<string | null>(null)
  /** Refused with 403 `permission_denied` (e.g. demoted to Guest since the page was loaded). */
  const [forbidden, setForbidden] = useState(false)
  const threadId = useRef<string | undefined>(undefined)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  const updateLast = (fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => [...prev.slice(0, -1), fn(prev[prev.length - 1])])

  const send = useCallback(
    async (text: string) => {
      setError(null)
      setIsStreaming(true)
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: text },
        { role: 'assistant', content: '', tools: [] },
      ])
      const abort = new AbortController()
      controller.current = abort
      try {
        for await (const ev of streamAgent(orgId, agent, text, threadId.current, abort.signal)) {
          if (ev.type === 'metadata') threadId.current = ev.thread_id
          else if (ev.type === 'token') updateLast((m) => ({ ...m, content: m.content + ev.text }))
          else if (ev.type === 'tool' && ev.status === 'start')
            updateLast((m) => ({ ...m, tools: [...(m.tools ?? []), ev.name] }))
          // The agent's raw error text is never shown (FR-47).
          else if (ev.type === 'error') setError('agent_error')
        }
      } catch (e) {
        if (isAbort(e)) return
        if (isForbidden(e)) setForbidden(true)
        else setError(e instanceof ApiError ? e.code : 'unknown')
      } finally {
        if (controller.current === abort) controller.current = null
        setIsStreaming(false)
      }
    },
    [orgId, agent],
  )

  const reset = () => {
    controller.current?.abort()
    threadId.current = undefined
    setMessages([])
    setError(null)
  }

  return { messages, isStreaming, error, forbidden, send, reset }
}
