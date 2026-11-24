import { CreateOrganizationForm } from './CreateOrganizationForm'
import { MyInvitations } from './MyInvitations'

/**
 * Signed in, member of no organization (FR-17): pending invitations and organization creation,
 * nothing else. `onChanged` receives the organization just joined or created.
 */
export function NoOrganizationScreen({
  onChanged,
}: {
  onChanged: (organizationId: string) => void
}) {
  return (
    <div className="mx-auto max-w-2xl space-y-8 p-6">
      <h1 className="text-2xl font-bold">Vous n'êtes membre d'aucune organisation</h1>
      <p className="text-slate-600">
        Acceptez une invitation qu'on vous a adressée, ou créez votre propre organisation.
      </p>
      <MyInvitations onAccepted={(org) => onChanged(org.id)} />
      <section aria-labelledby="create-organization" className="space-y-3">
        <h2 id="create-organization" className="text-lg font-semibold">
          Créer une organisation
        </h2>
        <CreateOrganizationForm onCreated={(org) => onChanged(org.id)} />
      </section>
    </div>
  )
}
