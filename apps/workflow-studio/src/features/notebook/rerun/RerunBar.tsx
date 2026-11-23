/**
 * The rerun bar: a rerun reached a change that was already applied, so it stopped instead of writing twice.
 * "Skip it and continue" reruns from there with on_applied: skip (the earlier output is kept);
 * "Propose it again" reruns from there with the cell in `reapply`, so a fresh diff waits for approval.
 */
import { useNb } from '../context'
import { fmtTime } from '../lib/cells'

export function RerunBar() {
  const { nb, live, actions, me, numberOf, fail } = useNb()
  const r = nb.runs[0]
  if (!r || live || r.status !== 'stopped_at_write' || r.stop?.reason !== 'applied_write' || !r.stop.cell_id) return null
  const cellId = r.stop.cell_id
  const cell = nb.cells.find((c) => c.id === cellId)
  const by = r.stop.applied_by ?? (cell?.latest?.output as { applied_by?: string } | undefined)?.applied_by
  const at = r.stop.applied_at ?? (cell?.latest?.output as { applied_at?: string } | undefined)?.applied_at
  return (
    <div className="nb-rerun" role="status" data-testid="nb-rerun-bar">
      <span>
        Run {r.n} stopped before cell {numberOf(cellId)}: that change was already applied
        {by ? ` by ${by}` : ''}
        {at ? ` · ${fmtTime(at)}` : ''}. Nothing was written twice.
      </span>
      <span className="nb-sp" />
      <button
        type="button"
        className="gl-btn"
        data-size="sm"
        disabled={actions.run.isPending}
        onClick={() => actions.run.mutate({ from_cell_id: cellId, on_applied: 'skip', triggered_by: me }, { onError: fail })}
        data-testid="nb-rerun-skip"
      >
        Skip it and continue
      </button>
      <button
        type="button"
        className="gl-btn"
        data-size="sm"
        data-variant="primary"
        disabled={actions.run.isPending}
        onClick={() => actions.run.mutate({ from_cell_id: cellId, reapply: [cellId], triggered_by: me }, { onError: fail })}
        data-testid="nb-rerun-reapply"
      >
        Propose it again
      </button>
    </div>
  )
}
