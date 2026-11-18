import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { SplitDescription, SplitOptions, SplitSpec, SplitStrategy } from '@/shared/api/types'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { useWidth } from '@/shared/hooks/useWidth'
import type { Dataset } from '../datasets'
import { sub } from '../wizard'

export const STRATEGY_NAME: Record<SplitStrategy, string> = {
  'random-stratified': 'random', group: 'group', time: 'out-of-time', 'time-group': 'out-of-time + group',
}
const ROLE_COLOR = { train: 'var(--accent-primary)', tuning: 'var(--accent-secondary)', holdout: 'var(--status-warning)', excluded: 'var(--border-track)' } as const
type Role = keyof typeof ROLE_COLOR

/** The spec the wizard sends for a strategy, filled from the columns the API found (Advanced edits override). */
export function specFor(strategy: SplitStrategy, o: SplitOptions | null, cur?: SplitSpec | null): SplitSpec | null {
  if (strategy === 'random-stratified') return null
  const p = o?.suggested_params ?? {}
  const keep = cur && cur.strategy !== 'random-stratified' ? cur : null
  const spec: SplitSpec = { strategy, tuning_share: keep?.tuning_share ?? p.tuning_share ?? 0.2 }
  if (strategy !== 'time') spec.group_column = keep?.group_column ?? p.group_column ?? undefined
  if (strategy !== 'group') { spec.time_column = keep?.time_column ?? p.time_column ?? undefined; spec.cutoff = keep?.cutoff ?? null }
  return spec
}

/**
 * F6: how the holdout H is drawn for y. Random (stratified) is the default; data with an entity column (several rows
 * per customer) or a date column gets a warning and the group, out-of-time and out-of-time + group splits. The split is
 * drawn by the API (POST /datasets/{id}/splits) and drawn here as a timeline or a group diagram with its row counts.
 */
