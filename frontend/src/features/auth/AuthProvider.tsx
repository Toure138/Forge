import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  type User,
  getSession,
  logout as apiLogout,
  setCsrfToken,
  setOnUnauthenticated,
} from '../../lib/api'
import { type AuthState, AuthContext, type VisitorReason } from './useAuth'

type SessionState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'visitor'; reason: VisitorReason }
  | { status: 'signed_in'; user: User }

function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}

/**
 * Loads the session (`GET /api/auth/session`) and keeps its CSRF token in the API client's memory.
 * Any 401 afterwards (expired or revoked session) turns a signed-in user into a visitor with the
 * reason `session_ended` (FR-46, AC-55).
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    setOnUnauthenticated(() => {
      setCsrfToken(null)
      setSession((prev) =>
        prev.status === 'signed_in'
          ? { status: 'visitor', reason: 'session_ended' }
          : prev.status === 'visitor'
            ? prev
            : { status: 'visitor', reason: null },
      )
    })
    return () => setOnUnauthenticated(null)
  }, [])

  useEffect(() => {
    let current = true
    getSession()
      .then((info) => {
        if (!current) return
        setCsrfToken(info.csrf_token)
        setSession({ status: 'signed_in', user: info.user })
      })
      .catch((error: unknown) => {
        if (!current) return
        setSession((prev) =>
          isUnauthenticated(error)
            ? prev.status === 'visitor'
              ? prev
              : { status: 'visitor', reason: null }
            : { status: 'error' },
        )
      })
    return () => {
      current = false
    }
  }, [attempt])

  const retry = useCallback(() => {
    setSession({ status: 'loading' })
    setAttempt((n) => n + 1)
  }, [])

  const logout = useCallback(async () => {
    try {
      await apiLogout()
    } catch (error) {
      // Already signed out server-side: nothing left to revoke.
      if (!isUnauthenticated(error)) throw error
    }
    setCsrfToken(null)
    setSession({ status: 'visitor', reason: null })
  }, [])

  const value = useMemo<AuthState>(() => {
    if (session.status === 'error') return { status: 'error', retry }
    if (session.status === 'signed_in') return { ...session, logout }
    return session
  }, [session, retry, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
