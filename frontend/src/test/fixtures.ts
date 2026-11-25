/** Test data shared by several tests (Acme: Alice Owner, Bob Admin, Carole Member, Dave Guest). */
import type { ActiveOrganization, Invitation, Member, Permission, Role } from '../lib/api'

const member = (name: string, role: Member['role']): Member => ({
  user_id: `u-${name.toLowerCase()}`,
  name,
  email: `${name.toLowerCase()}@acme.test`,
  picture_url: null,
  role,
  joined_at: '2026-11-01T09:00:00Z',
})

export const ALICE = member('Alice', 'owner')
export const BOB = member('Bob', 'admin')
export const CAROLE = member('Carole', 'member')
export const DAVE = member('Dave', 'guest')
export const MEMBERS = [ALICE, BOB, CAROLE, DAVE]

export const INVITATION: Invitation = {
  id: 'inv-erin',
  email: 'erin@example.test',
  role: 'member',
  invited_by: { user_id: BOB.user_id, name: 'Bob' },
  created_at: '2026-11-20T09:00:00Z',
  expires_at: '2026-11-27T09:00:00Z',
}

export const GUEST_PERMISSIONS: Permission[] = ['organization:leave', 'organization:read']
export const MEMBER_PERMISSIONS: Permission[] = ['agents:use', ...GUEST_PERMISSIONS]
export const ADMIN_PERMISSIONS: Permission[] = [
  ...MEMBER_PERMISSIONS,
  'audit:read',
  'members:manage',
  'members:read',
]
export const OWNER_PERMISSIONS: Permission[] = [
  ...ADMIN_PERMISSIONS,
  'admins:manage',
  'organization:rename',
  'owners:manage',
]

export const AS_ADMIN: ActiveOrganization = {
  id: 'org-acme',
  name: 'Acme',
  role: 'admin',
  permissions: ADMIN_PERMISSIONS,
}
export const AS_OWNER: ActiveOrganization = {
  ...AS_ADMIN,
  role: 'owner',
  permissions: OWNER_PERMISSIONS,
}

export const PERMISSIONS_OF: Readonly<Record<Role, Permission[]>> = {
  owner: OWNER_PERMISSIONS,
  admin: ADMIN_PERMISSIONS,
  member: MEMBER_PERMISSIONS,
  guest: GUEST_PERMISSIONS,
}
