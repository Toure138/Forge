import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { OrganizationSwitcher } from './OrganizationSwitcher'

const ORGANIZATIONS = [
  { id: 'org-acme', name: 'Acme', role: 'admin' as const },
  { id: 'org-globex', name: 'Globex', role: 'guest' as const },
]

describe('OrganizationSwitcher', () => {
  it('lists organizations with role and switches on change', async () => {
    const onSwitch = vi.fn()
    render(
      <OrganizationSwitcher organizations={ORGANIZATIONS} activeId="org-acme" onSwitch={onSwitch} />,
    )

    const select = screen.getByRole('combobox', { name: 'Organisation' })
    expect(select).toHaveValue('org-acme')
    expect(screen.getByRole('option', { name: 'Acme (Admin)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Globex (Invité)' })).toBeInTheDocument()

    await userEvent.tab()
    expect(select).toHaveFocus()
    await userEvent.selectOptions(select, 'Globex (Invité)')

    expect(onSwitch).toHaveBeenCalledWith('org-globex')
  })

  it('is disabled while switching', () => {
    render(
      <OrganizationSwitcher
        organizations={ORGANIZATIONS}
        activeId="org-acme"
        onSwitch={vi.fn()}
        disabled
      />,
    )

    expect(screen.getByRole('combobox', { name: 'Organisation' })).toBeDisabled()
  })
})
