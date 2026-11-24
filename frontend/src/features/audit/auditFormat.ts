/**
 * French wording of audit events. Keys of `details` per action (tasks.md, journal of T034):
 * `organization.created {name}` · `organization.renamed {from_name, to_name}` ·
 * `invitation.* {role}` · `member.role_changed {from_role, to_role}` · `member.removed {role}` ·
 * `member.left {role}` · rule refusals add `reason: "last_owner"` ·
 * `permission.denied {permission}` (+ `role`, or `from_role` / `to_role`).
 */
import type {
  AuditAction,
  AuditDetailValue,
  AuditEvent,
  AuditOutcome,
  AuditTargetType,
} from '../../lib/api'
import { ROLE_LABELS, isRole } from '../../lib/roles'

export const ACTION_LABELS: Readonly<Record<AuditAction, string>> = {
  'organization.created': 'Organisation créée',
  'organization.renamed': 'Organisation renommée',
  'invitation.created': 'Invitation créée',
  'invitation.accepted': 'Invitation acceptée',
  'invitation.declined': 'Invitation refusée',
  'invitation.revoked': 'Invitation révoquée',
  'invitation.expired_on_use': 'Invitation expirée utilisée',
  'member.role_changed': 'Rôle modifié',
  'member.removed': 'Membre retiré',
  'member.left': "Départ de l'organisation",
  'permission.denied': 'Permission refusée',
}

export const OUTCOME_LABELS: Readonly<Record<AuditOutcome, string>> = {
  success: 'Réussi',
  denied: 'Refusé',
}

const TARGET_LABELS: Readonly<Record<AuditTargetType, string>> = {
  organization: 'Organisation',
  member: 'Membre',
  invitation: 'Invitation',
}

const PERMISSION_LABELS: Readonly<Record<string, string>> = {
  'organization:read': "voir l'organisation",
  'organization:leave': "quitter l'organisation",
  'organization:rename': "renommer l'organisation",
  'agents:use': 'utiliser les agents',
  'members:read': 'voir les membres',
  'members:manage': 'gérer les membres',
  'admins:manage': 'gérer les Admins',
  'owners:manage': 'gérer les Propriétaires',
  'audit:read': 'consulter le journal',
}

const REASON_LABELS: Readonly<Record<string, string>> = {
  last_owner: 'dernier Propriétaire',
}

function text(value: AuditDetailValue): string {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? 'oui' : 'non'
  return String(value)
}

function role(value: AuditDetailValue): string {
  return typeof value === 'string' && isRole(value) ? ROLE_LABELS[value] : text(value)
}

function lookup(labels: Readonly<Record<string, string>>, value: AuditDetailValue): string {
  return typeof value === 'string' && Object.hasOwn(labels, value) ? labels[value] : text(value)
}

/** "Membre 1a2b3c4d": the type, and the start of the id (the full id is in the title). */
export function targetLabel(event: AuditEvent): string {
  if (event.target_type === null) return '—'
  const type = TARGET_LABELS[event.target_type]
  if (event.target_type === 'organization' || event.target_id === null) return type
  return `${type} ${event.target_id.slice(0, 8)}`
}

/** The details as short French phrases, known keys first, in a stable order. */
export function detailLines(details: AuditEvent['details']): string[] {
  const lines: string[] = []
  const rest = { ...details }
  const take = (key: string): AuditDetailValue | undefined => {
    if (!Object.hasOwn(rest, key)) return undefined
    const value = rest[key]
    delete rest[key]
    return value
  }

  const fromName = take('from_name')
  const toName = take('to_name')
  if (fromName !== undefined || toName !== undefined) {
    lines.push(`« ${text(fromName ?? null)} » → « ${text(toName ?? null)} »`)
  }
  const name = take('name')
  if (name !== undefined) lines.push(`Nom : ${text(name)}`)
  const fromRole = take('from_role')
  const toRole = take('to_role')
  if (fromRole !== undefined || toRole !== undefined) {
    lines.push(`${role(fromRole ?? null)} → ${role(toRole ?? null)}`)
  }
  const single = take('role')
  if (single !== undefined) lines.push(`Rôle : ${role(single)}`)
  const permission = take('permission')
  if (permission !== undefined) lines.push(`Permission : ${lookup(PERMISSION_LABELS, permission)}`)
  const reason = take('reason')
  if (reason !== undefined) lines.push(`Motif : ${lookup(REASON_LABELS, reason)}`)
  for (const [key, value] of Object.entries(rest)) lines.push(`${key} : ${text(value)}`)
  return lines
}
