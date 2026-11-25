import { createContext, useContext } from 'react'
import type { User } from '../../lib/api'

/** Why the user sees the login page: `session_ended` after a 401 while signed in (FR-46). */
export type VisitorReason = 'session_ended' | null

export type AuthState =
  | { status: 'loading' }
  | { status: 'error'; retry: () => void }
  | { status: 'visitor'; reason: VisitorReason }
  | { status: 'signed_in'; user: User; logout: () => Promise<void> }

export const AuthContext = createContext<AuthState | null>(null)

/** The session state provided by `AuthProvider`. */
export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (auth === null) throw new Error('useAuth must be used inside <AuthProvider>')
  return auth
}
