import { useEffect, useState } from 'react'
import { ChatPanel } from './features/chat/ChatPanel'
import { type AgentInfo, listAgents } from './lib/api'

export default function App() {
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [selected, setSelected] = useState('assistant')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listAgents()
      .then(setAgents)
      .catch((e: Error) => setError(`Backend injoignable : ${e.message}`))
  }, [])

  return (
    <div className="mx-auto flex h-screen max-w-3xl flex-col gap-4 p-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Forge</h1>
        <select
          className="rounded border px-2 py-1"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          aria-label="Agent"
        >
          {agents.map((a) => (
            <option key={a.name} value={a.name} title={a.description}>
              {a.name}
            </option>
          ))}
        </select>
      </header>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <main className="min-h-0 flex-1">
        <ChatPanel key={selected} agent={selected} />
      </main>
    </div>
  )
}