export function SplitCard({ ds, label, spec, onSpec }: { ds: Dataset; label: string; spec: SplitSpec | null; onSpec: (s: SplitSpec | null) => void }) {
  const [opts, setOpts] = useState<SplitOptions | null>(null)
  const [desc, setDesc] = useState<SplitDescription | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const strategy: SplitStrategy = spec?.strategy ?? 'random-stratified'
  useEffect(() => {
    let alive = true
    api.datasets.splitOptions(ds.id!, label).then((o) => { if (alive) setOpts(o) }).catch((e) => { if (alive) setErr((e as Error).message) })
    return () => { alive = false }
  }, [ds.id, label])
  const specKey = JSON.stringify(spec)
  useEffect(() => {
    let alive = true
    setErr('')
    setBusy(true)
    const t = setTimeout(() => {
      api.datasets.drawSplit(ds.id!, label, spec ?? { strategy: 'random-stratified' })
        .then((d) => { if (alive) setDesc(d) })
        .catch((e) => { if (alive) { setDesc(null); setErr((e as Error).message) } })
        .finally(() => { if (alive) setBusy(false) })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [ds.id, label, specKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const can = (s: SplitStrategy) => s === 'random-stratified' || (!!opts && (s === 'group' ? opts.group_columns.length > 0 : s === 'time' ? opts.date_columns.length > 0 : opts.group_columns.length > 0 && opts.date_columns.length > 0))
  const d = desc?.diagram
  const n = ds.rows
  return (
    <div className="card" data-testid="split-card" data-strategy={strategy}>
      <div className="card-head">
        <div className="left"><h3 className="card-title">How The Holdout Is Drawn</h3><span className="helper" style={{ fontSize: 12 }}>split strategy for y = {label}</span></div>
        {opts && opts.suggested !== 'random-stratified' && strategy === 'random-stratified'
          ? <StatusPill status="warning">Random on dated / grouped data</StatusPill>
          : <StatusPill status={strategy === 'random-stratified' ? 'info' : 'success'}>{STRATEGY_NAME[strategy]}</StatusPill>}
      </div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <div className="seg" role="tablist" data-testid="split-strategy">
          {(['random-stratified', 'group', 'time', 'time-group'] as SplitStrategy[]).map((s) => (
            <button key={s} role="tab" aria-selected={strategy === s} className={strategy === s ? 'on' : ''} disabled={!can(s)}
              data-testid={`split-${s}`} onClick={() => onSpec(specFor(s, opts, spec))}>{STRATEGY_NAME[s]}{opts?.suggested === s && s !== 'random-stratified' ? ' ★' : ''}</button>
          ))}
        </div>
        {strategy === 'random-stratified' && opts && opts.warnings.length > 0 && (
          <div className="split-warn" role="note" data-testid="split-warning">
            {opts.warnings.map((w) => <p key={w}><span style={{ color: 'var(--status-warning)' }}>▲</span> {w}</p>)}
            <button className="chip-btn" data-testid="split-use-suggested" onClick={() => onSpec(specFor(opts.suggested, opts, spec))}>Use the {STRATEGY_NAME[opts.suggested]} split →</button>
          </div>
        )}
        {desc && <SplitMath desc={desc} strategy={strategy} n={n} label={label} />}
        {d?.timeline && <Timeline d={desc!} />}
        {d && d.shared_groups && <GroupDiagram d={desc!} />}
        {desc && strategy === 'random-stratified' && <RandomBar desc={desc} n={n} />}
        {desc && (
          <div className="row" style={{ flexWrap: 'wrap', gap: 6 }} data-testid="split-chips">
            <ParamChip sym="key" value={desc.key ?? '—'} />
            <ParamChip sym="sha256" value={(desc.sha256 ?? '').slice(0, 12)} />
            {busy && <span className="helper" style={{ fontSize: 12 }}>drawing…</span>}
          </div>
        )}
        {strategy !== 'random-stratified' && opts && (
          <details className="an-adv split-adv" data-testid="split-advanced"><summary className="micro-label">Advanced · columns, cut-off, tuning slice</summary>
            <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 'var(--space-12)', marginTop: 'var(--space-8)' }}>
              {strategy !== 'time' && (
                <label className="field"><span className="micro-label">Entity column g</span>
                  <select className="select sm" data-testid="split-group-col" value={spec?.group_column ?? ''} onChange={(e) => onSpec({ ...spec!, group_column: e.target.value })}>
                    {opts.group_columns.map((g) => <option key={g.column} value={g.column}>{g.column} · {g.groups.toLocaleString()} values</option>)}
                  </select></label>
              )}
              {strategy !== 'group' && (
                <>
                  <label className="field"><span className="micro-label">Date column t</span>
                    <select className="select sm" data-testid="split-time-col" value={spec?.time_column ?? ''} onChange={(e) => onSpec({ ...spec!, time_column: e.target.value })}>
                      {opts.date_columns.map((c) => <option key={c.column} value={c.column}>{c.column} · {c.from} → {c.to}</option>)}
                    </select></label>
                  <label className="field"><span className="micro-label">Cut-off τ (blank: latest {Math.round(opts.holdout_share * 100)}%)</span>
                    <input className="input sm" type="date" data-testid="split-cutoff" value={spec?.cutoff ?? ''} onChange={(e) => onSpec({ ...spec!, cutoff: e.target.value || null })} /></label>
                </>
              )}
              <label className="field"><span className="micro-label">Tuning share s</span>
                <input className="input sm" type="number" min={0.05} max={0.5} step={0.05} data-testid="split-tuning-share" value={spec?.tuning_share ?? 0.2}
                  onChange={(e) => onSpec({ ...spec!, tuning_share: Math.min(0.5, Math.max(0.05, Number(e.target.value) || 0.2)) })} /></label>
            </div>
          </details>
        )}
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      </div>
    </div>
  )
}

const pct = (v?: number) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const k = (v?: number) => (v == null ? '—' : v.toLocaleString())

/** The definition of H, T and F for the strategy in view, with every symbol bound to the drawn split's numbers. */
function SplitMath({ desc, strategy, n, label }: { desc: SplitDescription; strategy: SplitStrategy; n: number; label: string }) {
  const r = desc.diagram?.roles
  const g = desc.params?.group_column, t = desc.params?.time_column
  if (strategy === 'random-stratified') {
    return (
      <div className="formula" data-testid="split-math">
        <div className="eq"><i>H</i> ⊂ {'{'}1, …, <i>n</i>{'}'} at random, &nbsp;|<i>H</i>| = {Math.round(desc.holdout_share * 100)}% · <i>n</i> = {k(desc.holdout_rows)}</div>
        <dl className="where">
          <dt>H</dt><dd>holdout rows, drawn {desc.stratified ? <>stratified on <b className="mono">{label}</b></> : 'uniformly'} with seed {desc.seed}</dd>
          <dt>n</dt><dd>{k(n)} rows in the file; the other {k(n - desc.holdout_rows)} are split into {desc.n_folds} random folds</dd>
        </dl>
      </div>
    )
  }
  return (
    <div className="formula" data-testid="split-math">
      {strategy === 'group' && <div className="eq"><i>g</i>(<i>H</i>) ∩ <i>g</i>(<i>T</i> ∪ <i>F</i>) = ∅, &nbsp;|<i>H</i>| = {k(r?.holdout.rows)}</div>}
      {strategy === 'time' && <div className="eq"><i>H</i> = {'{'}<i>i</i> : <i>t</i><sub>i</sub> ≥ τ{'}'}, &nbsp;<i>T</i> = {'{'}<i>i</i> : τ<sub>T</sub> ≤ <i>t</i><sub>i</sub> &lt; τ{'}'}, &nbsp;<i>F</i> = {'{'}<i>i</i> : <i>t</i><sub>i</sub> &lt; τ<sub>T</sub>{'}'}</div>}
      {strategy === 'time-group' && <div className="eq"><i>H</i> = {'{'}<i>i</i> : <i>t</i><sub>i</sub> ≥ τ, <i>g</i><sub>i</sub> ∉ <i>g</i>(<i>t</i> &lt; τ){'}'}, &nbsp;<i>g</i>(<i>H</i>) ∩ <i>g</i>(<i>T</i> ∪ <i>F</i>) = ∅</div>}
      <dl className="where">
        <dt>H</dt><dd>holdout: {k(r?.holdout.rows)} rows{r?.holdout.groups != null ? `, ${k(r.holdout.groups)} entities` : ''}{r?.holdout.from ? `, ${r.holdout.from} → ${r.holdout.to}` : ''}{r?.holdout.positive_rate != null ? ` · ${desc.diagram?.positive_label} rate ${pct(r.holdout.positive_rate)}` : ''}</dd>
        <dt>T</dt><dd>tuning slice, passed to <span className="mono">fit(tuning_data=…)</span>: {k(r?.tuning.rows)} rows{r?.tuning.from ? `, ${r.tuning.from} → ${r.tuning.to}` : ''}{strategy === 'group' ? `, ${k(r?.tuning.groups)} entities (s = ${desc.params?.tuning_share} of the training entities)` : ''}</dd>
        <dt>F</dt><dd>training rows: {k(r?.train.rows)}{r?.train.from ? `, ${r.train.from} → ${r.train.to}` : ''}{desc.diagram?.fold_groups ? `, in ${desc.n_folds} entity-disjoint folds F₁…F${sub(desc.n_folds)} (AutoGluon groups=)` : `, in ${desc.n_folds} contiguous time blocks`}</dd>
        {g && <><dt>g</dt><dd>the entity column <b className="mono">{g}</b>: g(S) is the set of its values in rows S</dd></>}
        {t && <><dt>tᵢ</dt><dd>row i's <b className="mono">{t}</b></dd></>}
        {desc.cutoff != null && <><dt>τ</dt><dd>cut-off {String(desc.cutoff)}{desc.params?.cutoff ? ' (set in Advanced)' : ` (default: the latest ${Math.round(desc.holdout_share * 100)}% of rows)`}</dd></>}
        {desc.tuning_cutoff != null && <><dt>τ<sub>T</sub></dt><dd>tuning cut-off {String(desc.tuning_cutoff)}: the latest s = {desc.params?.tuning_share} of the training period</dd></>}
        {(r?.excluded.rows ?? 0) > 0 && <><dt>E</dt><dd>{k(r?.excluded.rows)} rows after τ from entities already seen before it: in no set, so H holds only new entities</dd></>}
      </dl>
    </div>
  )
}

/** Rows per period, stacked by role, with the cut-offs τ_T and τ and the positive rate per period. */
function Timeline({ d }: { d: SplitDescription }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const tl = d.diagram!.timeline!
  const H = 164, top = 28, base = 126
  const max = Math.max(...tl.map((p) => p.train + p.tuning + p.holdout + p.excluded), 1)
  const rates = tl.map((p) => p.positive_rate ?? 0)
  const rmax = Math.max(...rates, 0.01)
  const bw = Math.max(1, (w - 70) / tl.length)
  const x = (i: number) => 32 + i * bw
  const firstOf = (role: Role) => tl.findIndex((p) => p[role] > 0)
  const iCut = firstOf('holdout'), iTune = firstOf('tuning')
  const roles: Role[] = ['train', 'tuning', 'holdout', 'excluded']
  const ticks = useMemo(() => tl.map((p, i) => ({ i, p: p.period })).filter((_, i) => i % Math.max(1, Math.ceil(tl.length / 7)) === 0 && i < tl.length - 2), [tl])
  return (
    <div ref={ref} className="split-timeline" data-testid="split-timeline">
      <svg width={w} height={H} role="img" aria-label="rows per period by role">
        <line x1={30} x2={w - 6} y1={base} y2={base} stroke="var(--plot-axis)" />
        <text x={26} y={top + 4} textAnchor="end" className="tick-text">{max}</text>
        {tl.map((p, i) => {
          let y = base
          return roles.map((r) => {
            const h = (p[r] / max) * (base - top)
            y -= h
            return h > 0 ? <rect key={`${i}${r}`} x={x(i) + 1} y={y} width={Math.max(1, bw - 2)} height={h} fill={ROLE_COLOR[r]} opacity={r === 'excluded' ? 0.6 : 0.85}><title>{p.period} · {r} {p[r]} rows</title></rect> : null
          })
        })}
        {d.diagram?.positive_label && <polyline fill="none" stroke="var(--text-primary)" strokeWidth={1.2} strokeDasharray="3 2"
          points={tl.map((p, i) => `${x(i) + bw / 2},${base - ((p.positive_rate ?? 0) / rmax) * (base - top) * 0.9}`).join(' ')} />}
        {iTune >= 0 && <><line x1={x(iTune)} x2={x(iTune)} y1={4} y2={base + 4} stroke="var(--accent-secondary)" strokeDasharray="4 3" /><text x={x(iTune) - 4} y={12} textAnchor="end" className="tick-text">τ<tspan baselineShift="sub" fontSize="9">T</tspan> = {String(d.tuning_cutoff)}</text></>}
        {iCut >= 0 && <><line x1={x(iCut)} x2={x(iCut)} y1={4} y2={base + 4} stroke="var(--status-warning)" strokeWidth={1.5} /><text x={x(iCut) + 4} y={12} className="tick-text">τ = {String(d.cutoff)}</text></>}
        {ticks.map(({ i, p }) => <text key={p} x={x(i)} y={base + 16} className="tick-text">{p}</text>)}
      </svg>
      <div className="row split-legend" style={{ flexWrap: 'wrap', gap: 10 }}>
        {roles.filter((r) => d.diagram!.roles[r].rows > 0).map((r) => <span key={r}><i style={{ background: ROLE_COLOR[r] }} />{r === 'train' ? 'F train' : r === 'tuning' ? 'T tuning' : r === 'holdout' ? 'H holdout' : 'E excluded'} {d.diagram!.roles[r].rows.toLocaleString()}</span>)}
        {d.diagram?.positive_label && <span><i className="dash" />{d.diagram.positive_label} rate per period (max {pct(rmax)})</span>}
      </div>
    </div>
  )
}

/** Entities per role and the pairwise overlaps (all zero), with the rows-per-entity histogram. */
function GroupDiagram({ d }: { d: SplitDescription }) {
  const r = d.diagram!.roles
  const s = d.diagram!.shared_groups!
  const hist = d.diagram!.rows_per_group?.hist ?? {}
  const hmax = Math.max(...Object.values(hist), 1)
  const boxes: Role[] = ['train', 'tuning', 'holdout']
  const total = boxes.reduce((a, b) => a + r[b].rows, 0) || 1
  return (
    <div className="split-groups" data-testid="split-groups">
      <div className="split-group-boxes">
        {boxes.map((b, i) => (
          <div key={b} className="sg-box" style={{ flexGrow: Math.max(0.2, r[b].rows / total), borderColor: ROLE_COLOR[b] }}>
            <span className="micro-label" style={{ color: ROLE_COLOR[b] }}>{b === 'train' ? 'F · train' : b === 'tuning' ? 'T · tuning' : 'H · holdout'}</span>
            <b className="mono">{r[b].rows.toLocaleString()}</b><span className="helper">rows · {r[b].groups?.toLocaleString()} entities</span>
            {r[b].positive_rate != null && <span className="helper">{d.diagram!.positive_label} {pct(r[b].positive_rate)}</span>}
            {i < boxes.length - 1 && <span className="sg-cap" aria-hidden>∩ ∅</span>}
          </div>
        ))}
      </div>
      <div className="row" style={{ flexWrap: 'wrap', gap: 6 }} data-testid="split-shared">
        <ParamChip sym="|g(F) ∩ g(H)|" value={String(s.train_holdout)} />
        <ParamChip sym="|g(T) ∩ g(H)|" value={String(s.tuning_holdout)} />
        <ParamChip sym="|g(F) ∩ g(T)|" value={String(s.train_tuning)} />
        {d.diagram!.fold_groups && <ParamChip sym="entities per fold" value={d.diagram!.fold_groups.map((x) => x.toLocaleString()).join(' · ')} />}
      </div>
      {Object.keys(hist).length > 0 && (
        <div className="sg-hist" aria-label="rows per entity">
          <span className="micro-label">rows per entity · mean {d.diagram!.rows_per_group!.mean.toFixed(2)}</span>
          {Object.entries(hist).map(([kk, v]) => (
            <div key={kk} className="dist-row"><span>{kk === '6' ? '6+' : kk}</span><svg width="100%" height="10" aria-hidden><line x1="0" x2={`${(v / hmax) * 100}%`} y1="5" y2="5" stroke="var(--accent-primary)" strokeWidth="2" /></svg><span className="table-cell">{v.toLocaleString()}</span></div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Random: H scattered over the whole file; the bar shows its share. */
function RandomBar({ desc, n }: { desc: SplitDescription; n: number }) {
  const h = desc.holdout_rows / Math.max(1, n)
  return (
    <div className="split-random" data-testid="split-random">
      <div className="sr-bar"><i style={{ width: `${(1 - h) * 100}%`, background: ROLE_COLOR.train }} /><i style={{ width: `${h * 100}%`, background: ROLE_COLOR.holdout }} /></div>
      <div className="row split-legend" style={{ gap: 10 }}><span><i style={{ background: ROLE_COLOR.train }} />F train {(n - desc.holdout_rows).toLocaleString()}</span><span><i style={{ background: ROLE_COLOR.holdout }} />H holdout {desc.holdout_rows.toLocaleString()}</span></div>
    </div>
  )
}
