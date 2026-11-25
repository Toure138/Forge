import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiError, callsTo, json, mockFetch } from '../../test/fetch'
import { InviteForm } from './InviteForm'
import { ADMIN_PERMISSIONS, INVITATION } from '../../test/fixtures'

afterEach(() => vi.restoreAllMocks())

describe('InviteForm', () => {
  it('invites with an allowed role', async () => {
    const created = { ...INVITATION, email: 'bob@exemple.com', role: 'guest' }
    const spy = mockFetch({ 'POST /api/organization/invitations': json(created, 201) })
    const onInvited = vi.fn()
    render(<InviteForm orgId="org-acme" permissions={ADMIN_PERMISSIONS} onInvited={onInvited} />)

    const role = screen.getByRole('combobox', { name: 'Rôle' })
    expect(within(role).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Membre',
      'Invité',
    ])
    expect(role).toHaveValue('member')
    await userEvent.type(screen.getByLabelText('Adresse email'), 'bob@exemple.com')
    await userEvent.selectOptions(role, 'Invité')
    await userEvent.click(screen.getByRole('button', { name: 'Inviter' }))

    expect(await screen.findByRole('status')).toHaveTextContent('bob@exemple.com')
    expect(onInvited).toHaveBeenCalledWith(created)
    const [post] = callsTo(spy, 'POST /api/organization/invitations')
    expect(post.body).toEqual({ email: 'bob@exemple.com', role: 'guest' })
    expect(post.headers.get('X-Organization-Id')).toBe('org-acme')
    expect(screen.getByLabelText('Adresse email')).toHaveValue('')
  })

  it('shows already_member message', async () => {
    mockFetch({ 'POST /api/organization/invitations': apiError(409, 'already_member') })
    const onInvited = vi.fn()
    render(<InviteForm orgId="org-acme" permissions={ADMIN_PERMISSIONS} onInvited={onInvited} />)

    await userEvent.type(screen.getByLabelText('Adresse email'), 'carole@acme.test')
    await userEvent.click(screen.getByRole('button', { name: 'Inviter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Cette personne est déjà membre de l'organisation.",
    )
    expect(onInvited).not.toHaveBeenCalled()
  })

  it('refuses a malformed address without calling the server', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    render(<InviteForm orgId="org-acme" permissions={ADMIN_PERMISSIONS} onInvited={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Adresse email'), 'pas-une-adresse')
    await userEvent.click(screen.getByRole('button', { name: 'Inviter' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Adresse email invalide.')
    expect(spy).not.toHaveBeenCalled()
  })
})
