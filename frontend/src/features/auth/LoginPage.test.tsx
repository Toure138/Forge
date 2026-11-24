import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'

afterEach(() => {
  window.history.replaceState(null, '', '/')
  vi.restoreAllMocks()
})

describe('LoginPage', () => {
  it('shows only the Google sign-in link', async () => {
    render(<LoginPage reason={null} />)

    const link = screen.getByRole('link', { name: 'Se connecter avec Google' })
    expect(link).toHaveAttribute('href', '/api/auth/login')
    expect(screen.getAllByRole('link')).toHaveLength(1)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await userEvent.tab()
    expect(link).toHaveFocus()
  })

  it('shows session-ended message', () => {
    render(<LoginPage reason="session_ended" />)

    expect(screen.getByRole('status')).toHaveTextContent(
      'Votre session a pris fin, reconnectez-vous.',
    )
    expect(screen.getByRole('link', { name: 'Se connecter avec Google' })).toBeInTheDocument()
  })

  it('shows login failure message and cleans the URL', () => {
    window.history.replaceState(null, '', '/?login_error=failed&keep=1#top')
    const replaceState = vi.spyOn(window.history, 'replaceState')

    render(<LoginPage reason={null} />)

    expect(screen.getByRole('alert')).toHaveTextContent('La connexion a échoué. Réessayez.')
    expect(replaceState).toHaveBeenCalled()
    expect(window.location.search).toBe('?keep=1')
    expect(window.location.hash).toBe('#top')
  })

  it.each([
    ['cancelled', 'La connexion a été annulée.'],
    ['unavailable', 'La connexion avec Google est indisponible pour le moment. Réessayez plus tard.'],
    ['<script>', 'La connexion a échoué. Réessayez.'],
  ])('translates login_error=%s', (value, message) => {
    window.history.replaceState(null, '', `/?login_error=${encodeURIComponent(value)}`)

    render(<LoginPage reason={null} />)

    expect(screen.getByRole('alert')).toHaveTextContent(message)
    expect(window.location.search).toBe('')
  })
})
