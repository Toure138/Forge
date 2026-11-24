import { type KeyboardEvent, type ReactNode, useId, useRef, useState } from 'react'
import { AuditLogPanel } from './features/audit/AuditLogPanel'
import { AuthProvider } from './features/auth/AuthProvider'
import { LoginPage } from './features/auth/LoginPage'
import { useAuth } from './features/auth/useAuth'
import { ChatPanel } from './features/chat/ChatPanel'
import { MembersPanel } from './features/members/MembersPanel'
import { MyInvitations } from './features/organizations/MyInvitations'
import { NoOrganizationScreen } from './features/organizations/NoOrganizationScreen'
import { OrganizationSettings } from './features/organizations/OrganizationSettings'
import { OrganizationSwitcher } from './features/organizations/OrganizationSwitcher'
import { useOrganizations } from './features/organizations/useOrganizations'
import type { ActiveOrganization, Permission, User } from './lib/api'
import { errorMessage, messageFor } from './lib/errors'
import { ROLE_LABELS } from './lib/roles'

export default function App() {
  return (
    <AuthProvider>
      <Root />
    </AuthProvider>
  )
}

/** Loading → login page (visitor) → signed-in app. */
function Root() {
  const auth = useAuth()
  switch (auth.status) {
    case 'loading':
      return <Loading />
    case 'error':
      return (
        <Failure message="Impossible de joindre Forge pour le moment." onRetry={auth.retry} />
      )
    case 'visitor':
      return <LoginPage reason={auth.reason} />
    case 'signed_in':
      return <SignedIn user={auth.user} logout={auth.logout} />
  }
}

function Loading() {
  return (
    <p role="status" className="p-6 text-slate-500">
      Chargement…
    </p>
  )
}

function Failure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="space-y-3 p-6">
      <p role="alert" className="text-red-600">
        {message}
      </p>
      <button type="button" onClick={onRetry} className="rounded-lg border px-3 py-1.5">
        Réessayer
      </button>
    </div>
  )
}

/** No organization → FR-17 screen; otherwise the organization's shell. */
function SignedIn({ user, logout }: { user: User; logout: () => Promise<void> }) {
  const { state, switchTo, refresh } = useOrganizations()

  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col">
      <Header user={user} logout={logout}>
        {state.status === 'ready' && (
          <OrganizationSwitcher
            organizations={state.organizations}
            activeId={state.active.id}
            onSwitch={switchTo}
            disabled={state.switching}
          />
        )}
      </Header>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && (
        <Failure message={messageFor(state.code)} onRetry={() => refresh()} />
      )}
      {state.status === 'none' && <NoOrganizationScreen onChanged={refresh} />}
      {state.status === 'ready' && (
        <>
          <div className="px-6 pt-4">
            <MyInvitations hideWhenEmpty onAccepted={() => refresh()} />
          </div>
          <OrganizationShell
            key={state.active.id}
            organization={state.active}
            currentUserId={user.id}
            onChanged={() => refresh()}
          />
        </>
      )}
    </div>
  )
}

function Header({
  user,
  logout,
  children,
}: {
  user: User
  logout: () => Promise<void>
  children: ReactNode
}) {
  const [error, setError] = useState<string | null>(null)
  const onLogout = () => {
    setError(null)
    logout().catch((err: unknown) => setError(errorMessage(err)))
  }

  return (
    <header className="flex flex-wrap items-center gap-4 border-b border-slate-200 px-6 py-3">
      <p className="text-xl font-bold">Forge</p>
      {children}
      <div className="ml-auto flex items-center gap-3 text-sm">
        <span>{user.name}</span>
        <button
          type="button"
          onClick={onLogout}
          className="rounded-lg border border-slate-300 px-3 py-1.5"
        >
          Déconnexion
        </button>
      </div>
      {error !== null && (
        <p role="alert" className="w-full text-sm text-red-600">
          {error}
        </p>
      )}
    </header>
  )
}

type TabId = 'chat' | 'members' | 'audit' | 'organization'

/** Tabs of the shell; `permission` hides the tab without it (FR-44: the server stays the authority). */
const TABS: readonly { id: TabId; label: string; permission: Permission | null }[] = [
  { id: 'chat', label: 'Chat', permission: null },
  { id: 'members', label: 'Membres', permission: 'members:read' },
  { id: 'audit', label: 'Journal', permission: 'audit:read' },
  { id: 'organization', label: 'Organisation', permission: null },
]

/**
 * Everything that belongs to the active organization. Keyed by its id in the parent: switching
 * organization remounts it, so no state (conversation, member list) passes from one to the other.
 */
function OrganizationShell({
  organization,
  currentUserId,
  onChanged,
}: {
  organization: ActiveOrganization
  currentUserId: string
  onChanged: () => void
}) {
  const tabs = TABS.filter(
    (t) => t.permission === null || organization.permissions.includes(t.permission),
  )
  const [selected, setSelected] = useState<TabId>('chat')
  const current = tabs.some((t) => t.id === selected) ? selected : 'chat'
  const tabRefs = useRef(new Map<TabId, HTMLButtonElement>())
  const baseId = useId()
  const tabId = (id: TabId) => `${baseId}-tab-${id}`
  const panelId = (id: TabId) => `${baseId}-panel-${id}`

  const select = (id: TabId) => {
    setSelected(id)
    tabRefs.current.get(id)?.focus()
  }

  // WAI-ARIA tabs pattern: arrows, Home and End move and select (automatic activation).
  const onKeyDown = (e: KeyboardEvent) => {
    const index = tabs.findIndex((t) => t.id === current)
    const last = tabs.length - 1
    const next =
      e.key === 'ArrowRight'
        ? index === last
          ? 0
          : index + 1
        : e.key === 'ArrowLeft'
          ? index === 0
            ? last
            : index - 1
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? last
              : null
    if (next === null) return
    e.preventDefault()
    select(tabs[next].id)
  }

  return (
    <main className="flex min-h-0 flex-1 flex-col gap-4 p-6">
      <div>
        <h1 className="text-2xl font-bold">{organization.name}</h1>
        <p className="text-sm text-slate-500">Votre rôle : {ROLE_LABELS[organization.role]}</p>
      </div>
      <div
        role="tablist"
        aria-label="Sections de l'organisation"
        onKeyDown={onKeyDown}
        className="flex gap-1 border-b border-slate-200"
      >
        {tabs.map((t) => (
          <button
            key={t.id}
            ref={(el) => {
              if (el === null) tabRefs.current.delete(t.id)
              else tabRefs.current.set(t.id, el)
            }}
            type="button"
            role="tab"
            id={tabId(t.id)}
            aria-selected={t.id === current}
            aria-controls={panelId(t.id)}
            tabIndex={t.id === current ? 0 : -1}
            onClick={() => select(t.id)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm ${
              t.id === current
                ? 'border-indigo-600 font-medium text-indigo-700'
                : 'border-transparent text-slate-600 hover:text-slate-900'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={panelId(current)}
        aria-labelledby={tabId(current)}
        tabIndex={0}
        className="min-h-0 flex-1 focus:outline-none"
      >
        {current === 'chat' && (
          <ChatPanel orgId={organization.id} permissions={organization.permissions} />
        )}
        {current === 'members' && (
          <MembersPanel
            organization={organization}
            currentUserId={currentUserId}
            onOwnRoleChanged={onChanged}
          />
        )}
        {current === 'audit' && <AuditLogPanel orgId={organization.id} />}
        {current === 'organization' && (
          <OrganizationSettings organization={organization} onChanged={onChanged} />
        )}
      </div>
    </main>
  )
}
