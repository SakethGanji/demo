import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import type { Dataset } from '../datasets'
import { useWidth } from '@/shared/hooks/useWidth'
import { effectiveType, inferTask, labelClasses, labelOptions, type Choices, type ColType, type Task } from '../wizard'

const TYPE_LABEL: Record<ColType, string> = { number: 'number', category: 'category', text: 'text', id: 'ignore' }
const AG_RAW: Record<ColType, string> = { number: 'float', category: 'object', text: 'object', id: '—' }

/** Step 2: which column to predict, what kind of problem that is, and how each column is read (AutoGluon feature_metadata). */
export function TargetStep({ ds, ch, set }: { ds: Dataset; ch: Choices; set: (p: Partial<Choices>) => void }) {
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
    set({ label, problemType: null, types })
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
                  {options.map((c) => <option key={c.name} value={c.name}>{c.name}{c.name === ds.target ? '  (suggested)' : ''} · {c.type}</option>)}
                </select>
              </label>
              <div className="field">
                <span className="micro-label">Problem type</span>
                <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--space-8)' }}>
                  <div className="seg" role="tablist" data-testid="problem-type">
                    {(['binary', 'multiclass', 'regression'] as Task[]).map((t) => (
                      <button key={t} role="tab" aria-selected={task === t} className={task === t ? 'on' : ''} disabled={!allowed.includes(t)}
                        onClick={() => set({ problemType: t === inferred ? null : t })}>{t}</button>
                    ))}
                  </div>
                  <span className="helper" style={{ fontSize: 12 }} data-testid="problem-type-note">
                    {inferred ? <>inferred: <b className="mono">{inferred}</b>{ch.problemType && ch.problemType !== inferred ? ' · overridden' : ''}</> : <span style={{ color: 'var(--status-warning)' }}>▲ this column has fewer than two values</span>}
                  </span>
                </div>
              </div>
              <div className="formula target-math">
                {task === 'regression'
                  ? <div className="eq"><i>y</i> ∈ ℝ, &nbsp;<i>ŷ</i> = <i>f</i>(<i>x</i>) ∈ ℝ</div>
                  : <div className="eq"><i>y</i> ∈ {'{'}<i>c</i>₁, …, <i>c</i><sub>K</sub>{'}'}, &nbsp;<i>p̂</i>(<i>x</i>) ∈ Δ<sup>K−1</sup>, &nbsp;<i>K</i> = {K || unique}</div>}
                <dl className="where">
                  <dt>x</dt><dd>one row's {used.length} feature{used.length === 1 ? '' : 's'}, X ∈ ℝ<sup>n×d</sup> after encoding</dd>
                  {task === 'regression'
                    ? <><dt>ŷ</dt><dd>the predicted value of <b className="mono">{ch.label}</b>, in its own units</dd></>
                    : <><dt>cₖ</dt><dd>the classes of <b className="mono">{ch.label}</b></dd><dt>Δᴷ⁻¹</dt><dd>the probability simplex: p̂ₖ ≥ 0, Σₖ p̂ₖ = 1</dd></>}
                </dl>
              </div>
            </div>
            <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }} data-testid="label-distribution">
              {task === 'regression' || (!K && stats) ? (
                stats ? <div className="stack" style={{ gap: 6 }}>
                  <div className="micro-label">Distribution of <span className="mono" style={{ textTransform: 'none' }}>{ch.label}</span></div>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                    <ParamChip sym="ȳ" value={fmt(stats.mean)} /><ParamChip sym="σ_y" value={fmt(stats.std)} />
                    <ParamChip sym="median" value={fmt(stats.median)} /><ParamChip sym="y ∈" value={`[${fmt(stats.min)}, ${fmt(stats.max)}]`} />
                  </div>
                  <RangeBar min={stats.min} max={stats.max} mean={stats.mean} std={stats.std} median={stats.median} />
                </div> : <span className="helper">A numeric label: AutoGluon fits regression.</span>
              ) : (
                <>
                  <div className="micro-label" style={{ marginBottom: 8 }}>Classes of <span className="mono" style={{ textTransform: 'none' }}>{ch.label}</span>{K > 1 && <> · imbalance ρ = n<sub>max</sub>/n<sub>min</sub> = {(maxShare / minShare).toFixed(1)}</>}</div>
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
                const isChanged = t !== c.type
                return (
                  <tr key={c.name} className={isChanged ? 'changed' : ''} data-testid={`coltype-row-${c.name}`}>
                    <td className="table-cell">{c.name}</td>
                    <td><span className="chip" style={{ fontSize: 11 }}>{TYPE_LABEL[c.type]}</span></td>
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
  )
}

const fmt = (v: number) => (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : Number(v.toPrecision(4)).toString())

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
        <text x={0} y="34" className="tick-text">{fmt(min)}</text><text x={w} y="34" textAnchor="end" className="tick-text">{fmt(max)}</text>
        <text x={x(mean)} y="34" textAnchor="middle" className="tick-text">ȳ ± σ · | median</text>
      </svg>
    </div>
  )
}
