import { useEffect, useMemo, useState } from 'react'
import { MaskBar } from '@/shared/components/MaskBar'
import type { MaskInfo, Unmask } from '@/shared/api/unmask'
import { api } from '@/shared/api/client'
import type { Run, ShiftAction, ShiftCheck, ShiftList, ShiftSource, ShiftSummary } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { niceTicks, XYPlot } from '@/shared/components/XYPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { fix, pct } from '@/shared/lib/format'
import { HeroCell } from '@/shared/components/Status'

type Patch = NonNullable<ShiftAction['patch']>
const pfmt = (p: number) => (p < 0.001 ? p.toExponential(1) : p.toFixed(3))
const SOURCES: Record<string, ShiftSource> = { 'Train vs Holdout': 'holdout', 'Two Periods': 'time', 'Registry Batch': 'batch' }
const VERDICT = {
  'no shift': { pill: 'success', say: 'The classifier cannot tell the two sets apart better than chance.' },
  slight: { pill: 'info', say: 'Separable, but barely: worth knowing, rarely worth acting on alone.' },
  moderate: { pill: 'warning', say: 'The new rows look measurably different. Check the shifting columns.' },
  strong: { pill: 'danger', say: 'The new rows are easy to tell apart: validate out of time, or act on the shifting columns.' },
} as const
const PSI_COLOR: Record<string, string> = { stable: 'var(--status-success)', watch: 'var(--status-warning)', shift: 'var(--status-danger)' }

/** F15: adversarial validation. An AutoGluon classifier (bagged, scored out of fold) tells the reference rows from the
 *  new rows; its AUC with a DeLong interval and two nulls, the shifting columns, and the remedies. Every number is
 *  GET /api/runs/{id}/shift/{key}. */
