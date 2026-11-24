import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPanel } from './ChatPanel'

function sseResponse(body: string): Response {
  return new Response(body, { status: 200 })
}

afterEach(() => vi.restoreAllMocks())

describe('ChatPanel', () => {
  it('streams the assistant answer into the conversation', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse(
        'event: metadata\ndata: {"thread_id":"t1"}\n\n' +
          'event: token\ndata: {"text":"Bon"}\n\n' +
          'event: token\ndata: {"text":"jour"}\n\n' +
          'event: done\ndata: {}\n\n',
      ),
    )
    render(<ChatPanel agent="assistant" />)
    await userEvent.type(screen.getByLabelText('Message'), 'Salut')
    await userEvent.click(screen.getByRole('button', { name: 'Envoyer' }))

    expect(await screen.findByText('Bonjour')).toBeInTheDocument()
    expect(screen.getByText('Salut')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/agents/assistant/stream', expect.anything())
  })
})
