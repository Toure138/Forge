import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActiveOrganization } from '../../lib/api'
import { apiError, callsTo, json, mockFetch, noContent } from '../../test/fetch'
import { OrganizationSettings } from './OrganizationSettings'

const OWNER: ActiveOrganization = {
  id: 'org-acme',
  name: 'Acme',
  role: 'owner',
  permissions: [
    'admins:manage',
    'agents:use',
    'audit:read',
    'members:manage',
    'members:read',
    'organization:leave',
    'organization:read',
    'organization:rename',
    'owners:manage',
  ],
}
const ADMIN: ActiveOrganization = {
  ...OWNER,
  role: 'admin',
  permissions: OWNER.permissions.filter(
    (p) => !['admins:manage', 'owners:manage', 'organization:rename'].includes(p),
  ),
}

async function leave() {
  await userEvent.click(screen.getByRole('button', { name: "Quitter l'organisation" }))
  await userEvent.click(screen.getByRole('button', { name: 'Confirmer le départ' }))
}

afterEach(() => vi.restoreAllMocks())

describe('OrganizationSettings', () => {
  it('rename is offered only with organization:rename', () => {
    const { unmount } = render(<OrganizationSettings organization={ADMIN} onChanged={vi.fn()} />)

    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getByText('Admin')).toBeInTheDocument()
    expect(screen.queryByLabelText('Nouveau nom')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Renommer' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Quitter l'organisation" })).toBeInTheDocument()
    unmount()

    render(<OrganizationSettings organization={OWNER} onChanged={vi.fn()} />)
    expect(screen.getByLabelText('Nouveau nom')).toHaveValue('Acme')
    expect(screen.getByRole('button', { name: 'Renommer' })).toBeInTheDocument()
  })

  it('renames and notifies the parent', async () => {
    const spy = mockFetch({ 'PATCH /api/organization': json({ ...OWNER, name: 'Acme SA' }) })
    const onChanged = vi.fn()
    render(<OrganizationSettings organization={OWNER} onChanged={onChanged} />)

    const input = screen.getByLabelText('Nouveau nom')
    await userEvent.clear(input)
    await userEvent.type(input, 'Acme SA')
    await userEvent.click(screen.getByRole('button', { name: 'Renommer' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Organisation renommée.')
    expect(onChanged).toHaveBeenCalledOnce()
    const [rename] = callsTo(spy, 'PATCH /api/organization')
    expect(rename.body).toEqual({ name: 'Acme SA' })
    expect(rename.headers.get('X-Organization-Id')).toBe('org-acme')
  })

  it('refuses an empty name without calling the server', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    render(<OrganizationSettings organization={OWNER} onChanged={vi.fn()} />)

    await userEvent.clear(screen.getByLabelText('Nouveau nom'))
    await userEvent.click(screen.getByRole('button', { name: 'Renommer' }))

    expect(screen.getByRole('alert')).toHaveTextContent("Le nom de l'organisation est obligatoire.")
    expect(spy).not.toHaveBeenCalled()
  })

  it('leave shows translated last_owner error', async () => {
    mockFetch({ 'POST /api/organization/leave': apiError(409, 'last_owner') })
    const onChanged = vi.fn()
    render(<OrganizationSettings organization={OWNER} onChanged={onChanged} />)

    await leave()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "L'organisation doit garder au moins un Propriétaire.",
    )
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('leave notifies the parent on success', async () => {
    const spy = mockFetch({ 'POST /api/organization/leave': noContent() })
    const onChanged = vi.fn()
    render(<OrganizationSettings organization={ADMIN} onChanged={onChanged} />)

    await leave()

    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
    expect(callsTo(spy, 'POST /api/organization/leave')[0].headers.get('X-Organization-Id')).toBe(
      'org-acme',
    )
  })

  it('leave can be cancelled', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    render(<OrganizationSettings organization={ADMIN} onChanged={vi.fn()} />)

    await userEvent.click(screen.getByRole('button', { name: "Quitter l'organisation" }))
    await userEvent.click(screen.getByRole('button', { name: 'Annuler' }))

    expect(screen.queryByRole('button', { name: 'Confirmer le départ' })).not.toBeInTheDocument()
    expect(spy).not.toHaveBeenCalled()
  })
})
