import { useCallback, useRef, useState } from 'react'
import { streamAgent } from '../../lib/api'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  tools?: string[]
}

export function useChat(agent: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const threadId = useRef<string | undefined>(undefined)

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
      try {
        for await (const ev of streamAgent(agent, text, threadId.current)) {
          if (ev.type === 'metadata') threadId.current = ev.thread_id
          else if (ev.type === 'token') updateLast((m) => ({ ...m, content: m.content + ev.text }))
          else if (ev.type === 'tool' && ev.status === 'start')
            updateLast((m) => ({ ...m, tools: [...(m.tools ?? []), ev.name] }))
          else if (ev.type === 'error') setError(ev.message)
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setIsStreaming(false)
      }
    },
    [agent],
  )

  const reset = () => {
    threadId.current = undefined
    setMessages([])
    setError(null)
  }

  return { messages, isStreaming, error, send, reset }
}
