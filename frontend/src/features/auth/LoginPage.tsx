import { useEffect, useState } from 'react'
import { LOGIN_URL } from '../../lib/api'
import type { VisitorReason } from './useAuth'

const LOGIN_ERROR_PARAM = 'login_error'

/** `?login_error=` set by the backend's redirect after a refused sign-in. */
const LOGIN_ERRORS: Readonly<Record<string, string>> = {
  failed: 'La connexion a échoué. Réessayez.',
  cancelled: 'La connexion a été annulée.',
  unavailable: 'La connexion avec Google est indisponible pour le moment. Réessayez plus tard.',
}

function readLoginError(): string | null {
  const value = new URLSearchParams(window.location.search).get(LOGIN_ERROR_PARAM)
  if (value === null) return null
  return Object.hasOwn(LOGIN_ERRORS, value) ? LOGIN_ERRORS[value] : LOGIN_ERRORS.failed
}

/** Removes `login_error` from the address bar, so a reload does not show the message again. */
function cleanLoginError(): void {
  const url = new URL(window.location.href)
  if (!url.searchParams.has(LOGIN_ERROR_PARAM)) return
  url.searchParams.delete(LOGIN_ERROR_PARAM)
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
}

/** The only screen a visitor sees (FR-42). */
export function LoginPage({ reason }: { reason: VisitorReason }) {
  const [loginError] = useState(readLoginError)

  useEffect(cleanLoginError, [])

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-3xl font-bold text-slate-900">Forge</h1>
        {reason === 'session_ended' && (
          <p role="status" className="rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Votre session a pris fin, reconnectez-vous.
          </p>
        )}
        {loginError !== null && (
          <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
            {loginError}
          </p>
        )}
        <a
          href={LOGIN_URL}
          className="block rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
        >
          Se connecter avec Google
        </a>
      </div>
    </main>
  )
}
