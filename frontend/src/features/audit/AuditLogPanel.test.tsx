import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AuditEvent } from '../../lib/api'
import { formatDateTime } from '../../lib/format'
import { apiError, callsTo, json, mockFetch } from '../../test/fetch'
import { AuditLogPanel } from './AuditLogPanel'

const CAROLE_ID = '33333333-3333-4333-8333-333333333333'

const event = (id: number, fields: Partial<AuditEvent>): AuditEvent => ({
  id,
  occurred_at: '2026-11-24T10:15:00Z',
  actor: { user_id: 'u-bob', name: 'Bob' },
  action: 'organization.created',
  target_type: 'organization',
  target_id: 'org-acme',
  outcome: 'success',
  details: {},
  ...fields,
})

const ROLE_CHANGED = event(12, {
  action: 'member.role_changed',
  target_type: 'member',
  target_id: CAROLE_ID,
  details: { from_role: 'member', to_role: 'guest' },
})
const DENIED = event(11, {
  occurred_at: '2026-11-24T09:00:00Z',
  actor: { user_id: CAROLE_ID, name: 'Carole' },
  action: 'permission.denied',
  outcome: 'denied',
  details: { permission: 'members:manage' },
})
const LAST_OWNER = event(10, {
  actor: { user_id: null, name: null },
  action: 'member.left',
  target_type: 'member',
  target_id: 'u-alice',
  outcome: 'denied',
  details: { role: 'owner', reason: 'last_owner' },
})
const RENAMED = event(6, {
  action: 'organization.renamed',
  details: { from_name: 'Acme', to_name: 'Acme SA' },
})

const table = () => screen.getByRole('table', { name: "Journal d'audit" })
const row = (text: RegExp) => within(table()).getByRole('row', { name: text })

afterEach(() => vi.restoreAllMocks())

describe('AuditLogPanel', () => {
  it('renders events with actor, action, target, date and outcome', async () => {
    const spy = mockFetch({
      'GET /api/organization/audit': json({
        items: [ROLE_CHANGED, DENIED, LAST_OWNER],
        next_before_id: null,
      }),
    })
    render(<AuditLogPanel orgId="org-acme" />)

    expect(screen.getByText('Chargement du journal…')).toBeInTheDocument()
    const changed = await screen.findByRole('row', { name: /Rôle modifié/ })
    expect(changed).toHaveTextContent('Bob')
    expect(changed).toHaveTextContent('Membre → Invité')
    expect(changed).toHaveTextContent(`Membre ${CAROLE_ID.slice(0, 8)}`)
    expect(changed).toHaveTextContent('Réussi')
    const time = within(changed).getByText(formatDateTime(ROLE_CHANGED.occurred_at))
    expect(time).toHaveAttribute('datetime', ROLE_CHANGED.occurred_at)

    const denied = row(/Permission refusée/)
    expect(denied).toHaveTextContent('Carole')
    expect(denied).toHaveTextContent('Refusé')
    expect(denied).toHaveTextContent('Permission : gérer les membres')
    expect(denied).toHaveTextContent('Organisation')

    const left = row(/Départ/)
    expect(left).toHaveTextContent('Inconnu')
    expect(left).toHaveTextContent('Rôle : Propriétaire')
    expect(left).toHaveTextContent('Motif : dernier Propriétaire')

    expect(screen.queryByRole('button', { name: 'Plus ancien' })).not.toBeInTheDocument()
    const [first] = callsTo(spy, 'GET /api/organization/audit')
    expect(first.headers.get('X-Organization-Id')).toBe('org-acme')
    expect(first.query.has('before_id')).toBe(false)
  })

  it('loads older events with before_id', async () => {
    const spy = mockFetch({
      'GET /api/organization/audit': [
        json({ items: [ROLE_CHANGED, DENIED], next_before_id: 11 }),
        json({ items: [RENAMED], next_before_id: null }),
      ],
    })
    render(<AuditLogPanel orgId="org-acme" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Plus ancien' }))

    const renamed = await screen.findByRole('row', { name: /Organisation renommée/ })
    expect(renamed).toHaveTextContent('« Acme » → « Acme SA »')
    expect(row(/Rôle modifié/)).toBeInTheDocument()
    expect(callsTo(spy, 'GET /api/organization/audit')[1].query.get('before_id')).toBe('11')
    expect(screen.queryByRole('button', { name: 'Plus ancien' })).not.toBeInTheDocument()
  })

  it('shows empty state', async () => {
    mockFetch({ 'GET /api/organization/audit': json({ items: [], next_before_id: null }) })
    render(<AuditLogPanel orgId="org-acme" />)

    expect(await screen.findByText('Aucun événement dans le journal.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows translated error', async () => {
    mockFetch({ 'GET /api/organization/audit': apiError(403, 'permission_denied') })
    render(<AuditLogPanel orgId="org-acme" />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Vous n'avez pas la permission d'effectuer cette action.",
    )
    expect(screen.queryByText('Raw server message.')).not.toBeInTheDocument()
  })

  it('keeps the loaded events when an older page fails', async () => {
    mockFetch({
      'GET /api/organization/audit': [
        json({ items: [ROLE_CHANGED], next_before_id: 12 }),
        apiError(503, 'unavailable'),
      ],
    })
    render(<AuditLogPanel orgId="org-acme" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Plus ancien' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Une erreur est survenue.')
    expect(row(/Rôle modifié/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Plus ancien' })).toBeEnabled()
  })
})
