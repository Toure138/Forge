import { type FormEvent, useState } from 'react'
import { useChat } from './useChat'

export function ChatPanel({ agent }: { agent: string }) {
  const { messages, isStreaming, error, send, reset } = useChat(agent)
  const [input, setInput] = useState('')

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || isStreaming) return
    setInput('')
    void send(text)
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex-1 space-y-3 overflow-y-auto" aria-live="polite">
        {messages.length === 0 && (
          <p className="text-sm text-slate-400">Envoie un message à l'agent « {agent} ».</p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[80%] rounded-lg px-4 py-2 whitespace-pre-wrap ${
              m.role === 'user' ? 'ml-auto bg-indigo-600 text-white' : 'bg-slate-100 text-slate-900'
            }`}
          >
            {m.tools && m.tools.length > 0 && (
              <div className="mb-1 text-xs text-slate-500">🔧 {m.tools.join(', ')}</div>
            )}
            {m.content || (isStreaming && i === messages.length - 1 ? '…' : '')}
          </div>
        ))}
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 focus:outline-indigo-500"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ton message…"
          aria-label="Message"
        />
        <button
          type="submit"
          disabled={isStreaming || !input.trim()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50"
        >
          Envoyer
        </button>
        <button type="button" onClick={reset} className="rounded-lg border px-3 py-2 text-sm">
          Nouveau
        </button>
      </form>
    </div>
  )
}
