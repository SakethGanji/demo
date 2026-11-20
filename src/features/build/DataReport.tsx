import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { DataFinding, DataFixEffect, DataReport as Report } from '@/shared/api/types'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { pct, sig4 } from '@/shared/lib/format'

const SEV_WORD = { high: 'high', medium: 'medium', low: 'low', info: 'info' } as const

/** A measured number as text: shares and rates as %, counts with separators, the rest to 4 significant digits. */
function num(k: string, v: string | number | null): string {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  if (/share|rate$|^rate/.test(k) && Math.abs(v) <= 1) return pct(v)
  if (Number.isInteger(v) && (/year|yr_|_yr|^yr/i.test(k) || (v >= 1800 && v <= 2100 && /from|to|built|renov/i.test(k)))) return String(v)  // A2 m7
  return Number.isInteger(v) ? v.toLocaleString() : sig4(v)
}

/** What one fix does, in a few words. */
function fixWords(t: DataFixEffect): string {
  const n = (x?: number) => (x == null ? '' : ` (${x.toLocaleString()})`)
  switch (t.op) {
    case 'to_missing': return `${t.column}: ${(t.values ?? []).join(', ')} → missing${t.flag ? ' + flag' : ''}${n(t.cells)}`
    case 'split_codes': return `${t.column}: codes ${(t.codes ?? []).join(', ')} → ${t.fill ?? 0} + ${t.column}_code${n(t.cells)}`
    case 'add_missing_flag': return `+ ${t.column}_was_missing`
    case 'drop_duplicates': return `drop duplicate rows${n(typeof t.rows === 'number' ? t.rows : undefined)}`
    case 'drop_near_duplicates': return `drop near-duplicate rows (identical but for one column)${n(typeof t.rows === 'number' ? t.rows : undefined)}`
    case 'drop_rows': return `drop ${t.reason ?? 'rows'}${n(Array.isArray(t.rows) ? t.rows.length : t.rows)}`
    case 'drop_columns': return `drop ${(t.columns ?? []).join(', ')}`
    case 'merge_classes': return `${t.column}: ${(t.classes ?? []).length} classes → '${t.into}'`
    case 'rescale': return `${t.column} × ${t.factor} from ${t.from} (+ flag)${n(t.cells)}`
    default: return t.op
  }
}

/**
 * D1 · Data Report: what is wrong with this table before any training, from GET /api/datasets/{id}/data-doctor.
 * High and medium findings are listed; low and info sit in a fold-out. Each opens to its rule, numbers and example
 * rows. One primary action applies the ticked fixes into a new dataset version (the original is never changed) and
 * saves the decision-time answers, which later runs on the dataset honour.
 */
export function DataReport({ dsId, onVersion }: { dsId: string; onVersion: (id: string, msg: string) => void }) {
  const rep = useFetch(() => api.datasets.doctor(dsId), [dsId], { pollMs: 4000, while: (d) => !!d?.findings.some((f) => f.status === 'running') })
  const r = rep.data?.dataset_id === dsId ? rep.data : null
  if (rep.err && !r) return <ErrorCard inline err={rep.err} />
  if (!r) return <Loading inline>Data Doctor: checking every column…</Loading>
  return <ReportCard key={dsId} r={r} onVersion={onVersion} reload={rep.reload} />
}