export function ShiftPanel({ run, go, onApplyPatch }: { run: Run; go: (r: string) => void; onApplyPatch?: (runId: string, patch: Patch) => void }) {
  const [list, setList] = useState<ShiftList | null>(null)
  const [key, setKey] = useState<string | null>(null)
  const [check, setCheck] = useState<(ShiftCheck & MaskInfo) | null>(null)
  const [unmask, setUnmask] = useState<Unmask>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [tick, setTick] = useState(0)
  const [source, setSource] = useState<ShiftSource>('holdout')
  const [batch, setBatch] = useState('')
  const [tcol, setTcol] = useState('')
  const [cutoff, setCutoff] = useState('')
  const [refits, setRefits] = useState(3)
  const [perms, setPerms] = useState(2000)
  const [clip, setClip] = useState(0.01)
  const [timeLimit, setTimeLimit] = useState<number | null>(null)
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')
  const [fitKw, setFitKw] = useState('')

  useEffect(() => {
    let alive = true
    api.runs.shifts(run.id).then((l) => {
      if (!alive) return
      setList(l); setErr('')
      if (!tcol && l.options.time_column) setTcol(l.options.time_column)
      if (!batch && l.options.batches[0]) setBatch(l.options.batches[0].id)
      setKey((k) => k ?? l.checks[0]?.key ?? null)
    }).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!key) { setCheck(null); return }
    let alive = true
    api.runs.shift(run.id, key, unmask ?? undefined).then((c) => alive && setCheck(c)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, key, tick, unmask])
  const active = check?.status === 'queued' || check?.status === 'running'
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => setTick((v) => v + 1), 2000)
    return () => clearTimeout(t)
  }, [active, check])

  const start = async () => {
    setBusy(true); setErr('')
    try {
      const split = (s: string) => s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean)
      const c = await api.runs.startShift(run.id, {
        source, batch_id: source === 'batch' ? batch : undefined, time_column: source === 'time' ? tcol || undefined : undefined,
        cutoff: source === 'time' && cutoff.trim() ? cutoff.trim() : undefined, null_refits: refits, permutations: perms, clip,
        time_limit: timeLimit ?? undefined, include: split(include), exclude: split(exclude),
        fit_kwargs: fitKw.trim() ? JSON.parse(fitKw) : undefined,
      })
      setKey(c.key); setCheck(c); setTick((v) => v + 1)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const cancel = async () => { if (key) try { await api.runs.cancelShift(run.id, key); setTick((v) => v + 1) } catch (e) { setErr((e as Error).message) } }

  if (!list && !err) return <div className="card card-body row"><ResonanceWave /> Reading the shift checks…</div>
  const opts = list?.options
  const done = check?.status === 'succeeded' && check.result

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="shift-panel" data-status={check?.status ?? 'none'}>
      {err && <div className="card card-body" role="alert" data-testid="shift-error"><p className="helper">✕ {err}</p></div>}
      {list && list.checks.length > 0 && <Checks checks={list.checks} sel={key} onPick={setKey} />}
      {check && active && (
        <div className="card card-body row" data-testid="shift-progress" style={{ gap: 'var(--space-12)', flexWrap: 'wrap' }}>
          <ResonanceWave /><span>{check.sets.reference.name} vs {check.sets.new.name}: <b>{check.stage ?? check.status}</b></span>
          <button className="btn" onClick={cancel} data-testid="shift-cancel">Cancel</button>
        </div>
      )}
      {check && (check.status === 'failed' || check.status === 'cancelled') && <div className="card card-body" role="alert"><p className="helper">✕ This check {check.status === 'failed' ? `failed: ${check.error}` : 'was cancelled'}.</p></div>}
      {done && <Hero c={check} />}
      {done && <div className="grid mono-grid"><AucFormula c={check} /><NullPlot c={check} /></div>}
      {done && <MaskBar info={check} kind="run" id={run.id} value={unmask} onChange={setUnmask} what="feature means and standard deviations" />}
      {done && <Features c={check} />}
      {done && <Actions c={check} run={run} go={go} onApplyPatch={onApplyPatch} onChanged={() => setTick((v) => v + 1)} />}
      {done && <div className="grid mono-grid"><Weights c={check} /><Sets c={check} /></div>}
      {list && !active && (
        <div className="card" data-testid="shift-start">
          <div className="card-head"><div className="left"><h3 className="card-title">{list.checks.length ? 'Check Another Pair' : 'Has the Data Shifted?'}</h3>
            <span className="helper" style={{ fontSize: 12 }}>an AutoGluon classifier learns to tell the two sets of rows apart; scored out of fold, AUC ≈ 0.5 means it cannot</span></div></div>
          <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
            <Segmented options={Object.keys(SOURCES)} value={Object.keys(SOURCES).find((k) => SOURCES[k] === source)!} onChange={(x) => setSource(SOURCES[x])} />
            {source === 'holdout' && <p className="helper" style={{ fontSize: 12.5 }}>The run's training rows against its holdout ({run.split?.strategy ?? 'random-stratified'} split). Under a random split the two are drawn from the same rows, so the expected answer is no shift; under an out-of-time split this measures how far the future drifts.</p>}
            {source === 'time' && (opts?.date_columns.length ? <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap' }}>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">date column (F6)</span>
                <select className="input" value={tcol} onChange={(e) => setTcol(e.target.value)} aria-label="Date column">{opts.date_columns.map((d) => <option key={d.column} value={d.column}>{d.column} · {d.from} → {d.to}</option>)}</select></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">cut-off τ (before vs on or after)</span>
                <input className="input mono" value={cutoff} placeholder={opts.cutoff ?? '80th percentile of the dates'} onChange={(e) => setCutoff(e.target.value)} aria-label="Cut-off" style={{ width: 200 }} /></label>
              <p className="helper" style={{ fontSize: 12, maxWidth: 520 }}>The date itself, row identifiers and entity ids are left out: they separate any earlier and later rows by construction (AUC ≈ 1) and say nothing about whether the inputs changed.</p>
            </div> : <p className="helper">This dataset has no date column.</p>)}
            {source === 'batch' && (opts?.batches.length ? <label className="stack" style={{ gap: 4 }}><span className="micro-label">a batch scored by a version of this run (Registry → Batch & Drift)</span>
              <select className="input" value={batch} onChange={(e) => setBatch(e.target.value)} aria-label="Batch">{opts.batches.map((b) => <option key={b.id} value={b.id}>{b.filename} · {b.id} · {b.version_id}{b.max_psi != null ? ` · max PSI ${b.max_psi.toFixed(3)}` : ''}</option>)}</select></label>
              : <p className="helper">No batch has been scored by a version of this run yet. Register it and score a file in Registry → Batch &amp; Drift.</p>)}
            <details className="an-adv" data-testid="shift-advanced"><summary className="micro-label">Advanced · nulls, weights, columns, fit</summary>
              <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap', marginTop: 'var(--space-8)' }}>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">R, refits on shuffled origins</span><NumberInput value={refits} min={0} max={20} step={1} onChange={(v) => setRefits(Math.round(v))} label="Refits" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">B, label permutations</span><NumberInput value={perms} min={100} max={100000} step={500} onChange={(v) => setPerms(Math.round(v))} label="Permutations" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">weight clip quantile</span><NumberInput value={clip} min={0} max={0.2} step={0.005} onChange={setClip} label="Clip" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">time limit per fit, s</span><NumberInput value={timeLimit ?? 0} min={0} max={7200} step={10} onChange={(v) => setTimeLimit(v || null)} label="Time limit" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">also leave out</span><input className="input mono" value={exclude} onChange={(e) => setExclude(e.target.value)} aria-label="Exclude" style={{ width: 180 }} /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">keep although left out</span><input className="input mono" value={include} onChange={(e) => setInclude(e.target.value)} aria-label="Include" style={{ width: 180 }} /></label>
                <label className="stack" style={{ gap: 4, flex: 1, minWidth: 260 }}><span className="micro-label">fit() kwargs, JSON (default {`{"num_bag_folds": ${opts?.defaults.num_bag_folds}, "hyperparameters": ${JSON.stringify(opts?.defaults.hyperparameters)}}`})</span>
                  <input className="input mono" value={fitKw} placeholder='{"hyperparameters": {"GBM": {}, "CAT": {}}}' onChange={(e) => setFitKw(e.target.value)} aria-label="Fit kwargs" /></label>
              </div>
            </details>
            <div className="row"><button className="btn primary" onClick={start} data-testid="shift-run"
              disabled={busy || (source === 'batch' && !batch) || (source === 'time' && !opts?.date_columns.length)}>{busy ? 'Starting…' : 'Run the shift check'}</button></div>
          </div>
        </div>
      )}
    </div>
  )
}

