import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type Permission, setCsrfToken } from '../../lib/api'
import { apiError, callsTo, json, mockFetch, sse } from '../../test/fetch'
import { ChatPanel } from './ChatPanel'

const ORG = 'org-acme'
const THREAD = '6f1c1a52-8a52-4f55-9a3e-7f1f6b1d2c11'
const MEMBER: Permission[] = ['agents:use', 'organization:leave', 'organization:read']
const GUEST: Permission[] = ['organization:leave', 'organization:read']
const NO_AGENT = "Aucun agent ne vous est partagé pour l'instant."
const AGENTS = json([
  { name: 'assistant', description: 'General assistant' },
  { name: 'researcher', description: 'Research' },
])

const answer = (text: string) =>
  sse(
    `event: metadata\ndata: {"thread_id":"${THREAD}"}\n\n` +
      `event: token\ndata: {"text":"${text.slice(0, 3)}"}\n\n` +
      `event: token\ndata: {"text":"${text.slice(3)}"}\n\n` +
      'event: done\ndata: {}\n\n',
  )

async function send(text: string) {
  await userEvent.type(screen.getByLabelText('Message'), text)
  await userEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
}

beforeEach(() => setCsrfToken('csrf-1'))
afterEach(() => {
  setCsrfToken(null)
  vi.restoreAllMocks()
})

describe('ChatPanel', () => {
  it('streams the assistant answer into the conversation', async () => {
    const spy = mockFetch({
      'GET /api/agents': AGENTS,
      'POST /api/agents/assistant/stream': answer('Bonjour'),
    })
    render(<ChatPanel orgId={ORG} permissions={MEMBER} />)
    expect(await screen.findByRole('combobox', { name: 'Agent' })).toHaveValue('assistant')

    await send('Salut')

    expect(await screen.findByText('Bonjour')).toBeInTheDocument()
    expect(screen.getByText('Salut')).toBeInTheDocument()
    const [list] = callsTo(spy, 'GET /api/agents')
    expect(list.headers.get('X-Organization-Id')).toBe(ORG)
    const [stream] = callsTo(spy, 'POST /api/agents/assistant/stream')
    expect(stream.headers.get('X-Organization-Id')).toBe(ORG)
    expect(stream.headers.get('X-CSRF-Token')).toBe('csrf-1')
    expect(stream.body).toEqual({ message: 'Salut' })
  })

  it('guest sees no shared agent message', () => {
    const spy = vi.spyOn(globalThis, 'fetch')

    render(<ChatPanel orgId={ORG} permissions={GUEST} />)

    expect(screen.getByText(NO_AGENT)).toBeInTheDocument()
    expect(screen.queryByLabelText('Message')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(spy).not.toHaveBeenCalled()
  })

  it('403 on agents shows the same message', async () => {
    mockFetch({ 'GET /api/agents': apiError(403, 'permission_denied') })
    const { unmount } = render(<ChatPanel orgId={ORG} permissions={MEMBER} />)

    expect(await screen.findByText(NO_AGENT)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    unmount()
    vi.restoreAllMocks()

    // Demoted to Guest since the page was loaded (FR-26): refused before the stream opens.
    mockFetch({
      'GET /api/agents': AGENTS,
      'POST /api/agents/assistant/stream': apiError(403, 'permission_denied'),
    })
    render(<ChatPanel orgId={ORG} permissions={MEMBER} />)
    await screen.findByRole('combobox', { name: 'Agent' })
    await send('Salut')

    expect(await screen.findByText(NO_AGENT)).toBeInTheDocument()
    expect(screen.queryByLabelText('Message')).not.toBeInTheDocument()
  })

  it('reuses the server thread_id', async () => {
    const spy = mockFetch({
      'GET /api/agents': AGENTS,
      'POST /api/agents/assistant/stream': answer('Bonjour'),
    })
    render(<ChatPanel orgId={ORG} permissions={MEMBER} />)
    await screen.findByRole('combobox', { name: 'Agent' })

    await send('Salut')
    await screen.findByText('Bonjour')
    await send('Encore')
    await vi.waitFor(() => expect(callsTo(spy, 'POST /api/agents/assistant/stream')).toHaveLength(2))

    const [first, second] = callsTo(spy, 'POST /api/agents/assistant/stream')
    expect(first.body).toEqual({ message: 'Salut' })
    expect(second.body).toEqual({ message: 'Encore', thread_id: THREAD })

    await userEvent.click(screen.getByRole('button', { name: 'Nouveau' }))
    await send('Nouvelle')
    await vi.waitFor(() => expect(callsTo(spy, 'POST /api/agents/assistant/stream')).toHaveLength(3))
    expect(callsTo(spy, 'POST /api/agents/assistant/stream')[2].body).toEqual({
      message: 'Nouvelle',
    })
  })

  it('starts a new conversation with another agent', async () => {
    const spy = mockFetch({
      'GET /api/agents': AGENTS,
      'POST /api/agents/assistant/stream': answer('Bonjour'),
      'POST /api/agents/researcher/stream': answer('Résultat'),
    })
    render(<ChatPanel orgId={ORG} permissions={MEMBER} />)
    await screen.findByRole('combobox', { name: 'Agent' })
    await send('Salut')
    await screen.findByText('Bonjour')

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Agent' }), 'researcher')

    expect(screen.queryByText('Bonjour')).not.toBeInTheDocument()
    await send('Cherche')
    expect(await screen.findByText('Résultat')).toBeInTheDocument()
    expect(callsTo(spy, 'POST /api/agents/researcher/stream')[0].body).toEqual({
      message: 'Cherche',
    })
  })

  it('shows translated errors, never the raw server message', async () => {
    mockFetch({
      'GET /api/agents': AGENTS,
      'POST /api/agents/assistant/stream': [
        sse(
          `event: metadata\ndata: {"thread_id":"${THREAD}"}\n\n` +
            'event: error\ndata: {"message":"RateLimitError: secret details"}\n\n' +
            'event: done\ndata: {}\n\n',
        ),
        apiError(404, 'conversation_not_found'),
      ],
    })
    render(<ChatPanel orgId={ORG} permissions={MEMBER} />)
    await screen.findByRole('combobox', { name: 'Agent' })

    await send('Salut')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "L'agent a rencontré une erreur. Réessayez.",
    )
    expect(screen.queryByText(/secret details/)).not.toBeInTheDocument()

    await send('Encore')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Cette conversation est introuvable. Commencez-en une nouvelle.',
    )
  })

  it('shows the empty and error states of the agent list', async () => {
    mockFetch({ 'GET /api/agents': json([]) })
    const { unmount } = render(<ChatPanel orgId={ORG} permissions={MEMBER} />)
    expect(screen.getByText('Chargement des agents…')).toBeInTheDocument()
    expect(await screen.findByText('Aucun agent disponible.')).toBeInTheDocument()
    unmount()
    vi.restoreAllMocks()

    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    render(<ChatPanel orgId={ORG} permissions={MEMBER} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Le serveur est injoignable.')
  })
})
