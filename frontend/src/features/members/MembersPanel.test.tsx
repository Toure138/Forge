import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiError, callsTo, json, mockFetch, noContent } from '../../test/fetch'
import { MembersPanel } from './MembersPanel'
import { AS_ADMIN, AS_OWNER, BOB, CAROLE, INVITATION, MEMBERS } from '../../test/fixtures'

function routes(extra: Parameters<typeof mockFetch>[0] = {}, invitations = [INVITATION]) {
  return mockFetch({
    'GET /api/organization/members': json(MEMBERS),
    'GET /api/organization/invitations': json(invitations),
    ...extra,
  })
}

const row = (name: string) =>
  within(screen.getByRole('table', { name: 'Membres' })).getByRole('row', { name: new RegExp(name) })
const optionNames = (select: HTMLElement) =>
  within(select)
    .getAllByRole('option')
    .map((o) => o.textContent)

afterEach(() => vi.restoreAllMocks())

describe('MembersPanel', () => {
  it('lists members with roles', async () => {
    const spy = routes()
    render(<MembersPanel organization={AS_ADMIN} currentUserId={BOB.user_id} />)

    expect(screen.getByText('Chargement des membres…')).toBeInTheDocument()
    await screen.findByRole('row', { name: /Alice/ })
    expect(row('Alice')).toHaveTextContent('Propriétaire')
    expect(row('Bob')).toHaveTextContent('Admin')
    expect(row('Bob')).toHaveTextContent('(vous)')
    expect(row('Dave')).toHaveTextContent('dave@acme.test')
    expect(within(row('Carole')).getByRole('combobox', { name: 'Rôle de Carole' })).toHaveValue(
      'member',
    )
    const [list] = callsTo(spy, 'GET /api/organization/members')
    expect(list.headers.get('X-Organization-Id')).toBe('org-acme')
  })

  it('role choices follow admins:manage and owners:manage', async () => {
    routes()
    const { unmount } = render(
      <MembersPanel organization={AS_ADMIN} currentUserId={BOB.user_id} />,
    )
    await screen.findByRole('row', { name: /Alice/ })

    // An Admin manages Members and Guests only.
    expect(screen.queryByRole('combobox', { name: 'Rôle de Alice' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Rôle de Bob' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retirer Alice' })).not.toBeInTheDocument()
    expect(optionNames(screen.getByRole('combobox', { name: 'Rôle de Carole' }))).toEqual([
      'Membre',
      'Invité',
    ])
    expect(screen.getByRole('button', { name: 'Retirer Dave' })).toBeInTheDocument()
    unmount()

    routes()
    render(<MembersPanel organization={AS_OWNER} currentUserId="u-alice" />)
    await screen.findByRole('row', { name: /Alice/ })
    expect(optionNames(screen.getByRole('combobox', { name: 'Rôle de Carole' }))).toEqual([
      'Propriétaire',
      'Admin',
      'Membre',
      'Invité',
    ])
    expect(screen.getByRole('button', { name: 'Retirer Bob' })).toBeInTheDocument()
    // One's own departure goes through « Quitter l'organisation ».
    expect(screen.queryByRole('button', { name: 'Retirer Alice' })).not.toBeInTheDocument()
  })

  it('changes a role and removes a member', async () => {
    const spy = routes({
      'PATCH /api/organization/members/u-carole': json({ ...CAROLE, role: 'guest' }),
      'DELETE /api/organization/members/u-dave': noContent(),
    })
    render(<MembersPanel organization={AS_ADMIN} currentUserId={BOB.user_id} />)
    const select = await screen.findByRole('combobox', { name: 'Rôle de Carole' })

    await userEvent.selectOptions(select, 'Invité')
    await userEvent.click(screen.getByRole('button', { name: 'Changer le rôle de Carole' }))

    await vi.waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Rôle de Carole' })).toHaveValue('guest'),
    )
    const [patch] = callsTo(spy, 'PATCH /api/organization/members/u-carole')
    expect(patch.body).toEqual({ role: 'guest' })
    expect(patch.headers.get('X-Organization-Id')).toBe('org-acme')

    await userEvent.click(screen.getByRole('button', { name: 'Retirer Dave' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirmer le retrait de Dave' }))

    await vi.waitFor(() =>
      expect(screen.queryByRole('row', { name: /Dave/ })).not.toBeInTheDocument(),
    )
    expect(callsTo(spy, 'DELETE /api/organization/members/u-dave')).toHaveLength(1)
  })

  it('shows translated error on refusal', async () => {
    routes({ 'PATCH /api/organization/members/u-carole': apiError(403, 'permission_denied') })
    render(<MembersPanel organization={AS_ADMIN} currentUserId={BOB.user_id} />)

    await userEvent.selectOptions(
      await screen.findByRole('combobox', { name: 'Rôle de Carole' }),
      'Invité',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Changer le rôle de Carole' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Vous n'avez pas la permission d'effectuer cette action.",
    )
    expect(screen.queryByText('Raw server message.')).not.toBeInTheDocument()
  })

  it('shows translated error when the list cannot be loaded', async () => {
    mockFetch({
      'GET /api/organization/members': apiError(403, 'permission_denied'),
      'GET /api/organization/invitations': json([]),
    })
    render(<MembersPanel organization={AS_ADMIN} currentUserId={BOB.user_id} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Vous n'avez pas la permission d'effectuer cette action.",
    )
  })

  it('shows empty state for pending invitations', async () => {
    routes({}, [])
    render(<MembersPanel organization={AS_ADMIN} currentUserId={BOB.user_id} />)

    expect(await screen.findByText('Aucune invitation en attente.')).toBeInTheDocument()
  })

  it('revokes a pending invitation', async () => {
    const spy = routes({ 'DELETE /api/organization/invitations/inv-erin': noContent() })
    render(<MembersPanel organization={AS_ADMIN} currentUserId={BOB.user_id} />)

    const invitation = await screen.findByRole('row', { name: /erin@example.test/ })
    expect(invitation).toHaveTextContent('Membre')
    expect(invitation).toHaveTextContent('Bob')
    await userEvent.click(
      within(invitation).getByRole('button', {
        name: "Révoquer l'invitation de erin@example.test",
      }),
    )

    expect(await screen.findByText('Aucune invitation en attente.')).toBeInTheDocument()
    expect(callsTo(spy, 'DELETE /api/organization/invitations/inv-erin')).toHaveLength(1)
  })

  it('offers no management to a reader without members:manage', async () => {
    routes()
    const reader = { ...AS_ADMIN, permissions: AS_ADMIN.permissions.filter((p) => p !== 'members:manage') }
    render(<MembersPanel organization={reader} currentUserId={BOB.user_id} />)

    await screen.findByRole('row', { name: /Carole/ })
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retirer|Révoquer|Inviter/ })).not.toBeInTheDocument()
  })
})
