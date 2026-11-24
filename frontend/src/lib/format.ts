/** Dates of the API (ISO 8601 strings) shown in French. */

const DATE_TIME = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
const DATE = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long' })

export function formatDateTime(iso: string): string {
  return DATE_TIME.format(new Date(iso))
}

export function formatDate(iso: string): string {
  return DATE.format(new Date(iso))
}
