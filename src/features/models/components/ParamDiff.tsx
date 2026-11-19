import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run } from '@/shared/api/types'
import { fmtScore } from '@/shared/lib/metrics'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { RUN_COLORS } from '../RunList'

type Row = { section: string; key: string; vals: string[] }

const show = (v: unknown): string => {
  if (v === undefined) return '∅'
  if (v === null) return 'None'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : fmtScore(v)
  if (typeof v === 'string') return v
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  const s = JSON.stringify(v)
  return s.length > 140 ? `${s.slice(0, 138)}…` : s
}

/** Dotted paths of a JSON object, to `depth` levels; deeper values are shown as JSON. */
function flatten(o: unknown, prefix = '', depth = 3, out: Record<string, unknown> = {}) {
  if (o && typeof o === 'object' && !Array.isArray(o) && depth > 0 && Object.keys(o).length) {
    for (const [k, v] of Object.entries(o)) flatten(v, prefix ? `${prefix}.${k}` : k, depth - 1, out)
  } else if (prefix) out[prefix] = o
  return out
}

function rowsFor(runs: Run[]): Row[] {
  const sections: [string, (r: Run) => Record<string, unknown>][] = [
    ['data', (r) => ({ dataset_id: r.dataset_id, label: r.label, train_rows: r.data?.train_rows, holdout_rows: r.data?.holdout_rows, features: r.features?.length, drop_columns: r.config.drop_columns, column_types: r.config.column_types })],
    ['TabularPredictor(…)', (r) => flatten(r.resolved?.predictor_kwargs ?? {})],
    ['.fit(…)', (r) => { const fk = { ...(r.resolved?.fit_kwargs ?? {}) }; delete (fk as Record<string, unknown>).callbacks; return flatten(fk) }],
    ['ENSEMBLE', (r) => ({ calibrate_threshold: r.config.calibrate_threshold, custom_text_models: r.config.custom_text_models, feature_importance: r.config.feature_importance })],
    ['result', (r) => ({
      problem_type: r.problem_type, eval_metric: r.eval_metric?.name, best_model: r.best_model, models: r.leaderboard?.length,
      fit_seconds: r.fit_seconds, 'threshold.value': r.threshold?.value, 'threshold.source': r.threshold?.source,
      ...Object.fromEntries(Object.entries(r.holdout?.metrics ?? {}).map(([k, v]) => [`holdout.${k}`, v])),
    })],
  ]
  const out: Row[] = []
  for (const [section, get] of sections) {
    const maps = runs.map(get)
    const keys = [...new Set(maps.flatMap((m) => Object.keys(m)))]
    keys.forEach((k) => out.push({ section, key: k, vals: maps.map((m) => show(m[k])) }))
  }
  return out
}

/** Every argument that went into each selected run (the exact resolved fit call) and what came out, side by side;
 *  cells that differ from the first run are tinted. */
export function ParamDiff({ runs }: { runs: Run[] }) {
  const ids = runs.slice(0, 6).map((r) => r.id)
  const [full, setFull] = useState<Record<string, Run>>({})
  const [all, setAll] = useState(false)
  useEffect(() => {
    let alive = true
    ids.filter((id) => !full[id]).forEach((id) => api.runs.get(id).then((r) => alive && setFull((m) => ({ ...m, [id]: r }))).catch(() => {}))
    return () => { alive = false }
  }, [ids.join()]) // eslint-disable-line react-hooks/exhaustive-deps
  if (ids.length < 2) return <div className="card card-body helper" data-testid="diff-empty">Select two or more runs in the Runs tab to diff their parameters.</div>
  const rs = ids.map((id) => full[id]).filter(Boolean)
  if (rs.length < ids.length) return <div className="card card-body row"><ResonanceWave /> Reading the resolved fit calls…</div>
  const rows = rowsFor(rs)
  const differ = (r: Row) => r.vals.some((v) => v !== r.vals[0])
  const visible = all ? rows : rows.filter(differ)
  const nDiff = rows.filter(differ).length
  let last = ''
  return (
    <div className="card" data-testid="param-diff">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Parameter Diff</h3><span className="helper" style={{ fontSize: 12 }}>vs <span className="mono">{rs[0].id}</span> · {nDiff} of {rows.length} entries differ</span></div>
        <div className="seg"><button className={!all ? 'on' : ''} onClick={() => setAll(false)}>differences ({nDiff})</button><button className={all ? 'on' : ''} onClick={() => setAll(true)}>everything ({rows.length})</button></div>
      </div>
      <div className="table-wrap"><table className="table diff-table">
        <thead><tr><th>argument</th>{rs.map((r, i) => <th key={r.id}><i className="run-dot" style={{ background: RUN_COLORS[i % RUN_COLORS.length] }} /><span className="nocase-h">{r.id}</span></th>)}</tr></thead>
        <tbody>
          {visible.map((r) => {
            const head = r.section !== last ? r.section : null
            last = r.section
            return [
              head && <tr key={`h-${r.section}`} className="diff-sec"><td colSpan={rs.length + 1}><span className="micro-label">{head}</span></td></tr>,
              <tr key={`${r.section}.${r.key}`} data-testid={`diff-${r.key}`}>
                <td className="mono diff-key">{r.key}</td>
                {r.vals.map((v, i) => <td key={i} className={`table-cell diff-v ${i > 0 && v !== r.vals[0] ? 'changed' : ''}`} title={v}>{v}</td>)}
              </tr>,
            ]
          })}
          {!visible.length && <tr><td colSpan={rs.length + 1} className="helper" style={{ padding: 20 }}>These runs were trained with identical arguments.</td></tr>}
        </tbody>
      </table></div>
      <p className="helper" style={{ fontSize: 12, padding: 'var(--space-8) var(--space-16) var(--space-12)' }}>
        From each run's <span className="mono">resolved</span> record: the exact <span className="mono">TabularPredictor(**predictor_kwargs).fit(train, **fit_kwargs)</span> AutoGluon received (presets expanded by AutoGluon itself, our progress callback left out), the data it saw, and what it produced. ∅ = not set (AutoGluon's default).
      </p>
    </div>
  )
}
