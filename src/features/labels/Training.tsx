import { useEffect, useState } from 'react'
import { labels, countsOf, type Snapshot } from '@/shared/api/labels'
import { useFetch } from '@/shared/hooks/useFetch'
import { Badge, CardHead, CopyId } from '@/shared/components/DataKit'
import { day, int } from '@/shared/lib/format'
import './labels.css'

export type LabelsPin = { snapshot_id: string; holdout: 'original' | 'corrected'; stale: 'exclude' | 'keep'; features: false }

/** LB-C: the run form's "Labels: none | snapshot …" (§7.8). Lists the snapshots of this dataset's label layers on the
 *  table; the overlay applies them to the training rows only, the holdout keeps its original labels. */
export function LabelPin({ root, table, value, onChange }: { root: string; table?: string | null; value: LabelsPin | null; onChange: (v: LabelsPin | null) => void }) {
  const [snaps, setSnaps] = useState<(Snapshot & { layer: string })[] | null>(null)
  useEffect(() => {
    let live = true
    labels.layers(root).then(async (ls) => {
      const mine = ls.filter((l) => !table || l.table_name === table)
      const all = (await Promise.all(mine.map((l) => labels.snapshots(l.id).then((ss) => ss.map((s) => ({ ...s, layer: l.name }))).catch(() => [])))).flat()
      if (live) setSnaps(all.filter((s) => s.state !== 'purged').sort((a, b) => b.created_at.localeCompare(a.created_at)))
    }).catch(() => live && setSnaps([]))
    return () => { live = false }
  }, [root, table])
  if (!snaps?.length) return null
  return (
    <label className="helper row train-labels" title="Pin a frozen label snapshot: it is overlaid on the training rows at read time (nothing copied); the holdout keeps its original labels">labels
      <select className="select" value={value?.snapshot_id ?? ''} onChange={(e) => onChange(e.target.value ? { snapshot_id: e.target.value, holdout: 'original', stale: 'exclude', features: false } : null)} data-testid="train-labels">
        <option value="">none (the version's own)</option>
        {snaps.map((s) => <option key={s.id} value={s.id}>{s.layer} · {s.id.slice(0, 10)} · {int(countsOf(s).applied)} labels · {s.state}</option>)}
      </select>
    </label>
  )
}

type Pinned = { snapshot_id?: string; label_snapshot_id?: string; snapshot_sha256?: string; label_snapshot_sha256?: string; overlay_sha256?: string; holdout?: string; stale?: string
  rows_applied?: number; columns?: string[]; layer?: string; overlay_spec?: { rows_applied?: number; holdout?: string; stale?: string; columns?: string[] } | string }

/** Find the label pin of a run (its config `labels`, or the API's `labels` / `label_snapshot` section) in any of the
 *  shapes the run / card answers carry. */
export function pinOf(x: unknown): Pinned | null {
  if (!x || typeof x !== 'object') return null
  const o = x as Record<string, unknown>
  const cands = [o.labels, (o.config as Record<string, unknown> | undefined)?.labels, o.label_snapshot, (o.inputs as Record<string, unknown> | undefined)?.labels]
  for (const c of cands) if (c && typeof c === 'object' && ((c as Pinned).snapshot_id || (c as Pinned).label_snapshot_id)) return c as Pinned
  const ins = (o.inputs as { label_snapshot_id?: string }[] | undefined)
  if (Array.isArray(ins)) { const r = ins.find((i) => i?.label_snapshot_id); if (r) return r as Pinned }
  return null
}

/** LB-C: the "Labels" section of a run page and a model card: which snapshot, its sha, the overlay's sha, and that it
 *  applied to the training rows only (the holdout kept its original labels unless 'corrected' was chosen). */
export function RunLabels({ pin, testId = 'run-labels' }: { pin: Pinned | null; testId?: string }) {
  const sid = pin?.snapshot_id ?? pin?.label_snapshot_id ?? null
  const snap = useFetch(sid ? () => labels.getSnapshot(sid) : null, [sid])
  if (!pin || !sid) return null
  const spec = typeof pin.overlay_spec === 'string' ? (() => { try { return JSON.parse(pin.overlay_spec as string) } catch { return {} } })() : pin.overlay_spec ?? {}
  const holdout = pin.holdout ?? spec.holdout ?? 'original'
  const applied = pin.rows_applied ?? spec.rows_applied
  const s = snap.data
  return (
    <div className="card" data-testid={testId}>
      <CardHead title="Labels" badge={<Badge status={s?.state ?? 'pinned'}>{s?.state ?? 'pinned'}</Badge>} sub="a label snapshot was overlaid on the data at read time; the version itself is unchanged" />
      <div className="card-body lbl-pin">
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <span className="micro-label">snapshot</span><CopyId value={sid} testId={`${testId}-snapshot`} />
          <span className="micro-label">sha</span><CopyId value={pin.snapshot_sha256 ?? pin.label_snapshot_sha256 ?? s?.snapshot_sha256} n={8} />
          {pin.overlay_sha256 && <><span className="micro-label">overlay</span><CopyId value={pin.overlay_sha256} n={8} /></>}
          {s && <span className="muted">through event {int(s.through_seq)} · {int(countsOf(s).applied)} labels · {s.created_by} {day(s.created_at)}</span>}
        </div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }} data-testid={`${testId}-scope`}>
          <span className="pt-chip">✓ applied to training rows only{applied != null ? ` (${int(applied)} rows)` : ''}</span>
          <span className="pt-chip">{holdout === 'corrected' ? 'holdout: corrected (people only; metrics on both)' : 'holdout: original labels, untouched'}</span>
          <span className="pt-chip">stale labels: {pin.stale ?? spec.stale ?? 'exclude'}d</span>
        </div>
      </div>
    </div>
  )
}
