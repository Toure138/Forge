import type { OrganizationSummary } from '../../lib/api'
import { ROLE_LABELS } from '../../lib/roles'

interface Props {
  organizations: OrganizationSummary[]
  activeId: string
  onSwitch: (organizationId: string) => void
  disabled?: boolean
}

/** Native select: keyboard and screen readers work out of the box. */
export function OrganizationSwitcher({ organizations, activeId, onSwitch, disabled = false }: Props) {
  return (
    <select
      aria-label="Organisation"
      className="rounded border border-slate-300 bg-white px-2 py-1 text-sm disabled:opacity-60"
      value={activeId}
      disabled={disabled}
      onChange={(e) => onSwitch(e.target.value)}
    >
      {organizations.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name} ({ROLE_LABELS[o.role]})
        </option>
      ))}
    </select>
  )
}
