import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MyInvitation } from '../../lib/api'
import { apiError, callsTo, json, mockFetch, noContent } from '../../test/fetch'
import { MyInvitations } from './MyInvitations'

const ACME_INVITATION: MyInvitation = {
  id: 'inv-acme',
  organization_id: 'org-acme',
  organization_name: 'Acme',
  role: 'member',
  invited_by_name: 'Alice',
  expires_at: '2026-12-01T10:00:00Z',
}
const GLOBEX_INVITATION: MyInvitation = {
  ...ACME_INVITATION,
  id: 'inv-globex',
  organization_id: 'org-globex',
  organization_name: 'Globex',
  role: 'guest',
  invited_by_name: 'Gina',
}

afterEach(() => vi.restoreAllMocks())

describe('MyInvitations', () => {
  it('accepts an invitation then notifies the parent', async () => {
    const joined = { id: 'org-acme', name: 'Acme', role: 'member' }
    const spy = mockFetch({
      'GET /api/invitations/mine': json([ACME_INVITATION, GLOBEX_INVITATION]),
      'POST /api/invitations/inv-acme/accept': json(joined),
    })
    const onAccepted = vi.fn()
    render(<MyInvitations onAccepted={onAccepted} />)

    const item = await screen.findByRole('listitem', { name: /Acme/ })
    expect(item).toHaveTextContent('Membre')
    expect(item).toHaveTextContent('Alice')
    await userEvent.click(within(item).getByRole('button', { name: "Accepter l'invitation à Acme" }))

    expect(onAccepted).toHaveBeenCalledWith(joined)
    expect(callsTo(spy, 'POST /api/invitations/inv-acme/accept')).toHaveLength(1)
    expect(screen.queryByRole('listitem', { name: /Acme/ })).not.toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: /Globex/ })).toBeInTheDocument()
  })

  it('declines an invitation', async () => {
    const spy = mockFetch({
      'GET /api/invitations/mine': json([ACME_INVITATION]),
      'POST /api/invitations/inv-acme/decline': noContent(),
    })
    const onAccepted = vi.fn()
    render(<MyInvitations onAccepted={onAccepted} />)

    await userEvent.click(
      await screen.findByRole('button', { name: "Refuser l'invitation à Acme" }),
    )

    expect(await screen.findByText('Aucune invitation en attente.')).toBeInTheDocument()
    expect(callsTo(spy, 'POST /api/invitations/inv-acme/decline')).toHaveLength(1)
    expect(onAccepted).not.toHaveBeenCalled()
  })

  it('shows translated error when the invitation expired', async () => {
    mockFetch({
      'GET /api/invitations/mine': json([ACME_INVITATION]),
      'POST /api/invitations/inv-acme/accept': apiError(410, 'invitation_expired'),
    })
    const onAccepted = vi.fn()
    render(<MyInvitations onAccepted={onAccepted} />)

    await userEvent.click(
      await screen.findByRole('button', { name: "Accepter l'invitation à Acme" }),
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Cette invitation a expiré.')
    expect(screen.queryByText('Raw server message.')).not.toBeInTheDocument()
    expect(screen.queryByRole('listitem', { name: /Acme/ })).not.toBeInTheDocument()
    expect(onAccepted).not.toHaveBeenCalled()
  })

  it('shows a loading state then a translated loading error', async () => {
    mockFetch({ 'GET /api/invitations/mine': apiError(500, 'boom') })
    render(<MyInvitations onAccepted={vi.fn()} />)

    expect(screen.getByText('Chargement des invitations…')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('Une erreur est survenue.')
  })

  it('renders nothing when empty and hideWhenEmpty is set', async () => {
    const spy = mockFetch({ 'GET /api/invitations/mine': json([]) })
    const { container } = render(<MyInvitations onAccepted={vi.fn()} hideWhenEmpty />)

    await vi.waitFor(() => expect(callsTo(spy, 'GET /api/invitations/mine')).toHaveLength(1))
    expect(container).toBeEmptyDOMElement()
  })
})
