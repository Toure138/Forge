import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { MyInvitation, OrganizationSummary } from './lib/api'
import { setCsrfToken } from './lib/api'
import {
  type FetchCall,
  type Handler,
  apiError,
  callsTo,
  json,
  jsonResponse,
  mockFetch,
  noContent,
} from './test/fetch'
import { MEMBERS, PERMISSIONS_OF } from './test/fixtures'

const SESSION = {
  user: { id: 'u-bob', email: 'bob@acme.test', name: 'Bob Martin', picture_url: null },
  csrf_token: 'csrf-bob',
}
const ACME: OrganizationSummary = { id: 'org-acme', name: 'Acme', role: 'admin' }
const GLOBEX: OrganizationSummary = { id: 'org-globex', name: 'Globex', role: 'member' }
const INVITATION: MyInvitation = {
  id: 'inv-initech',
  organization_id: 'org-initech',
  organization_name: 'Initech',
  role: 'member',
  invited_by_name: 'Ivan',
  expires_at: '2026-12-01T10:00:00Z',
}

/** A signed-in backend: `organizations` with their roles, `GET /organization` by header. */
function backend(
  organizations: OrganizationSummary[],
  extra: Record<string, Handler | Handler[]> = {},
  invitations: MyInvitation[] = [],
) {
  const active = (call: FetchCall) => {
    const org = organizations.find((o) => o.id === call.headers.get('X-Organization-Id'))
    if (org === undefined) return jsonResponse({ code: 'organization_not_found', message: '' }, 404)
    return jsonResponse({ ...org, permissions: PERMISSIONS_OF[org.role] })
  }
  return mockFetch({
    'GET /api/auth/session': json(SESSION),
    'GET /api/organizations': json(organizations),
    'GET /api/organization': active,
    'GET /api/invitations/mine': json(invitations),
    'GET /api/organization/members': json(MEMBERS),
    'GET /api/organization/invitations': json([]),
    'GET /api/organization/audit': json({ items: [], next_before_id: null }),
    'POST /api/auth/logout': noContent(),
    'GET /api/agents': json([{ name: 'assistant', description: 'General assistant' }]),
    ...extra,
  })
}

const tabNames = () => screen.queryAllByRole('tab').map((t) => t.textContent)

beforeEach(() => localStorage.clear())
afterEach(() => {
  setCsrfToken(null)
  vi.restoreAllMocks()
})

