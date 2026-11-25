/**
 * French messages for API error codes (FR-47). The UI never shows the server's own `message`
 * (English, generic, for developers): only `messageFor(code)`. A refusal never says anything about
 * other organizations: the server answers 404 for them, like for an id that does not exist.
 *
 * Independent of `api.ts`: `errorMessage` reads any value with a string `code`.
 */

export const GENERIC_ERROR = 'Une erreur est survenue. Réessayez dans un instant.'

const MESSAGES: Readonly<Record<string, string>> = {
  // Backend codes (plan, « Contrat API »).
  not_authenticated: 'Votre session a pris fin, reconnectez-vous.',
  csrf_failed: "La demande n'a pas pu être vérifiée. Rechargez la page et réessayez.",
  permission_denied: "Vous n'avez pas la permission d'effectuer cette action.",
  organization_required: 'Choisissez une organisation.',
  organization_not_found: "Cette organisation est introuvable, ou vous n'en êtes plus membre.",
  not_found: "Cet élément est introuvable. Il a peut-être déjà été modifié ou supprimé.",
  conversation_not_found: 'Cette conversation est introuvable. Commencez-en une nouvelle.',
  agent_not_found: 'Cet agent est introuvable.',
  already_member: "Cette personne est déjà membre de l'organisation.",
  invitation_already_pending: 'Une invitation est déjà en attente pour cette adresse.',
  last_owner: "L'organisation doit garder au moins un Propriétaire.",
  invitation_expired: 'Cette invitation a expiré.',
  // Client codes (lib/api.ts, chat).
  validation_error: 'Les informations saisies ne sont pas valides.',
  network_error: 'Le serveur est injoignable. Vérifiez votre connexion et réessayez.',
  agent_error: "L'agent a rencontré une erreur. Réessayez.",
}

/** French message for an error code; a generic message for any unknown code. */
export function messageFor(code: string): string {
  return Object.hasOwn(MESSAGES, code) ? MESSAGES[code] : GENERIC_ERROR
}

/** French message for a caught value: its string `code` if any, never its text. */
export function errorMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'code' in error) {
    const { code } = error
    if (typeof code === 'string') return messageFor(code)
  }
  return GENERIC_ERROR
}
