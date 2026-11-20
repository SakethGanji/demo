import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { DatasetSplits } from '@/shared/api/types'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { fromServer, type Dataset } from '../datasets'
import { SplitCard } from './SplitCard'
import { RangeCard } from './RangeCard'
import { useWidth } from '@/shared/hooks/useWidth'
import { effectiveType, inferTask, labelClasses, labelOptions, type Choices, type ColType, type Task } from '../wizard'
import { sig4 } from '@/shared/lib/format'

const TYPE_LABEL: Record<ColType, string> = { number: 'number', category: 'category', text: 'text', id: 'ignore' }
const AG_RAW: Record<ColType, string> = { number: 'float', category: 'object', text: 'object', id: '—' }

/** Step 2: which column to predict, what kind of problem that is, and how each column is read (AutoGluon feature_metadata). */
/** Is y = label confirmed for this server dataset? (Mock datasets have no server split: nothing to confirm.) */
export const targetConfirmed = (ds: Dataset, label: string) => !ds.id || (ds.target === label && ds.profile?.target_source !== 'guessed')

export function TargetStep({ ds, ch, set, onConfirmed }: { ds: Dataset; ch: Choices; set: (p: Partial<Choices>) => void; onConfirmed?: (d: Dataset) => void }) {
  const inferred = inferTask(ds, ch.label)
  const task: Task | null = ch.problemType ?? inferred
  const classes = labelClasses(ds, ch.label)
  const col = ds.profile?.columns.find((c) => c.name === ch.label)
  const stats = ds.profile && ch.label === ds.profile.target ? ds.profile.target_stats : col?.stats && 'mean' in col.stats && col.type === 'number' ? col.stats : null
  const K = classes.length
  const unique = col?.unique ?? K
  const allowed: Task[] = unique === 2 ? ['binary', 'multiclass'] : col?.type === 'number' ? (unique <= 50 ? ['multiclass', 'regression'] : ['regression']) : ['multiclass']
  if (!ds.profile) allowed.splice(0, allowed.length, ...(K === 2 ? ['binary', 'multiclass'] as Task[] : K > 2 ? ['multiclass'] as Task[] : ['regression'] as Task[]))
  const maxShare = Math.max(...classes.map((c) => c.share), 1e-4)
  const minShare = Math.min(...classes.map((c) => c.share), 1)
  const options = labelOptions(ds)
  const pickLabel = (label: string) => {
    const types = { ...ch.types }
    delete types[label]
    set({ label, problemType: null, types, split: null, range: null })
  }
  const setType = (name: string, t: ColType) => {
    const c = ds.cols.find((x) => x.name === name)!
    const types = { ...ch.types }
    if (t === c.type) delete types[name]; else types[name] = t
    set({ types })
  }
  const feats = ds.cols.filter((c) => c.name !== ch.label)
  const used = feats.filter((c) => effectiveType(c, ch) !== 'id')
  const text = used.filter((c) => effectiveType(c, ch) === 'text').map((c) => c.name)
  const changed = Object.keys(ch.types).filter((k) => k !== ch.label)

  return (
    <div className="page">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.55fr)', alignItems: 'start' }}>
        <div className="stack">
          <div className="card" data-testid="target-card">
            <div className="card-head"><h3 className="card-title">What To Predict</h3><span className="micro-label">label column y</span></div>
            <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
              <label className="field">
                <span className="micro-label">Label column</span>
                <select className="select" data-testid="label-select" value={ch.label} onChange={(e) => pickLabel(e.target.value)}>
                  {options.map((c) => <option key={c.name} value={c.name}>{c.name}{c.name === ds.target && targetConfirmed(ds, c.name) && ds.id ? '  (confirmed)' : c.name === (ds.profile?.suggested_target ?? ds.target) ? '  (suggested)' : ''} · {c.type}</option>)}
                </select>
              </label>
              <div className="field">
                <span className="micro-label">Problem type</span>
                <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--space-8)' }}>
                  <div className="seg" role="tablist" data-testid="problem-type">
                    {(['binary', 'multiclass', 'regression'] as Task[]).map((t) => (
                      <button key={t} role="tab" aria-selected={task === t} className={task === t ? 'on' : ''} disabled={!allowed.includes(t)}
                        onClick={() => set({ problemType: t === inferred ? null : t, ...(t !== 'regression' ? { range: null } : {}) })}>{t}</button>
                    ))}
                  </div>
                  <span className="helper" style={{ fontSize: 12 }} data-testid="problem-type-note">
                    {inferred ? <>inferred: <b className="mono">{inferred}</b>{ch.problemType && ch.problemType !== inferred ? ' · overridden' : ''}</> : <span style={{ color: 'var(--status-warning)' }}>▲ this column has fewer than two values</span>}
                  </span>
                </div>
              </div>
              {task === 'regression' && <RangeCard ch={ch} set={set} label={ch.label} />}
              {!(task === 'regression' && ch.range) && <div className="formula target-math">
                {task === 'regression'
                  ? <div className="eq"><i>y</i> ∈ ℝ, &nbsp;<i>ŷ</i> = <i>f</i>(<i>x</i>) ∈ ℝ</div>
                  : <div className="eq"><i>y</i> ∈ {'{'}<i>c</i>₁, …, <i>c</i><sub>K</sub>{'}'}, &nbsp;<i>p̂</i>(<i>x</i>) ∈ Δ<sup>K−1</sup>, &nbsp;<i>K</i> = {K || unique}</div>}
                <dl className="where">
                  <dt>x</dt><dd>one row's {used.length} feature{used.length === 1 ? '' : 's'}, X ∈ ℝ<sup>n×d</sup> after encoding</dd>
                  {task === 'regression'
                    ? <><dt>ŷ</dt><dd>the predicted value of <b className="mono">{ch.label}</b>, in its own units</dd></>
                    : <><dt>cₖ</dt><dd>the classes of <b className="mono">{ch.label}</b></dd><dt>Δᴷ⁻¹</dt><dd>the probability simplex: p̂ₖ ≥ 0, Σₖ p̂ₖ = 1</dd></>}
                </dl>
              </div>}
            </div>
            <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }} data-testid="label-distribution">
              {task === 'regression' || (!K && stats) ? (
                stats ? <div className="stack" style={{ gap: 6 }}>
                  <div className="micro-label">Distribution of <span className="mono" style={{ textTransform: 'none' }}>{ch.label}</span></div>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                    <ParamChip sym="ȳ" value={sig4(stats.mean)} /><ParamChip sym="σ_y" value={sig4(stats.std)} />
                    <ParamChip sym="median" value={sig4(stats.median)} /><ParamChip sym="y ∈" value={`[${sig4(stats.min)}, ${sig4(stats.max)}]`} />
                  </div>
                  <RangeBar min={stats.min} max={stats.max} mean={stats.mean} std={stats.std} median={stats.median} />
                </div> : <span className="helper">A numeric label: AutoGluon fits regression.</span>
              ) : (
                <>
                  <div className="micro-label" style={{ marginBottom: 8 }}>Classes of <span className="mono" style={{ textTransform: 'none' }}>{ch.label}</span>{K > 1 && <> · imbalance <i className="nocase">ρ = n<sub>max</sub>/n<sub>min</sub> = {(maxShare / minShare).toFixed(1)}</i></>}</div>
                  {classes.slice(0, 10).map((c) => (
                    <div key={c.name} className="dist-row">
                      <span title={c.name}>{c.name}</span>
                      <svg width="100%" height="10" aria-hidden><line x1="0" x2={`${(c.share / maxShare) * 100}%`} y1="5" y2="5" stroke={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} strokeWidth="2" strokeDasharray={c.share < 0.1 ? '4 3' : undefined} /><circle cx={`${(c.share / maxShare) * 100}%`} cy="5" r="3" fill={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} /></svg>
                      <span className="table-cell">{(c.share * 100).toFixed(1)}%</span>
                    </div>
                  ))}
                  {classes.length > 10 && <span className="helper" style={{ fontSize: 12 }}>+ {classes.length - 10} more</span>}
                </>
              )}
            </div>
          </div>
        </div>

        <div className="stack">
        {ds.id && <ConfirmTarget ds={ds} label={ch.label} onConfirmed={onConfirmed} />}
        {ds.id && targetConfirmed(ds, ch.label) && <SplitCard ds={ds} label={ch.label} spec={ch.split ?? null} onSpec={(sp) => set({ split: sp })} />}
        <div className="card" data-testid="column-types">
          <div className="card-head">
            <div className="left"><h3 className="card-title">Columns</h3><span className="helper" style={{ fontSize: 12 }}>how AutoGluon reads each one · edit to override</span></div>
            <div className="row"><ParamChip sym="d" value={String(used.length)} />{changed.length > 0 && <StatusPill status="info">{changed.length} changed</StatusPill>}</div>
          </div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Column</th><th>Inferred</th><th>Read as</th><th>AutoGluon raw · special</th><th>Example</th></tr></thead>
            <tbody>
              <tr className="sel"><td className="table-cell">{ch.label}</td><td><span className="chip" style={{ fontSize: 11 }}>{ds.cols.find((c) => c.name === ch.label)?.type}</span></td><td><StatusPill status="success">Label y</StatusPill></td><td className="muted mono" style={{ fontSize: 12 }}>{task ?? '—'}</td><td className="muted">{ds.cols.find((c) => c.name === ch.label)?.sample}</td></tr>
              {feats.map((c) => {
                const t = effectiveType(c, ch)
                const isChanged = t !== (c.type === 'date' ? 'category' : c.type)
                return (
                  <tr key={c.name} className={isChanged ? 'changed' : ''} data-testid={`coltype-row-${c.name}`}>
                    <td className="table-cell">{c.name}</td>
                    <td><span className="chip" style={{ fontSize: 11 }}>{c.type === 'date' ? 'date' : TYPE_LABEL[c.type]}</span></td>
                    <td>
                      <select className="select sm" aria-label={`Type of ${c.name}`} data-testid={`coltype-${c.name}`} value={t} onChange={(e) => setType(c.name, e.target.value as ColType)}>
                        {(['number', 'category', 'text', 'id'] as ColType[]).map((o) => <option key={o} value={o}>{TYPE_LABEL[o]}</option>)}
                      </select>
                    </td>
                    <td className="mono muted" style={{ fontSize: 12 }}>{t === 'id' ? <span>dropped</span> : <>{AG_RAW[t]}{t === 'text' ? ' · text' : ''}</>}</td>
                    <td className="muted" style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.sample}</td>
                  </tr>
                )
              })}
            </tbody>
          </table></div>
          <div className="card-body fm-preview" style={{ borderTop: '1px solid var(--border-default)' }}>
            <div className="micro-label" style={{ marginBottom: 6 }}>Sent to <span className="mono" style={{ textTransform: 'none' }}>fit(feature_metadata=…)</span></div>
            <code className="mono" data-testid="feature-metadata">
              {changed.length === 0
                ? 'feature_metadata = "infer"  — AutoGluon infers every type itself'
                : `FeatureMetadata(type_map_raw={${used.filter((c) => ch.types[c.name] && ch.types[c.name] !== 'id').map((c) => `'${c.name}': '${AG_RAW[effectiveType(c, ch)]}'`).join(', ')}, …}${text.length ? `, type_group_map_special={'text': [${text.map((x) => `'${x}'`).join(', ')}]}` : ''})`}
              {Object.entries(ch.types).some(([k, v]) => v === 'id' && k !== ch.label) && `\ndrop_columns = [${Object.entries(ch.types).filter(([k, v]) => v === 'id' && k !== ch.label).map(([k]) => `'${k}'`).join(', ')}]`}
            </code>
            <p className="helper" style={{ fontSize: 12, marginTop: 6 }}>
              number → float · category → object (one category per value) · text → object with special type <i>text</i> (n-grams; TF-IDF models on classification) · ignore → left out, like ID columns
            </p>
          </div>
        </div>
        </div>
      </div>
    </div>
  )
}

