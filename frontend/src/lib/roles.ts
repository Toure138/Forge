/**
 * Roles as shown in the UI, and which roles the user may manage. Mirrors
 * `ROLE_MANAGEMENT_PERMISSION` / `can_manage_role` of backend/app/core/authz.py. It only filters
 * what the UI offers (FR-44): the server stays the authority and re-checks every request.
 */
import type { Permission, Role } from './api'

/** Highest first. */
export const ROLES: readonly Role[] = ['owner', 'admin', 'member', 'guest']

export const ROLE_LABELS: Readonly<Record<Role, string>> = {
  owner: 'Propriétaire',
  admin: 'Admin',
  member: 'Membre',
  guest: 'Invité',
}

/** "To manage a holder of role X, you need…" */
const ROLE_MANAGEMENT_PERMISSION: Readonly<Record<Role, Permission>> = {
  guest: 'members:manage',
  member: 'members:manage',
  admin: 'admins:manage',
  owner: 'owners:manage',
}

export function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value)
}

/** Invite as, remove, revoke the invitation of, or change from/to `role`. */
export function canManageRole(permissions: readonly Permission[], role: Role): boolean {
  return (
    permissions.includes('members:manage') &&
    permissions.includes(ROLE_MANAGEMENT_PERMISSION[role])
  )
}

/** The roles the user may give (invitation, role change). */
export function manageableRoles(permissions: readonly Permission[]): Role[] {
  return ROLES.filter((role) => canManageRole(permissions, role))
}
