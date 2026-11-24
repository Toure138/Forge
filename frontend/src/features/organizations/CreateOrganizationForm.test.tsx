import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiError, callsTo, json, mockFetch } from '../../test/fetch'
import { CreateOrganizationForm } from './CreateOrganizationForm'

afterEach(() => vi.restoreAllMocks())

describe('CreateOrganizationForm', () => {
  it('creates an organization and notifies the parent', async () => {
    const created = { id: 'org-new', name: 'Initech', role: 'owner' }
    const spy = mockFetch({ 'POST /api/organizations': json(created, 201) })
    const onCreated = vi.fn()
    render(<CreateOrganizationForm onCreated={onCreated} />)

    await userEvent.type(screen.getByLabelText("Nom de l'organisation"), '  Initech  ')
    await userEvent.click(screen.getByRole('button', { name: "Créer l'organisation" }))

    expect(onCreated).toHaveBeenCalledWith(created)
    expect(callsTo(spy, 'POST /api/organizations')[0].body).toEqual({ name: 'Initech' })
  })

  it('shows validation error for an empty name', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    const onCreated = vi.fn()
    render(<CreateOrganizationForm onCreated={onCreated} />)

    await userEvent.type(screen.getByLabelText("Nom de l'organisation"), '   ')
    await userEvent.click(screen.getByRole('button', { name: "Créer l'organisation" }))

    expect(screen.getByRole('alert')).toHaveTextContent("Le nom de l'organisation est obligatoire.")
    expect(screen.getByLabelText("Nom de l'organisation")).toHaveAttribute('aria-invalid', 'true')
    expect(spy).not.toHaveBeenCalled()
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('shows a translated error when the server refuses', async () => {
    mockFetch({ 'POST /api/organizations': apiError(403, 'csrf_failed') })
    render(<CreateOrganizationForm onCreated={vi.fn()} />)

    await userEvent.type(screen.getByLabelText("Nom de l'organisation"), 'Initech')
    await userEvent.click(screen.getByRole('button', { name: "Créer l'organisation" }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "La demande n'a pas pu être vérifiée.",
    )
  })
})