/**
 * F4: confirming the target is an explicit step. The holdout H and the folds are drawn per target (and strategy),
 * so the split shown here is the one every run with this y trains and is scored on, whether it starts here or over the API.
 */
function ConfirmTarget({ ds, label, onConfirmed }: { ds: Dataset; label: string; onConfirmed?: (d: Dataset) => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [stored, setStored] = useState<DatasetSplits | null>(null)
  const ok = targetConfirmed(ds, label)
  const sp = ds.profile?.splits
  const src = ds.profile?.target_source
  const guess = ds.profile?.suggested_target
  useEffect(() => {
    let alive = true
    api.datasets.splits(ds.id!).then((x) => { if (alive) setStored(x) }).catch(() => {})
    return () => { alive = false }
  }, [ds.id, ds.target, sp?.key])
  const confirm = async () => {
    setBusy(true); setErr('')
    try { onConfirmed?.(fromServer(await api.datasets.setTarget(ds.id!, label))) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const pct = sp ? Math.round(sp.holdout_share * 100) : 20
  return (
    <div className="card" data-testid="confirm-target" data-confirmed={ok ? 'yes' : 'no'}>
      <div className="card-head">
        <h3 className="card-title">Confirm The Target</h3>
        {ok ? <StatusPill status="success">{src === 'given' ? 'Named on upload' : 'Confirmed'}</StatusPill> : <StatusPill status="warning">Not confirmed</StatusPill>}
      </div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {ok && sp ? (
          <>
            <div className="formula">
              <div className="eq"><i>H</i> ⊂ {'{'}1, …, <i>n</i>{'}'}, &nbsp;|<i>H</i>| = {pct}% · <i>n</i> = {sp.holdout_rows.toLocaleString()}</div>
              <div className="eq"><i>F</i><sub>1</sub> ∪ … ∪ <i>F</i><sub>{sp.n_folds}</sub> = {'{'}1, …, <i>n</i>{'}'} ∖ <i>H</i></div>
              <dl className="where">
                <dt>H</dt><dd>the holdout rows of data.csv: every run with y = <b className="mono">{label}</b> is scored on exactly these</dd>
                <dt>Fₖ</dt><dd>fold k of the remaining rows ({sp.fold_rows?.map((x) => x.toLocaleString()).join(' · ')})</dd>
                <dt>n</dt><dd>rows in the file: {ds.rows.toLocaleString()}</dd>
                <dt>seed</dt><dd>{sp.seed}; {sp.stratified ? <>stratified on <b className="mono">{label}</b> (each class keeps its share in H and every Fₖ)</> : <>random (a numeric target, or a class with fewer than {sp.n_folds + 1} rows)</>}</dd>
              </dl>
            </div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 6 }} data-testid="confirmed-split">
              <ParamChip sym="strategy" value={sp.strategy ?? 'random-stratified'} />
              <ParamChip sym="key" value={sp.key ?? '—'} />
              <ParamChip sym="sha256" value={(sp.sha256 ?? '').slice(0, 12)} />
            </div>
          </>
        ) : (
          <>
            <p className="helper" style={{ fontSize: 12.5 }} data-testid="confirm-note">
              {src === 'guessed' && ds.target === label
                ? <>The profiler guessed <b className="mono">{guess}</b>. Confirm it, or pick another label column above.</>
                : <>The dataset's target is <b className="mono">{ds.target || '—'}</b>{src === 'guessed' ? ' (a guess)' : ''}. Confirming <b className="mono">{label}</b> re-profiles the file for it and draws its own holdout and folds (seed {sp?.seed ?? 42}, {pct}%, stratified on y when every class can be), or reuses them if they were drawn before. Other targets' splits stay as they are.</>}
            </p>
            <div className="row"><button className="btn primary" data-testid="confirm-target-btn" disabled={busy || !label} onClick={confirm}>{busy ? 'Drawing the split…' : <>Confirm y = {label}</>}</button></div>
          </>
        )}
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      </div>
      {stored && stored.splits.length > 0 && (
        <div className="table-wrap" style={{ borderTop: '1px solid var(--border-default)' }}>
          <table className="table" data-testid="stored-splits">
            <thead><tr><th>Target</th><th>Strategy</th><th>|H|</th><th>SHA-256</th><th /></tr></thead>
            <tbody>{stored.splits.map((x) => (
              <tr key={x.key} className={x.key === stored.current ? 'sel' : ''}>
                <td className="table-cell mono">{x.target ?? '—'}</td><td className="muted">{x.strategy}</td>
                <td className="table-cell">{x.holdout_rows.toLocaleString()}</td><td className="mono muted" style={{ fontSize: 12 }}>{x.sha256?.slice(0, 12)}</td>
                <td className="muted" style={{ fontSize: 12 }}>{x.key === stored.current ? 'current' : ''}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="helper" style={{ fontSize: 12, padding: '6px 12px' }}>One split per target and strategy, drawn the first time it is used and never redrawn. A run records the key and SHA-256 of the split it used.</p>
        </div>
      )}
    </div>
  )
}


/** The label's range with its mean ± σ and median marked, as a string from min to max. */
function RangeBar({ min, max, mean, std, median }: { min: number; max: number; mean: number; std: number; median: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const x = (v: number) => 4 + Math.max(0, Math.min(1, (v - min) / (max - min || 1))) * (w - 8)
  return (
    <div ref={ref} style={{ width: '100%' }}>
      <svg width={w} height="36" className="range-bar" aria-label={`range ${min} to ${max}`} style={{ display: 'block', overflow: 'visible' }}>
        <line x1={4} x2={w - 4} y1="14" y2="14" stroke="var(--border-track)" strokeWidth="2" />
        <rect x={x(mean - std)} y="8" height="12" width={Math.max(2, x(mean + std) - x(mean - std))} fill="var(--bg-wave-tint)" stroke="var(--accent-secondary)" strokeWidth="1" rx="2" />
        <line x1={x(median)} x2={x(median)} y1="5" y2="23" stroke="var(--accent-primary)" strokeWidth="2" />
        <circle cx={x(mean)} cy="14" r="4" fill="var(--accent-secondary)" />
        <circle cx={4} cy="14" r="3" fill="var(--accent-primary)" /><circle cx={w - 4} cy="14" r="3" fill="var(--accent-primary)" />
        <text x={0} y="34" className="tick-text">{sig4(min)}</text><text x={w} y="34" textAnchor="end" className="tick-text">{sig4(max)}</text>
        <text x={x(mean)} y="34" textAnchor="middle" className="tick-text">ȳ ± σ · | median</text>
      </svg>
    </div>
  )
}
