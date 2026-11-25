import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { listMyOrganizations, setCsrfToken } from '../../lib/api'
import { apiError, callsTo, json, mockFetch, noContent } from '../../test/fetch'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './useAuth'

const SESSION = {
  user: { id: 'u-1', email: 'alice@acme.test', name: 'Alice', picture_url: null },
  csrf_token: 'csrf-alice',
}

function Probe() {
  const auth = useAuth()
  if (auth.status === 'loading') return <p>chargement</p>
  if (auth.status === 'error') return <p>erreur</p>
  if (auth.status === 'visitor') return <p>visiteur : {auth.reason ?? 'aucun motif'}</p>
  return (
    <div>
      <p>connecté : {auth.user.name}</p>
      <button type="button" onClick={() => void auth.logout()}>
        Déconnexion
      </button>
      <button type="button" onClick={() => void listMyOrganizations().catch(() => undefined)}>
        Appel
      </button>
    </div>
  )
}

function renderProvider() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

afterEach(() => {
  setCsrfToken(null)
  vi.restoreAllMocks()
})

describe('AuthProvider', () => {
  it('loads the session and sets the CSRF token', async () => {
    const spy = mockFetch({
      'GET /api/auth/session': json(SESSION),
      'POST /api/auth/logout': noContent(),
    })

    renderProvider()

    expect(screen.getByText('chargement')).toBeInTheDocument()
    expect(await screen.findByText('connecté : Alice')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Déconnexion' }))
    const [logout] = callsTo(spy, 'POST /api/auth/logout')
    expect(logout.headers.get('X-CSRF-Token')).toBe('csrf-alice')
  })

  it('is a visitor without reason when there is no session', async () => {
    mockFetch({ 'GET /api/auth/session': apiError(401, 'not_authenticated') })

    renderProvider()

    expect(await screen.findByText('visiteur : aucun motif')).toBeInTheDocument()
  })

  it('becomes a visitor with reason session_ended on 401 after being signed in', async () => {
    mockFetch({
      'GET /api/auth/session': json(SESSION),
      'GET /api/organizations': apiError(401, 'not_authenticated'),
    })
    renderProvider()
    await screen.findByText('connecté : Alice')

    await userEvent.click(screen.getByRole('button', { name: 'Appel' }))

    expect(await screen.findByText('visiteur : session_ended')).toBeInTheDocument()
  })

  it('logout calls the API and becomes a visitor', async () => {
    const spy = mockFetch({
      'GET /api/auth/session': json(SESSION),
      'POST /api/auth/logout': noContent(),
    })
    renderProvider()
    await screen.findByText('connecté : Alice')

    await userEvent.click(screen.getByRole('button', { name: 'Déconnexion' }))

    expect(await screen.findByText('visiteur : aucun motif')).toBeInTheDocument()
    expect(callsTo(spy, 'POST /api/auth/logout')).toHaveLength(1)
  })

  it('shows an error state when the server is unreachable', async () => {
    mockFetch({ 'GET /api/auth/session': apiError(500, 'internal') })

    renderProvider()

    expect(await screen.findByText('erreur')).toBeInTheDocument()
  })
})