function Checks({ checks, sel, onPick }: { checks: ShiftSummary[]; sel: string | null; onPick: (k: string) => void }) {
  return (
    <div className="row" style={{ gap: 8, flexWrap: 'wrap' }} data-testid="shift-checks">
      {checks.map((c) => (
        <button key={c.key} className={`chip ${c.key === sel ? 'active' : ''}`} onClick={() => onPick(c.key)} data-testid={`shift-check-${c.key}`}
          style={{ cursor: 'pointer', border: c.key === sel ? '1px solid var(--accent)' : undefined }}>
          {c.reference} vs {c.new}{c.auc != null ? <> · AUC <b className="mono">{c.auc.toFixed(3)}</b></> : ` · ${c.status}`}{c.verdict ? ` · ${c.verdict}` : ''}
        </button>
      ))}
    </div>
  )
}

function Hero({ c }: { c: ShiftCheck }) {
  const r = c.result!
  const v = r.verdict
  const meta = VERDICT[v.label]
  const top = r.features.filter((f) => f.significant)[0]
  return <>
    <div className="card navy explain-hero" data-testid="shift-hero">
      <div className="eh-cell lead"><span className="micro-label">adversarial AUC</span><b className="mono" data-testid="shift-auc">{r.auc.value.toFixed(3)}</b><small>95% CI [{r.auc.ci[0].toFixed(3)}, {r.auc.ci[1].toFixed(3)}] · out of fold</small></div>
      <div className="eh-cell"><span className="micro-label">verdict</span><b data-testid="shift-verdict">{v.label}</b><small>{meta.say}</small></div>
      <div className="eh-cell"><span className="micro-label">permutation null</span><b className="mono">p {r.null.permutation.p_value <= 1 / (r.null.permutation.B + 1) + 1e-12 ? '≤' : '='} {pfmt(r.null.permutation.p_value)}</b><small>AUC₀ 95% range [{r.null.permutation.q025.toFixed(3)}, {r.null.permutation.q975.toFixed(3)}], B = {r.null.permutation.B.toLocaleString()}</small></div>
      {r.null.refit && <div className="eh-cell"><span className="micro-label">refit null (R = {r.null.refit.R})</span><b className="mono">max {r.null.refit.max.toFixed(3)}</b><small>{r.null.refit.aucs.map((a) => a.toFixed(3)).join(' · ')}: the same fit on shuffled origin labels</small></div>}
      <div className="eh-cell"><span className="micro-label">shifts most</span><b className="mono" style={{ fontSize: top && top.feature.length > 10 ? 18 : undefined, overflowWrap: 'anywhere' }}>{top ? top.feature : '—'}</b><small>{top ? <>ΔAUC {fix(top.importance, 4)}{top.psi != null ? ` · PSI ${fix(top.psi)}` : ''}</> : 'no column, without a shift'}</small></div>
      {c.sets.new.max_psi != null && <HeroCell label="max PSI (batch)" sub="the univariate view, next to the AUC">{fix(c.sets.new.max_psi)}</HeroCell>}
    </div>
    <p className="helper" style={{ fontSize: 12.5 }} data-testid="shift-reason"><StatusPill status={meta.pill}>{v.label}</StatusPill>{' '}{v.reason} <span className="muted">Rule: {v.rule}.</span></p>
  </>
}

