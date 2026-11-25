import { describe, expect, it } from 'vitest'
import { GENERIC_ERROR, errorMessage, messageFor } from './errors'

// Every code of the plan (« Contrat API »), plus the client codes of lib/api.ts.
const BACKEND_CODES = [
  'not_authenticated',
  'csrf_failed',
  'permission_denied',
  'organization_required',
  'organization_not_found',
  'not_found',
  'conversation_not_found',
  'agent_not_found',
  'already_member',
  'invitation_already_pending',
  'last_owner',
  'invitation_expired',
]
const CLIENT_CODES = ['validation_error', 'network_error', 'agent_error']

describe('messageFor', () => {
  it('maps every backend error code to a French message', () => {
    const messages = [...BACKEND_CODES, ...CLIENT_CODES].map(messageFor)

    for (const message of messages) {
      expect(message).not.toBe(GENERIC_ERROR)
      expect(message).toMatch(/[a-zà-ÿ]/i)
    }
    // Each code has its own message.
    expect(new Set(messages).size).toBe(messages.length)
    expect(messageFor('last_owner')).toBe("L'organisation doit garder au moins un Propriétaire.")
    expect(messageFor('already_member')).toBe("Cette personne est déjà membre de l'organisation.")
  })

  it('falls back to a generic message for unknown codes', () => {
    expect(messageFor('something_new')).toBe(GENERIC_ERROR)
    expect(messageFor('http_error')).toBe(GENERIC_ERROR)
    expect(messageFor('constructor')).toBe(GENERIC_ERROR)
  })

  it('never exposes the raw message', () => {
    const raw = 'psycopg.errors.UniqueViolation: duplicate key acme-secret'
    const withCode = Object.assign(new Error(raw), { code: 'last_owner' })

    expect(errorMessage(withCode)).toBe(messageFor('last_owner'))
    expect(errorMessage(new Error(raw))).toBe(GENERIC_ERROR)
    expect(errorMessage(raw)).toBe(GENERIC_ERROR)
    expect(errorMessage({ code: 42, message: raw })).toBe(GENERIC_ERROR)
  })
})
