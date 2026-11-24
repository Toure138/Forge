import { messageFor } from '../../lib/errors'
import { formatDateTime } from '../../lib/format'
import { ACTION_LABELS, OUTCOME_LABELS, detailLines, targetLabel } from './auditFormat'
import { useAuditLog } from './useAuditLog'

const cell = 'px-3 py-2 text-left align-top'

/** Read-only audit log of the active organization (FR-40, AC-48). */
export function AuditLogPanel({ orgId }: { orgId: string }) {
  const { state, loadOlder } = useAuditLog(orgId)

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Journal d'audit</h2>
      {state.status === 'loading' && (
        <p className="text-sm text-slate-500">Chargement du journal…</p>
      )}
      {state.status === 'error' && (
        <p role="alert" className="text-sm text-red-600">
          {messageFor(state.code)}
        </p>
      )}
      {state.status === 'ready' && state.items.length === 0 && (
        <p className="text-sm text-slate-500">Aucun événement dans le journal.</p>
      )}
      {state.status === 'ready' && state.items.length > 0 && (
        <div className="overflow-x-auto">
          <table aria-label="Journal d'audit" className="w-full text-sm">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr>
                <th className={cell}>Date</th>
                <th className={cell}>Auteur</th>
                <th className={cell}>Action</th>
                <th className={cell}>Cible</th>
                <th className={cell}>Résultat</th>
                <th className={cell}>Détails</th>
              </tr>
            </thead>
            <tbody>
              {state.items.map((event) => (
                <tr key={event.id} className="border-b border-slate-100">
                  <td className={`${cell} whitespace-nowrap`}>
                    <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
                  </td>
                  <td className={cell}>{event.actor.name ?? 'Inconnu'}</td>
                  <td className={cell}>{ACTION_LABELS[event.action]}</td>
                  <td className={cell} title={event.target_id ?? undefined}>
                    {targetLabel(event)}
                  </td>
                  <td className={cell}>
                    <span
                      className={
                        event.outcome === 'denied' ? 'font-medium text-red-700' : 'text-green-700'
                      }
                    >
                      {OUTCOME_LABELS[event.outcome]}
                    </span>
                  </td>
                  <td className={cell}>
                    {detailLines(event.details).map((line) => (
                      <div key={line}>{line}</div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {state.status === 'ready' && state.moreError !== null && (
        <p role="alert" className="text-sm text-red-600">
          {messageFor(state.moreError)}
        </p>
      )}
      {state.status === 'ready' && state.nextBeforeId !== null && (
        <button
          type="button"
          disabled={state.loadingMore}
          onClick={() => void loadOlder()}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-50"
        >
          Plus ancien
        </button>
      )}
    </section>
  )
}
