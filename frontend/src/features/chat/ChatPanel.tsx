import { type FormEvent, useState } from 'react'
import type { AgentInfo, Permission } from '../../lib/api'
import { messageFor } from '../../lib/errors'
import { useAgents } from './useAgents'
import { useChat } from './useChat'

const NO_SHARED_AGENT = "Aucun agent ne vous est partagé pour l'instant."

interface Props {
  orgId: string
  permissions: readonly Permission[]
}

/**
 * Chat with the agents of the active organization. Without `agents:use` (Guest, FR-45), or when
 * the server refuses with 403, a clear message instead of an error, and no call to the server.
 */
export function ChatPanel({ orgId, permissions }: Props) {
  if (!permissions.includes('agents:use')) return <NoSharedAgent />
  return <AgentChat orgId={orgId} />
}

function NoSharedAgent() {
  return <p className="text-slate-600">{NO_SHARED_AGENT}</p>
}

function AgentChat({ orgId }: { orgId: string }) {
  const state = useAgents(orgId)
  const [selected, setSelected] = useState<string | null>(null)

  if (state.status === 'loading') {
    return <p className="text-sm text-slate-500">Chargement des agents…</p>
  }
  if (state.status === 'forbidden') return <NoSharedAgent />
  if (state.status === 'error') {
    return (
      <p role="alert" className="text-sm text-red-600">
        {messageFor(state.code)}
      </p>
    )
  }
  if (state.agents.length === 0) {
    return <p className="text-sm text-slate-500">Aucun agent disponible.</p>
  }
  const agent = pickAgent(state.agents, selected)

  return (
    <div className="flex h-full flex-col gap-4">
      <select
        aria-label="Agent"
        className="self-start rounded border border-slate-300 bg-white px-2 py-1"
        value={agent}
        onChange={(e) => setSelected(e.target.value)}
      >
        {state.agents.map((a) => (
          <option key={a.name} value={a.name} title={a.description}>
            {a.name}
          </option>
        ))}
      </select>
      {/* Another agent: another conversation (the thread id is per agent on the server). */}
      <Conversation key={agent} orgId={orgId} agent={agent} />
    </div>
  )
}

/** The chosen agent, else `assistant` (spec 000's default), else the first one. */
function pickAgent(agents: AgentInfo[], selected: string | null): string {
  const names = agents.map((a) => a.name)
  if (selected !== null && names.includes(selected)) return selected
  return names.includes('assistant') ? 'assistant' : names[0]
}

function Conversation({ orgId, agent }: { orgId: string; agent: string }) {
  const { messages, isStreaming, error, forbidden, send, reset } = useChat(orgId, agent)
  const [input, setInput] = useState('')

  if (forbidden) return <NoSharedAgent />

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || isStreaming) return
    setInput('')
    void send(text)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="min-h-64 flex-1 space-y-3 overflow-y-auto" aria-live="polite">
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
      {error !== null && (
        <p role="alert" className="text-sm text-red-600">
          {messageFor(error)}
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