describe('App', () => {
  it('shows only the login page to visitors', async () => {
    mockFetch({ 'GET /api/auth/session': apiError(401, 'not_authenticated') })
    render(<App />)

    expect(await screen.findByRole('link', { name: 'Se connecter avec Google' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByText('Votre session a pris fin, reconnectez-vous.')).not.toBeInTheDocument()
  })

  it('user without organization sees the no-organization screen', async () => {
    backend([], {}, [INVITATION])
    render(<App />)

    expect(
      await screen.findByRole('heading', { name: "Vous n'êtes membre d'aucune organisation" }),
    ).toBeInTheDocument()
    expect(await screen.findByRole('listitem', { name: /Initech/ })).toBeInTheDocument()
    expect(screen.getByLabelText("Nom de l'organisation")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Déconnexion' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Organisation' })).not.toBeInTheDocument()
  })

  it('enters the organization just created', async () => {
    const created: OrganizationSummary = { id: 'org-new', name: 'Initech', role: 'owner' }
    const spy = backend([], {
      'GET /api/organizations': [json([]), json([created])],
      'POST /api/organizations': json(created, 201),
      'GET /api/organization': json({ ...created, permissions: PERMISSIONS_OF.owner }),
    })
    render(<App />)

    await userEvent.type(await screen.findByLabelText("Nom de l'organisation"), 'Initech')
    await userEvent.click(screen.getByRole('button', { name: "Créer l'organisation" }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Initech' })).toBeInTheDocument()
    expect(callsTo(spy, 'POST /api/organizations')[0].headers.get('X-CSRF-Token')).toBe('csrf-bob')
  })

  it('switching organization updates name and role-dependent screens', async () => {
    backend([ACME, GLOBEX])
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: 'Acme' })).toBeInTheDocument()
    expect(screen.getByText('Bob Martin')).toBeInTheDocument()
    expect(tabNames()).toEqual(['Chat', 'Membres', 'Journal', 'Organisation'])
    await userEvent.click(screen.getByRole('tab', { name: 'Membres' }))
    expect(await screen.findByRole('table', { name: 'Membres' })).toBeInTheDocument()

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Organisation' }),
      'Globex (Membre)',
    )

    expect(await screen.findByRole('heading', { level: 1, name: 'Globex' })).toBeInTheDocument()
    expect(tabNames()).toEqual(['Chat', 'Organisation'])
    expect(screen.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('table', { name: 'Membres' })).not.toBeInTheDocument()
    expect(localStorage.getItem('forge.activeOrganizationId')).toBe('org-globex')
  })

  it.each([
    ['member', ['Chat', 'Organisation']],
    ['guest', ['Chat', 'Organisation']],
    ['admin', ['Chat', 'Membres', 'Journal', 'Organisation']],
    ['owner', ['Chat', 'Membres', 'Journal', 'Organisation']],
  ] as const)('management tabs follow permissions [%s]', async (role, tabs) => {
    backend([{ ...ACME, role }])
    render(<App />)

    await screen.findByRole('heading', { level: 1, name: 'Acme' })
    expect(tabNames()).toEqual(tabs)
  })

  it('returns to login with session-ended message on 401', async () => {
    backend([ACME], { 'GET /api/organization/members': apiError(401, 'not_authenticated') })
    render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Acme' })

    await userEvent.click(screen.getByRole('tab', { name: 'Membres' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Votre session a pris fin, reconnectez-vous.',
    )
    expect(screen.getByRole('link', { name: 'Se connecter avec Google' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('logs out back to the login page', async () => {
    const spy = backend([ACME])
    render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Acme' })

    await userEvent.click(screen.getByRole('button', { name: 'Déconnexion' }))

    expect(await screen.findByRole('link', { name: 'Se connecter avec Google' })).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(callsTo(spy, 'POST /api/auth/logout')[0].headers.get('X-CSRF-Token')).toBe('csrf-bob')
  })

  it('shows pending invitations inside the app', async () => {
    const joined: OrganizationSummary = { id: 'org-initech', name: 'Initech', role: 'member' }
    backend([ACME], {
      'GET /api/organizations': [json([ACME]), json([ACME, joined])],
      'POST /api/invitations/inv-initech/accept': json(joined),
    }, [INVITATION])
    render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Acme' })

    const item = await screen.findByRole('listitem', { name: /Initech/ })
    await userEvent.click(within(item).getByRole('button', { name: "Accepter l'invitation à Initech" }))

    expect(await screen.findByRole('option', { name: 'Initech (Membre)' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Acme' })).toBeInTheDocument()
  })

  it('chat runs in the active organization; a Guest sees the no-agent message', async () => {
    const spy = backend([ACME, { ...GLOBEX, role: 'guest' }])
    render(<App />)

    expect(await screen.findByRole('combobox', { name: 'Agent' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(callsTo(spy, 'GET /api/agents')[0].headers.get('X-Organization-Id')).toBe('org-acme')

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Organisation' }),
      'Globex (Invité)',
    )

    expect(
      await screen.findByText("Aucun agent ne vous est partagé pour l'instant."),
    ).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Agent' })).not.toBeInTheDocument()
    expect(callsTo(spy, 'GET /api/agents')).toHaveLength(1)
  })

  it('tabs are keyboard reachable', async () => {
    backend([ACME])
    render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Acme' })
    const chat = screen.getByRole('tab', { name: 'Chat' })

    for (let i = 0; i < 10 && document.activeElement !== chat; i++) await userEvent.tab()
    expect(chat).toHaveFocus()

    await userEvent.keyboard('{ArrowRight}')
    const members = screen.getByRole('tab', { name: 'Membres' })
    expect(members).toHaveFocus()
    expect(members).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('tabpanel', { name: 'Membres' })).toBeInTheDocument()

    await userEvent.keyboard('{End}')
    expect(screen.getByRole('tab', { name: 'Organisation' })).toHaveFocus()
    expect(screen.getByRole('tabpanel', { name: 'Organisation' })).toBeInTheDocument()

    await userEvent.keyboard('{ArrowRight}')
    expect(chat).toHaveFocus()

    // The selected tab is the only one in the Tab order (roving tabindex).
    await userEvent.tab()
    expect(screen.getByRole('tabpanel', { name: 'Chat' })).toHaveFocus()
  })
})