function AucFormula({ c }: { c: ShiftCheck }) {
  const a = c.result!.auc
  const p = c.result!.null.permutation
  return (
    <div className="formula" data-testid="shift-formula">
      <div className="micro-label">The AUC, its interval and the null</div>
      <div className="eq">AUC = <span className="frac">1 ⁄ (<i>m</i>·<i>n</i>)</span> Σ<sub><i>i</i>∈N</sub> Σ<sub><i>j</i>∈R</sub> ψ(<i>ŝ</i><sub><i>i</i></sub>, <i>ŝ</i><sub><i>j</i></sub>) = <b className="mono">{a.value.toFixed(4)}</b></div>
      <div className="eq">SE = √(S<sub>10</sub> ⁄ <i>m</i> + S<sub>01</sub> ⁄ <i>n</i>) = √({a.s10.toExponential(3)} ⁄ {a.m.toLocaleString()} + {a.s01.toExponential(3)} ⁄ {a.n.toLocaleString()}) = {a.se.toFixed(4)}</div>
      <div className="eq">CI = AUC ± <i>z</i>·SE = {a.value.toFixed(4)} ± {a.z.toFixed(2)} · {a.se.toFixed(4)} = [{a.ci[0].toFixed(4)}, {a.ci[1].toFixed(4)}]</div>
      <div className="eq"><i>p</i> = (1 + #{'{'}<i>b</i> : |AUC<sub>0</sub><sup>(<i>b</i>)</sup> − ½| ≥ |AUC − ½|{'}'}) ⁄ (<i>B</i> + 1) = <b className="mono">{pfmt(p.p_value)}</b></div>
      <dl className="where">
        <dt>N, R</dt><dd>the new rows ({c.sets.new.name}, <i>m</i> = {a.m.toLocaleString()}) and the reference rows ({c.sets.reference.name}, <i>n</i> = {a.n.toLocaleString()}), origin <i>o</i> = 1 and 0</dd>
        <dt>ŝ</dt><dd>ŝ = P̂(<i>o</i> = 1 | <i>x</i>) from {c.fit?.model}: each row scored by the fold model that did not see it ({c.fit?.num_bag_folds} folds, <span className="mono">{c.fit?.oof}</span>), so the AUC is not optimistic</dd>
        <dt>ψ</dt><dd>1 if ŝᵢ &gt; ŝⱼ, ½ if equal, 0 otherwise: AUC is the chance a new row scores above a reference row</dd>
        <dt>S₁₀, S₀₁</dt><dd>DeLong's variances of the per-row components: each new row's share of reference rows it outranks, and each reference row's share of new rows that outrank it</dd>
        <dt>z</dt><dd>{a.z.toFixed(2)}, the normal quantile for a {Math.round(a.level * 100)}% interval</dd>
        <dt>AUC₀, B</dt><dd>the AUC of the same scores against shuffled origins, B = {p.B.toLocaleString()} times: what chance alone gives (mean {p.mean.toFixed(4)}, sd {p.sd.toFixed(4)})</dd>
      </dl>
    </div>
  )
}

function NullPlot({ c }: { c: ShiftCheck }) {
  const r = c.result!
  const p = r.null.permutation
  const roc = r.roc
  const [view, setView] = useState<'Null' | 'ROC'>('Null')
  const bars = p.hist.map((h, i) => ({ x0: p.hist_edges[i], x1: p.hist_edges[i + 1], y: h, color: 'var(--viz-b)', opacity: 0.55 }))
  const xs = [p.hist_edges[0], p.hist_edges[p.hist_edges.length - 1], r.auc.value, ...(r.null.refit?.aucs ?? [])]
  const x0 = Math.min(...xs) - 0.01, x1 = Math.max(...xs) + 0.01
  const ymax = Math.max(...p.hist) * 1.15
  return (
    <PlotFrame title={view === 'Null' ? 'Observed AUC Against Chance' : 'Origin Classifier ROC'}
      right={<Segmented options={['Null', 'ROC']} value={view} onChange={(x) => setView(x as 'Null' | 'ROC')} />}
      chips={<><ParamChip sym="AUC" value={r.auc.value.toFixed(3)} /><ParamChip sym="p" value={pfmt(p.p_value)} /></>}
      legend={view === 'Null' ? [{ name: `AUC₀, ${p.B.toLocaleString()} label shuffles`, color: 'var(--viz-b)', data: [] }, { name: 'refit on shuffled origins', color: 'var(--viz-c)', data: [] }, { name: 'observed AUC', color: 'var(--viz-a)', data: [] }] : [{ name: 'ROC of the out-of-fold ŝ', color: 'var(--viz-a)', data: [] }]}
      caption={view === 'Null' ? <>bars: how often each AUC₀ arises when the origin labels are shuffled against the fixed out-of-fold scores (the count per bin); dots: the whole fit repeated on shuffled origins; line: the observed AUC.</>
        : <>true-positive rate (share of new rows above a threshold on ŝ) against false-positive rate (share of reference rows above it); the diagonal is chance, the area under the curve is the AUC.</>}>
      <div data-testid="shift-null">
        {view === 'Null' ? (
          <XYPlot height={240} series={r.null.refit ? [{ name: 'refit', color: 'var(--viz-c)', width: 0, dots: true, dotR: () => 4, points: r.null.refit.aucs.map((a) => [a, ymax * 0.12] as [number, number]), tip: (i) => `refit ${i + 1}: AUC ${r.null.refit!.aucs[i].toFixed(4)}` }] : []}
            bars={bars} x={{ min: x0, max: x1, ticks: niceTicks(x0, x1, 5), title: 'AUC', fmt: (v) => v.toFixed(3) }}
            y={{ min: 0, max: ymax, ticks: niceTicks(0, ymax, 4), title: 'shuffles per bin', fmt: (v) => v.toFixed(0) }}
            refs={[{ x: r.auc.value, label: `observed ${r.auc.value.toFixed(3)}`, color: 'var(--viz-a)', strong: true }, { x: 0.5, label: '½', color: 'var(--text-muted)', dash: '3 3' }]} />
        ) : (
          <XYPlot height={240} diagonal={{ label: 'chance' }} series={[{ name: 'ROC', color: 'var(--viz-a)', points: roc }]}
            x={{ min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], title: 'false-positive rate (reference rows)', fmt: (v) => v.toFixed(2) }}
            y={{ min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], title: 'true-positive rate (new rows)', fmt: (v) => v.toFixed(2) }} />
        )}
      </div>
    </PlotFrame>
  )
}