function ReportCard({ r, onVersion, reload }: { r: Report; onVersion: (id: string, msg: string) => void; reload: () => void }) {
  const [sel, setSel] = useState<Record<string, boolean>>(() => Object.fromEntries(r.findings.map((f) => [f.key, f.fix.selected])))
  const dt = r.decision_time
  // null = not answered yet: the column stays in every run (recorded as unanswered); never saved as an answer by itself
  const [known, setKnown] = useState<Record<string, boolean | null>>(() => Object.fromEntries(dt.columns.map((c) => [c.column, c.known ?? null])))
  const [fair, setFair] = useState<string[]>(dt.fairness_only)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState(dt.answered)
  useEffect(() => setSel((s) => ({ ...Object.fromEntries(r.findings.map((f) => [f.key, s[f.key] ?? f.fix.selected])) })), [r])

  const withFix = r.findings.filter((f) => f.fix.transforms.length > 0)
  const chosen = withFix.filter((f) => sel[f.key])
  const transforms = chosen.flatMap((f) => f.fix.transforms)
  const main = r.findings.filter((f) => f.severity === 'high' || f.severity === 'medium')
  const rest = r.findings.filter((f) => f.severity === 'low' || f.severity === 'info')
  const asks = dt.columns.length > 0 || dt.protected.length > 0
  const dropped = useMemo(() => [...dt.columns.filter((c) => known[c.column] === false).map((c) => c.column), ...fair], [dt, known, fair])
  const open = dt.columns.filter((c) => c.suspect && known[c.column] == null).map((c) => c.column)

  const saveAnswers = async () => { await api.datasets.decisionTime(r.dataset_id, known, fair); setSaved(true) }
  const confirm = async () => {
    setErr(''); setBusy('Saving…')
    try { await saveAnswers(); reload() } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const apply = async () => {
    setErr(''); setBusy(`Applying ${chosen.length} fix${chosen.length === 1 ? '' : 'es'}…`)
    try {
      const out = await api.datasets.applyFixes(r.dataset_id, transforms)
      const what = [out.rows_before !== out.rows_after ? `${(out.rows_before - out.rows_after).toLocaleString()} rows dropped` : '',
        out.columns_added.length ? `+ ${out.columns_added.join(', ')}` : '', out.columns_removed.length ? `− ${out.columns_removed.join(', ')}` : ''].filter(Boolean).join(' · ')
      onVersion(out.dataset.id, `${out.dataset.name}: ${chosen.length} fix${chosen.length === 1 ? '' : 'es'} applied to a copy${what ? ` (${what})` : ''}. The original is unchanged.`)
    } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }

  return (
    <div className="card data-report" data-testid="data-report">
      <div className="card-head">
        <div className="left">
          <h3 className="card-title">Data Report</h3>
          <span className="dr-counts" data-testid="dr-counts">
            {(['high', 'medium', 'low'] as const).map((s) => r.counts[s] > 0 && <span key={s} className={`dr-sev s-${s}`}>{r.counts[s]} {SEV_WORD[s]}</span>)}
            {r.counts.high + r.counts.medium + r.counts.low === 0 && <span className="dr-sev s-ok">nothing to fix</span>}
          </span>
          <span className="helper" style={{ fontSize: 12 }}>{r.rows.toLocaleString()} rows × {r.columns} · {r.target ? `y = ${r.target}` : 'no target yet'} · checked in {r.seconds < 0.1 ? '< 0.1' : r.seconds.toFixed(1)} s</span>
        </div>
        {chosen.length > 0
          ? <button className="btn primary" disabled={!!busy} onClick={apply} data-testid="dr-apply" title="Makes a new dataset version with these fixes; the original is never changed. Your decision-time answers are saved only with Save answers.">{busy || `Apply ${chosen.length} fix${chosen.length === 1 ? '' : 'es'} →`}</button>
          : asks && !saved ? <button className="btn primary" disabled={!!busy} onClick={confirm} data-testid="dr-confirm">{busy || 'Save answers'}</button> : null}
      </div>
      {r.lineage?.kind === 'data_doctor' && (
        <p className="helper dr-lineage" data-testid="dr-lineage">↳ Fixed version of <a href={`#/build?dataset=${encodeURIComponent(r.lineage.parent)}`} className="mono">{r.lineage.parent}</a>: {(r.lineage.fixes ?? []).map(fixWords).join(' · ')}</p>
      )}
      {err && <p className="helper dr-lineage" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      <div className="dr-list">
        {main.map((f) => <Finding key={f.key} f={f} on={!!sel[f.key]} toggle={() => setSel({ ...sel, [f.key]: !sel[f.key] })} />)}
        {main.length === 0 && <p className="helper dr-none">No high or medium findings: nothing stops a first model.</p>}
      </div>
      {asks && (
        <div className="dr-ask" data-testid="dr-decision">
          {dt.columns.length > 0 && <div className="dr-ask-row">
            <b>Known when you decide?</b>
            {dt.columns.map((c) => (
              <label key={c.column} className="dr-tick" title={c.reasons.join(' · ')} data-testid={`dr-known-${c.column}`}>
                <span className="mono">{c.column}</span>{c.auc != null && <small className="muted mono">AUC {c.auc.toFixed(3)} alone{c.basis === 'strength only' ? ' (strength only)' : ''}</small>}
                <select className="input sm" value={known[c.column] == null ? '' : known[c.column] ? 'yes' : 'no'}
                  onChange={(e) => { setKnown({ ...known, [c.column]: e.target.value === '' ? null : e.target.value === 'yes' }); setSaved(false) }}>
                  <option value="">not answered (kept)</option><option value="yes">known: keep</option><option value="no">not known: drop</option>
                </select>
              </label>
            ))}
          </div>}
          {dt.protected.length > 0 && <div className="dr-ask-row">
            <b>Fairness check only?</b>
            {dt.protected.map((c) => (
              <label key={c} className="dr-tick" data-testid={`dr-fair-${c}`}>
                <input type="checkbox" checked={fair.includes(c)} onChange={(e) => { setFair(e.target.checked ? [...fair, c] : fair.filter((x) => x !== c)); setSaved(false) }} />
                <span className="mono">{c}</span>
              </label>
            ))}
          </div>}
          <p className="helper dr-ask-rule">
            {saved ? <>Saved{dt.by ? ` by ${dt.by}` : ''}: {dropped.length ? <><span className="mono">{dropped.join(', ')}</span> left out of every run on this dataset.</> : 'no column dropped.'}</>
              : <>Not saved yet: runs keep every column. {dt.rule} Ticked fairness-only columns are left out too.</>}
            {open.length > 0 && <span data-testid="dr-unanswered"> Not answered: <span className="mono">{open.join(', ')}</span> {open.length === 1 ? 'stays' : 'stay'} in every run as an open leak question.</span>}
            {dt.protected.length > 0 && !saved && fair.length === 0 && <span data-testid="dr-fair-open"> Fairness question open: <span className="mono">{dt.protected.join(', ')}</span> {dt.protected.length === 1 ? 'stays' : 'stay'} in runs until you tick {dt.protected.length === 1 ? 'it' : 'them'}.</span>}
            {(chosen.length > 0 || saved) && <button className="btn sm ghost" onClick={confirm} disabled={!!busy || saved} style={{ marginLeft: 8 }} data-testid="dr-save">{saved ? 'Saved' : 'Save answers'}</button>}
          </p>
        </div>
      )}
      {rest.length > 0 && (
        <details className="an-adv dr-more" data-testid="dr-more">
          <summary className="micro-label">Also · {rest.length} low / info: {rest.map((f) => f.id.replace(/_/g, ' ')).join(', ')}</summary>
          <div className="dr-list">{rest.map((f) => <Finding key={f.key} f={f} on={!!sel[f.key]} toggle={() => setSel({ ...sel, [f.key]: !sel[f.key] })} />)}</div>
        </details>
      )}
      <p className="helper dr-foot">Rules, not a model (except label quality: a 60 s out-of-fold model + cleanlab). Severity: {r.severity_rule}</p>
    </div>
  )
}

/** One finding: the severity, the title and its fix in one line; open for the rule, the numbers and example rows. */
function Finding({ f, on, toggle }: { f: DataFinding; on: boolean; toggle: () => void }) {
  const ev = f.evidence
  return (
    <details className={`dr-item s-${f.severity}`} data-testid={`dr-${f.id}`}>
      <summary>
        <span className={`dr-sev s-${f.severity}`}>{f.severity}</span>
        <span className="dr-title">{f.title}{f.status === 'running' && <span className="muted"> (running)</span>}</span>
        {f.fix.transforms.length > 0
          ? <label className="dr-tick dr-fixtick" onClick={(e) => e.stopPropagation()} title={f.fix.transforms.map(fixWords).join('\n')}>
              <input type="checkbox" checked={on} onChange={toggle} data-testid={`dr-fix-${f.id}`} /> fix
            </label>
          : <span className="dr-kind muted">{f.fix.kind === 'question' ? 'question below' : f.fix.kind === 'advice' ? 'advice' : ''}</span>}
      </summary>
      <div className="dr-body">
        {f.fix.text && <p className="dr-fixtext"><b>Fix · </b>{f.fix.text}{f.fix.transforms.length > 0 && <span className="mono muted"> {f.fix.transforms.slice(0, 4).map(fixWords).join(' · ')}{f.fix.transforms.length > 4 ? ` · +${f.fix.transforms.length - 4}` : ''}</span>}</p>}
        {f.preview && f.preview.rows.length > 0 && <div className="table-wrap dr-ev" data-testid={`dr-preview-${f.id}`}>
          <p className="helper" style={{ margin: '0 0 4px', fontSize: 12 }}><b>Preview before Apply</b>: {f.preview.rows_rescaled.toLocaleString()} {f.preview.column} values change; {f.preview.rows_left.toLocaleString()} from that date already on the old scale stay.</p>
          <table className="table"><thead><tr><th>row</th><th>date</th><th>{f.preview.column} now</th><th>after the fix</th><th /></tr></thead>
          <tbody>{f.preview.rows.map((p) => <tr key={p.row}><td className="mono">{p.row}</td><td className="mono">{p.date}</td><td className="mono">{String(p.before)}</td><td className="mono">{String(p.after)}</td><td className="muted">{p.changed ? 'rescaled' : 'unchanged'}</td></tr>)}</tbody></table></div>}
        <p className="dr-rule"><span className="micro-label">Rule</span> {f.rule}</p>
        {Object.keys(f.numbers).length > 0 && <div className="dr-nums">{Object.entries(f.numbers).map(([k, v]) => <span key={k} className="chip"><span className="muted">{k}</span> {num(k, v)}</span>)}</div>}
        {ev.rows.length > 0 && <div className="table-wrap dr-ev"><table className="table">
          <thead><tr>{ev.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{ev.rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className="mono">{v == null ? '∅' : String(v).slice(0, 60)}</td>)}</tr>)}</tbody>
        </table></div>}
      </div>
    </details>
  )
}
