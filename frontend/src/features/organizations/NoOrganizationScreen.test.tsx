import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { json, mockFetch } from '../../test/fetch'
import { NoOrganizationScreen } from './NoOrganizationScreen'

const INVITATION = {
  id: 'inv-acme',
  organization_id: 'org-acme',
  organization_name: 'Acme',
  role: 'member',
  invited_by_name: 'Alice',
  expires_at: '2026-12-01T10:00:00Z',
}

afterEach(() => vi.restoreAllMocks())

describe('NoOrganizationScreen', () => {
  it('shows pending invitations and create form', async () => {
    mockFetch({
      'GET /api/invitations/mine': json([INVITATION]),
      'POST /api/invitations/inv-acme/accept': json({ id: 'org-acme', name: 'Acme', role: 'member' }),
    })
    const onChanged = vi.fn()
    render(<NoOrganizationScreen onChanged={onChanged} />)

    expect(
      screen.getByRole('heading', { name: "Vous n'êtes membre d'aucune organisation" }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Invitations en attente' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Créer une organisation' })).toBeInTheDocument()
    expect(screen.getByLabelText("Nom de l'organisation")).toBeInTheDocument()

    await userEvent.click(
      await screen.findByRole('button', { name: "Accepter l'invitation à Acme" }),
    )

    expect(onChanged).toHaveBeenCalledWith('org-acme')
  })

  it('shows the empty state when there is no invitation', async () => {
    mockFetch({ 'GET /api/invitations/mine': json([]) })
    render(<NoOrganizationScreen onChanged={vi.fn()} />)

    expect(await screen.findByText('Aucune invitation en attente.')).toBeInTheDocument()
  })
})