function Features({ c }: { c: ShiftCheck }) {
  const r = c.result!
  const max = Math.max(1e-9, ...r.features.map((f) => Math.max(f.ci?.[1] ?? f.importance, f.importance)))
  const min = Math.min(0, ...r.features.map((f) => Math.min(f.ci?.[0] ?? f.importance, f.importance)))
  const X = (v: number) => 2 + ((v - min) / (max - min)) * 196
  return (
    <div className="card" data-testid="shift-features">
      <div className="card-head"><div className="left"><h3 className="card-title">What Shifted</h3>
        <span className="helper" style={{ fontSize: 12 }}>ΔAUC<sub>j</sub>: the drop in out-of-fold AUC when column j is shuffled (AutoGluon's permutation importance, {r.importance_method.split(': ')[1]?.split(';')[0]}), with a 95% t-interval; PSI = Σ (aᵢ − eᵢ) ln(aᵢ ⁄ eᵢ) over bins, the one-column view</span></div></div>
      <div className="card-body" style={{ overflowX: 'auto' }}>
        <table className="table compact"><thead><tr><th className="r">#</th><th>column</th><th>kind</th><th className="r">ΔAUC</th><th style={{ width: 200 }}>95% t-interval (dashed: 0)</th><th className="r">PSI</th><th className="r">mean R → N</th><th /></tr></thead>
          <tbody>{r.features.map((f) => (
            <tr key={f.feature} data-testid={`shift-feature-${f.feature}`} data-rank={f.rank}>
              <td className="r mono">{f.rank}</td><td className="mono">{f.feature}</td><td className="muted">{f.kind}{f.model_features > 1 ? ` (${f.model_features} model features)` : ''}</td>
              <td className="r mono"><b>{fix(f.importance, 4)}</b></td>
              <td><svg width={200} height={14} aria-label={`interval of ${f.feature}`}>
                <line x1={0} x2={200} y1={7} y2={7} stroke="var(--border-subtle)" />
                <line x1={X(0)} x2={X(0)} y1={1} y2={13} stroke="var(--text-muted)" strokeDasharray="2 2" />
                <line x1={X(f.ci?.[0] ?? f.importance)} x2={X(f.ci?.[1] ?? f.importance)} y1={7} y2={7} stroke={f.significant ? 'var(--viz-a)' : 'var(--text-muted)'} strokeWidth={3} />
                <circle cx={X(f.importance)} cy={7} r={3} fill={f.significant ? 'var(--viz-a)' : 'var(--text-muted)'} />
              </svg><span className="mono" style={{ fontSize: 11 }}>{f.ci ? `[${fix(f.ci[0], 4)}, ${fix(f.ci[1], 4)}]` : ''}</span></td>
              <td className="r mono" style={{ color: f.psi_band ? PSI_COLOR[f.psi_band] : undefined }}>{fix(f.psi)}</td>
              <td className="r mono" style={{ fontSize: 12 }}>{f.mean_expected != null && f.mean_actual != null ? `${fmtScore(f.mean_expected, 3)} → ${fmtScore(f.mean_actual, 3)}` : '—'}</td>
              <td>{f.significant ? <StatusPill status="warning">shifts</StatusPill> : null}</td>
            </tr>))}</tbody></table>
        <p className="helper" style={{ fontSize: 12, marginTop: 8 }}>A column is marked <b>shifts</b> only when the sets are separable (verdict ≠ no shift) and its interval lies above 0. The interval treats the {r.features[0]?.n} scores (shuffles × fold models) as independent, so it is narrow; without a shift, intervals above 0 are the noise model's, not a shift.</p>
      </div>
    </div>
  )
}

function Actions({ c, run, go, onApplyPatch, onChanged }: { c: ShiftCheck; run: Run; go: (r: string) => void; onApplyPatch?: (runId: string, p: Patch) => void; onChanged: () => void }) {
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [wv, setWv] = useState<{ id: string; patch: Patch } | null>(null)
  const say = (id: string, m: string) => setMsg((x) => ({ ...x, [id]: m }))
  const twin = async (a: ShiftAction) => {
    setBusy(a.id)
    try {
      const r = await api.runs.splitTwin(run.id, a.split_spec!)
      say(a.id, `Training ${r.id} under the out-of-time split; Results → Random vs Out-Of-Time compares the two.`)
      go(`quick-train?run=${r.id}`)
    } catch (e) { say(a.id, `✕ ${(e as Error).message}`) } finally { setBusy('') }
  }
  const weights = async () => {
    setBusy('weights')
    try {
      const r = await api.runs.shiftWeightsVersion(run.id, c.key)
      setWv({ id: r.dataset_version.id, patch: r.patch })
      say('weights', `Dataset version ${r.dataset_version.id}: ${r.dataset_version.rows_weighted.toLocaleString()} rows weighted, ESS ${Math.round(r.dataset_version.ess).toLocaleString()}.`)
      onChanged()
    } catch (e) { say('weights', `✕ ${(e as Error).message}`) } finally { setBusy('') }
  }
  return (
    <div className="card" data-testid="shift-actions">
      <div className="card-head"><div className="left"><h3 className="card-title">What To Do About It</h3><span className="helper" style={{ fontSize: 12 }}>each remedy is a concrete request; nothing changes until you run it</span></div></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {c.actions.map((a) => (
          <div key={a.id} className="row" style={{ gap: 'var(--space-12)', alignItems: 'flex-start' }} data-testid={`shift-action-${a.id}`}>
            <div style={{ minWidth: 210 }}><b>{a.title}</b>{a.kind === 'split-twin' && a.split_spec ? <div className="mono helper" style={{ fontSize: 11 }}>split_spec {JSON.stringify(a.split_spec)}</div> : null}
              {a.kind === 'gym-patch' && a.patch ? <div className="mono helper" style={{ fontSize: 11 }}>{a.patch.changes.map((ch) => `${ch.path}: ${JSON.stringify(ch.from)} → ${JSON.stringify(ch.to)}`).join('; ')}</div> : null}</div>
            <p className="helper" style={{ fontSize: 12.5, flex: 1 }}>{a.why}{msg[a.id] ? <><br /><b>{msg[a.id]}</b></> : null}</p>
            {a.kind === 'split-twin' && <button className="btn sm" disabled={!a.available || busy === a.id} onClick={() => twin(a)} data-testid="shift-oot">{a.available ? 'Train out-of-time twin' : 'Already out of time'}</button>}
            {a.kind === 'gym-patch' && a.patch && <button className="btn sm" disabled={!onApplyPatch} onClick={() => onApplyPatch?.(run.id, a.patch!)} data-testid={`shift-drop-${a.feature}`}>Apply in Advanced</button>}
            {a.kind === 'weights-version' && (wv
              ? <button className="btn sm primary" disabled={!onApplyPatch} onClick={() => onApplyPatch?.(run.id, wv.patch)} data-testid="shift-weights-gym">Apply in Advanced (sample_weight)</button>
              : <button className="btn sm" disabled={busy === 'weights'} onClick={weights} data-testid="shift-weights">Save weights as a dataset version</button>)}
          </div>
        ))}
        {c.actions.every((a) => a.kind !== 'gym-patch') && c.result?.verdict.shift === false && <p className="helper" style={{ fontSize: 12 }}>No column is offered to drop: without a detectable shift there is nothing to drop.</p>}
      </div>
    </div>
  )
}

function Weights({ c }: { c: ShiftCheck }) {
  const w = c.result!.weights
  const h = w.hist_log10
  const ymax = Math.max(...h.counts) * 1.15
  const bars = useMemo(() => h.counts.map((n, i) => ({ x0: h.edges[i], x1: h.edges[i + 1], y: n, color: 'var(--viz-a)', opacity: 0.6 })), [h])
  return (
    <div className="stack" style={{ gap: 'var(--space-12)' }}>
      <div className="formula" data-testid="shift-weights-formula">
        <div className="micro-label">Importance weights (density ratio)</div>
        <div className="eq"><i>w</i>(<i>x</i>) = <span className="frac"><i>p</i><sub>N</sub>(<i>x</i>) ⁄ <i>p</i><sub>R</sub>(<i>x</i>)</span> = <span className="frac">ŝ(<i>x</i>) ⁄ (1 − ŝ(<i>x</i>))</span> · <span className="frac"><i>n</i> ⁄ <i>m</i></span> = <span className="frac">ŝ ⁄ (1 − ŝ)</span> · {w.prior_ratio.toFixed(3)}</div>
        <div className="eq">ESS = (Σ<i>w</i>)² ⁄ Σ<i>w</i>² = {w.sum_w.toFixed(1)}² ⁄ {w.sum_w2.toFixed(1)} = <b className="mono" data-testid="shift-ess">{Math.round(w.ess).toLocaleString()}</b> of {w.n_ref.toLocaleString()} ({pct(w.ess_share)})</div>
        <dl className="where">
          <dt>ŝ(x)</dt><dd>the out-of-fold P̂(o = 1 | x) of each reference row (the same scores as the AUC)</dd>
          <dt>n ⁄ m</dt><dd>{w.n_ref.toLocaleString()} reference ⁄ {w.n_new.toLocaleString()} new rows: undoes the classifier's prior odds</dd>
          <dt>clip</dt><dd>w clipped at its {w.clip_quantile} and {1 - w.clip_quantile} quantiles ({w.clipped_rows} rows), then scaled to mean 1: range [{w.min.toFixed(3)}, {w.max.toFixed(3)}], median {w.median.toFixed(3)}; unclipped ESS {Math.round(w.ess_unclipped).toLocaleString()} ({pct(w.ess_unclipped_share)})</dd>
          <dt>ESS</dt><dd>effective sample size: how many equally weighted rows carry the same information; a small share means a few rows dominate the weighted fit</dd>
        </dl>
        {w.balance.length > 0 && <table className="table compact" style={{ marginTop: 8 }}><thead><tr><th>column</th><th className="r">mean R</th><th className="r">weighted R</th><th className="r">mean N</th></tr></thead>
          <tbody>{w.balance.map((b) => <tr key={b.feature}><td className="mono">{b.feature}</td><td className="r mono">{fmtScore(b.mean_reference, 3)}</td><td className="r mono"><b>{fmtScore(b.mean_reference_weighted, 3)}</b></td><td className="r mono">{fmtScore(b.mean_new, 3)}</td></tr>)}</tbody></table>}
      </div>
      <PlotFrame title="Weights of the Reference Rows" chips={<ParamChip sym="ESS" value={pct(w.ess_share)} />}
        caption={<>reference rows per bin of log₁₀ w; w = 1 (log 0) is a row as likely in either set. Saved as column <span className="mono">{w.column}</span> of a dataset version, the weights reach AutoGluon as <span className="mono">sample_weight</span>.</>}>
        <XYPlot height={160} series={[]} bars={bars} x={{ min: h.edges[0], max: h.edges[h.edges.length - 1], ticks: niceTicks(h.edges[0], h.edges[h.edges.length - 1], 5), title: 'log₁₀ w', fmt: (v) => v.toFixed(1) }}
          y={{ min: 0, max: ymax, ticks: niceTicks(0, ymax, 3), title: 'rows', fmt: (v) => v.toFixed(0) }} refs={[{ x: 0, label: 'w = 1', color: 'var(--text-muted)', dash: '3 3' }]} />
      </PlotFrame>
    </div>
  )
}

function Sets({ c }: { c: ShiftCheck }) {
  return (
    <div className="card" data-testid="shift-sets">
      <div className="card-head"><div className="left"><h3 className="card-title">What Was Compared</h3></div></div>
      <div className="card-body stack" style={{ gap: 8 }}>
        <p className="helper" style={{ fontSize: 12.5 }}><b>R</b> = {c.sets.reference.what} ({c.sets.reference.rows.toLocaleString()} rows). <b>N</b> = {c.sets.new.what} ({c.sets.new.rows.toLocaleString()} rows).
          {c.extra.cutoff ? <> Cut-off τ = <span className="mono">{c.extra.cutoff}</span> ({c.extra.cutoff_source}) on {c.extra.time_column}, dates {c.extra.from} → {c.extra.to}{c.extra.undated_rows ? `, ${c.extra.undated_rows} undated rows left out` : ''}.</> : null}</p>
        <p className="helper" style={{ fontSize: 12.5 }}>{c.features.length} columns compared: <span className="mono">{c.features.join(', ')}</span>.</p>
        <table className="table compact" data-testid="shift-excluded" style={{ tableLayout: 'fixed', width: '100%' }}><thead><tr><th style={{ width: 150 }}>left out</th><th>why</th></tr></thead>
          <tbody>{c.excluded.map((e) => <tr key={e.column}><td className="mono">{e.column}</td><td className="helper" style={{ fontSize: 12, whiteSpace: 'normal' }}>{e.reason}</td></tr>)}</tbody></table>
        <details className="an-adv"><summary className="micro-label">The fit call ({c.fit?.fit_seconds.toFixed(0)} s)</summary>
          <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{c.fit?.call}</pre>
          <p className="helper" style={{ fontSize: 12 }}>Every fit (the check and each refit null) uses this call; importance: {c.result?.importance_method}.</p>
        </details>
      </div>
    </div>
  )
}
