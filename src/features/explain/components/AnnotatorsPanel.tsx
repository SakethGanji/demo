import { Fragment, useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { AnnItem, Annotations, Run } from '@/shared/api/types'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

const f3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))
const f4 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(4))
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const ci = (c: [number, number] | null | undefined, d = 4) => (c ? `[${c[0].toFixed(d)}, ${c[1].toFixed(d)}]` : '')
const nfmt = (n: number) => Math.round(n).toLocaleString()
const PAGE = 30

/** F14: ground-truth reliability. Several annotators per row → Cohen's κ per pair, Fleiss' κ, each annotator's
 *  quality and the CROWDLAB consensus (cleanlab.multiannotator) from their labels and the run's out-of-fold p̂; a
 *  disagreement queue to vote or adjudicate under a reviewer name; the final labels as a dataset version (holdout
 *  untouched). Every number is GET /api/runs/{id}/annotations. */
export function AnnotatorsPanel({ run }: { run: Run }) {
  const [an, setAn] = useState<Annotations | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [page, setPage] = useState(0)
  useEffect(() => {
    let alive = true
    api.runs.annotations(run.id, { offset: page * PAGE, limit: PAGE }).then((r) => { if (alive) { setAn(r); setErr('') } }).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, tick, page])
  const working = an?.status === 'computing'
  useEffect(() => {
    if (!working) return
    const t = setTimeout(() => setTick((v) => v + 1), 2000)
    return () => clearTimeout(t)
  }, [working, an])
  const reload = () => setTick((v) => v + 1)

  if (!an && !err) return <div className="card card-body row"><ResonanceWave /> Reading the annotations…</div>
  const ready = an?.status === 'ready' && !!an.summary
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="ann-panel" data-status={an?.status}>
      {err && <div className="card card-body" role="alert"><p className="helper">✕ {err}</p></div>}
      {an && working && (
        <div className="card card-body row" data-testid="ann-progress" style={{ gap: 'var(--space-12)' }}>
          <ResonanceWave />
          <span>{an.n_raw?.toLocaleString()} annotations loaded · computing p̂(x), the class probabilities of every training row out of fold
            (the run's own OOF when it was bagged, else a quick bagged fit of its call: about a minute)</span>
        </div>
      )}
      {an?.status === 'failed' && <div className="card card-body" role="alert"><p className="helper">✕ {an.error}</p></div>}
      {an && ready && <Hero an={an} />}
      {an && ready && <AnnotatorTable an={an} />}
      {an && ready && an.summary!.truth && <TruthCard an={an} />}
      {an && ready && <Agreement an={an} />}
      {an && ready && <Queue an={an} page={page} setPage={setPage} onChange={reload} />}
      {an && !working && <Load an={an} run={run} onLoaded={(r) => { setPage(0); setAn(r) }} setErr={setErr} />}
    </div>
  )
}

function Hero({ an }: { an: Annotations }) {
  const s = an.summary!
  const fk = s.agreement.fleiss_kappa
  const t = s.truth
  return <>
    <div className="card navy explain-hero" data-testid="ann-hero">
      <div className="eh-cell lead"><span className="micro-label">agreement · Fleiss' κ</span><b className="mono" data-testid="ann-fleiss">{f3(fk?.value)}</b>
        <small>95% CI {ci(fk?.ci, 3)} · {s.agreement.interpretation} (Landis–Koch) · {nfmt(s.n_rows)} rows × {s.annotations_per_row.mean.toFixed(1)} labels</small></div>
      <div className="eh-cell"><span className="micro-label">least reliable annotator</span><b data-testid="ann-worst">{s.worst_annotator?.name ?? '—'}</b>
        <small>quality {f3(s.worst_annotator?.quality)}{t?.worst_by_truth ? <> · truth says {t.worst_by_truth.name} ({pct(t.worst_by_truth.accuracy)}) {t.worst_identified ? '✓' : '✕'}</> : null}</small></div>
      {t ? <div className="eh-cell"><span className="micro-label">consensus vs majority vote · simulated truth</span>
        <b className="mono" data-testid="ann-acc">{pct(t.accuracy.consensus.accuracy, 2)} vs {pct(t.accuracy.majority_vote.accuracy, 2)}</b>
        <small>accuracy on {nfmt(t.n)} rows · {t.paired.consensus_right_mv_wrong} fixed, {t.paired.mv_right_consensus_wrong} broken · McNemar p = {t.paired.mcnemar_p.toExponential(1)}</small></div>
        : <div className="eh-cell"><span className="micro-label">consensus ≠ majority vote</span><b className="mono">{s.consensus.consensus_vs_majority_vote_differ}</b><small>rows ({s.consensus.majority_vote_ties} majority-vote ties)</small></div>}
      <div className="eh-cell"><span className="micro-label">disagreement queue</span><b className="mono">{nfmt(s.consensus.queue)}</b>
        <small>{s.consensus.disagreements} split votes · {s.consensus.low_quality} with q &lt; {s.consensus.queue_threshold} · {s.consensus.adjudicated} adjudicated</small></div>
    </div>
    <p className="helper" style={{ fontSize: 12.5 }} data-testid="ann-source">
      {an.simulated ? <><StatusPill status="warning">simulated</StatusPill>{' '}The annotators are the sample's simulation: each label was drawn from the sample's hidden truth with the error profile listed below, so the truth columns can be shown. </> : null}
      p̂(x): {s.probs.note}{s.probs.fit_seconds ? ` in ${s.probs.fit_seconds} s` : ''}; {nfmt(s.probs.n_oof)} training rows out of fold. Only training rows are annotated, scored and relabelled; the holdout is never relabelled.
    </p>
  </>
}

function Bar({ v, color = 'var(--viz-a)' }: { v: number | null | undefined; color?: string }) {
  return <span style={{ display: 'inline-block', width: 64, height: 8, background: 'var(--bg-canvas)', border: '1px solid var(--border-default)', verticalAlign: 'middle', marginRight: 6 }}>
    <span style={{ display: 'block', height: '100%', width: `${Math.max(0, Math.min(1, v ?? 0)) * 100}%`, background: color }} /></span>
}

function AnnotatorTable({ an }: { an: Annotations }) {
  const s = an.summary!
  const rows = [...(an.annotators ?? [])].sort((a, b) => (a.quality ?? 1) - (b.quality ?? 1))
  const truth = !!s.truth
  return (
    <div className="card" data-testid="ann-annotators">
      <div className="card-head"><div className="left"><h3 className="card-title">Annotators, Least Reliable First</h3>
        <span className="helper" style={{ fontSize: 12 }}>quality, agreement and weight from cleanlab's CROWDLAB; the top confusion is against the consensus, so a systematic bias shows without any truth</span></div></div>
      <div className="card-body" style={{ overflowX: 'auto' }}>
        <table className="table compact">
          <thead><tr><th>annotator</th><th className="r">labels</th><th>quality q<sub>j</sub></th><th className="r">agrees with ŷ</th><th className="r">weight w<sub>j</sub></th><th className="r">mean κ</th><th>top disagreement<br />consensus → label</th>
            {truth && <th>accuracy vs truth (simulated)</th>}</tr></thead>
          <tbody>{rows.map((a) => {
            const worst = s.worst_annotator?.name === a.name
            const tc = a.top_confusion
            return <tr key={a.name} data-testid="ann-row">
              <td><b>{a.name}</b>{worst && <> <StatusPill status="danger">least reliable</StatusPill></>}
                {truth && <div className="muted" style={{ fontSize: 11, whiteSpace: 'normal', maxWidth: 260 }}>{a.truth?.profile?.profile ?? 'not simulated (a person in this page)'}</div>}</td>
              <td className="r mono">{a.n}</td>
              <td className="mono"><Bar v={a.quality} color={worst ? 'var(--status-danger)' : 'var(--viz-a)'} />{f3(a.quality)}</td>
              <td className="r mono">{pct(a.agreement_with_consensus)}</td>
              <td className="r mono">{f3(a.weight)}</td>
              <td className="r mono">{f3(a.kappa_mean)}</td>
              <td style={{ fontSize: 12 }}>{tc ? <><b>{tc.consensus} → {tc.given}</b>{tc.share_of_class > 0.3 ? <> <StatusPill status="warning">systematic?</StatusPill></> : null}<div className="muted" style={{ fontSize: 11 }}>{tc.n} of {tc.of_class_rows} consensus-{tc.consensus} rows ({pct(tc.share_of_class, 0)})</div></> : '—'}</td>
              {truth && <td className="mono"><Bar v={a.truth?.accuracy} color="var(--viz-b)" />{pct(a.truth?.accuracy)}<div className="muted" style={{ fontSize: 10.5 }}>{ci(a.truth?.ci, 3)}</div></td>}
            </tr>
          })}</tbody>
        </table>
        <dl className="where" style={{ marginTop: 'var(--space-8)' }}>
          <dt>q<sub>j</sub></dt><dd>cleanlab's annotator quality, ω · (the mean label quality of j's labels under the posterior p̃, below) + (1 − ω) · (j's agreement with the consensus ŷ), ω = w<sub>M</sub> ⁄ (w<sub>M</sub> + Σ<sub>j</sub> w<sub>j</sub> · n̄ ⁄ M), with n̄ = {s.annotations_per_row.mean.toFixed(1)} labels per row and M = {s.n_annotators} annotators; in [0, 1]</dd>
          <dt>w<sub>j</sub></dt><dd>CROWDLAB's weight of annotator j: 1 − (j's disagreement with the other annotators) ⁄ (the error of always answering the commonest class), floored at 10⁻⁴</dd>
          <dt>mean κ</dt><dd>the mean of j's Cohen's κ with every other annotator (pairs below)</dd>
          <dt>least reliable</dt><dd>the lowest q<sub>j</sub> among annotators with ≥ {s.worst_annotator?.min_labels ?? 30} labels</dd>
        </dl>
      </div>
    </div>
  )
}

function TruthCard({ an }: { an: Annotations }) {
  const t = an.summary!.truth!
  const rows: [string, string, typeof t.accuracy.consensus | null][] = [
    ['CROWDLAB consensus', 'arg max of p̃ (annotators + model)', t.accuracy.consensus],
    ['final labels', 'consensus, with adjudications over it', t.accuracy.final],
    ['majority vote', 'the commonest label; ties broken at random', t.accuracy.majority_vote],
    ["cleanlab's majority vote", 'ties broken by the model', t.accuracy.majority_vote_cleanlab],
    ['model alone', 'arg max p̂(x), out of fold', t.accuracy.model],
    ["the dataset's label", 'the label the run trained on', t.accuracy.given],
  ]
  return (
    <div className="card" data-testid="ann-truth">
      <div className="card-head"><div className="left"><h3 className="card-title">Against the Hidden Truth</h3>
        <span className="helper" style={{ fontSize: 12 }}>simulated: the sample knows each row's true class ({t.source}); a real upload has no such column</span></div>
        <StatusPill status="warning">simulated</StatusPill></div>
      <div className="card-body" style={{ overflowX: 'auto' }}>
        <table className="table compact">
          <thead><tr><th>labels</th><th>rule</th><th className="r">accuracy</th><th className="r">95% Wilson CI</th><th className="r">correct of {t.n}</th></tr></thead>
          <tbody>{rows.map(([k, rule, a]) => a && <tr key={k}><td><b>{k}</b></td><td className="muted" style={{ fontSize: 12 }}>{rule}</td><td className="r mono"><b>{f4(a.accuracy)}</b></td><td className="r mono">{ci(a.ci)}</td><td className="r mono">{a.correct}</td></tr>)}</tbody>
        </table>
        <div className="formula" style={{ marginTop: 'var(--space-12)' }}>
          <div className="eq">b = {t.paired.consensus_right_mv_wrong}, c = {t.paired.mv_right_consensus_wrong} · Δ = (b − c) ⁄ n = <b className="mono">{(t.paired.gain * 100).toFixed(2)} points</b> · P(Bin(b + c, ½) as extreme) = <b className="mono">{t.paired.mcnemar_p.toExponential(2)}</b></div>
          <dl className="where">
            <dt>b</dt><dd>rows the consensus gets right and majority vote gets wrong</dd>
            <dt>c</dt><dd>rows majority vote gets right and the consensus gets wrong</dd>
            <dt>n</dt><dd>{t.n} annotated rows; the exact McNemar test asks whether b and c differ by more than a fair coin would</dd>
          </dl>
        </div>
      </div>
    </div>
  )
}

function Agreement({ an }: { an: Annotations }) {
  const s = an.summary!
  const fk = an.fleiss
  const names = (an.annotators ?? []).map((a) => a.name)
  const pw = new Map((an.pairwise ?? []).map((p) => [`${p.a}|${p.b}`, p]))
  const get = (a: string, b: string) => pw.get(`${a}|${b}`) ?? pw.get(`${b}|${a}`)
  const ks = (an.pairwise ?? []).filter((p) => p.kappa != null)
  const [sel, setSel] = useState(ks[0] ? `${ks[0].a}|${ks[0].b}` : '')
  const p = pw.get(sel)
  const pe = p && p.kappa != null && p.agreement != null ? (p.agreement - p.kappa) / (1 - p.kappa) : null
  const cl = s.crowdlab
  return (
    <div className="grid mono-grid" data-testid="ann-agreement">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Cohen's κ, Every Pair</h3><span className="helper" style={{ fontSize: 12 }}>on the rows both labelled; click a cell</span></div></div>
        <div className="card-body" style={{ overflowX: 'auto' }}>
          <table className="table compact">
            <thead><tr><th />{names.map((n) => <th key={n} className="r">{n}</th>)}</tr></thead>
            <tbody>{names.map((a) => <tr key={a}><td><b>{a}</b></td>{names.map((b) => {
              if (a === b) return <td key={b} className="r muted">—</td>
              const q = get(a, b)
              const k = q?.kappa
              const key = q ? `${q.a}|${q.b}` : ''
              return <td key={b} className="r mono" onClick={() => q && k != null && setSel(key)} style={{ cursor: 'pointer', outline: key === sel ? '2px solid var(--accent-primary)' : undefined,
                background: k == null ? undefined : `color-mix(in srgb, var(--viz-a) ${Math.round(Math.max(0, k) * 70)}%, transparent)` }} title={q ? `n = ${q.n}` : ''}>{k == null ? '·' : k.toFixed(2)}</td>
            })}</tr>)}</tbody>
          </table>
          {p && p.kappa != null && <div className="formula compact" style={{ marginTop: 'var(--space-8)' }}>
            <div className="eq">κ<sub>{p.a},{p.b}</sub> = (p<sub>o</sub> − p<sub>e</sub>) ⁄ (1 − p<sub>e</sub>) = ({f3(p.agreement)} − {f3(pe)}) ⁄ (1 − {f3(pe)}) = <b className="mono">{f3(p.kappa)}</b> <span className="muted">95% CI {ci(p.ci, 3)}</span></div>
            <dl className="where"><dt>p<sub>o</sub></dt><dd>the share of the {p.n} rows both labelled where they agree</dd><dt>p<sub>e</sub></dt><dd>Σ<sub>k</sub> p<sub>{p.a},k</sub> · p<sub>{p.b},k</sub>: the agreement expected by chance from each one's class shares</dd><dt>CI</dt><dd>bootstrap over the shared rows, B = 300</dd></dl>
          </div>}
        </div>
      </div>
      <div className="stack" style={{ gap: 'var(--space-12)' }}>
        {fk && <div className="formula compact" data-testid="ann-fleiss-formula">
          <div className="micro-label">Fleiss' κ, all annotators at once</div>
          <div className="eq">κ = (P̄ − P̄<sub>e</sub>) ⁄ (1 − P̄<sub>e</sub>) = ({f4(fk.P_bar)} − {f4(fk.P_e)}) ⁄ (1 − {f4(fk.P_e)}) = <b className="mono">{f4(fk.value)}</b></div>
          <dl className="where">
            <dt>P̄</dt><dd>the mean over the {nfmt(fk.n_rows)} rows of P<sub>i</sub> = Σ<sub>k</sub> n<sub>ik</sub>(n<sub>ik</sub> − 1) ⁄ (n<sub>i</sub>(n<sub>i</sub> − 1)): the share of agreeing annotator pairs on row i</dd>
            <dt>n<sub>ik</sub></dt><dd>annotators who gave row i class k; n<sub>i</sub> = {fk.raters_per_row.min === fk.raters_per_row.max ? fk.raters_per_row.min : `${fk.raters_per_row.min}–${fk.raters_per_row.max}`} labels per row</dd>
            <dt>P̄<sub>e</sub></dt><dd>Σ<sub>k</sub> p<sub>k</sub>², p<sub>k</sub> = the share of all labels that are class k ({Object.entries(fk.p).map(([k, v]) => `${k} ${v.toFixed(2)}`).join(', ')})</dd>
            <dt>CI</dt><dd>{ci(fk.ci)}, bootstrap over rows, B = 1,000</dd>
          </dl>
        </div>}
        {cl.model_weight != null && <div className="formula compact" data-testid="ann-crowdlab-formula">
          <div className="micro-label">The CROWDLAB consensus</div>
          <div className="eq">p̃<sub>k</sub>(x) = [w<sub>M</sub> p̂<sub>k</sub>(x) + Σ<sub>j∈A(x)</sub> w<sub>j</sub> · (π if ℓ<sub>j</sub>(x) = k, else (1 − π) ⁄ (K − 1))] ⁄ (w<sub>M</sub> + Σ<sub>j∈A(x)</sub> w<sub>j</sub>)</div>
          <div className="eq">ŷ(x) = arg max<sub>k</sub> p̃<sub>k</sub>(x) · w<sub>M</sub> = <b className="mono">{f3(cl.model_weight)}</b> · π = <b className="mono">{f3(cl.pi)}</b> · K = {s.classes.length}</div>
          <dl className="where">
            <dt>p̂<sub>k</sub>(x)</dt><dd>the model's out-of-fold probability of class k ({s.probs.model}, {s.probs.source === 'run-oof' ? "the run's own OOF" : 'a quick bagged fit'})</dd>
            <dt>A(x), ℓ<sub>j</sub>(x)</dt><dd>the annotators of row x and j's label</dd>
            <dt>w<sub>j</sub></dt><dd>{Object.entries(cl.annotator_weights ?? {}).map(([k, v]) => `${k} ${v.toFixed(3)}`).join(' · ')}</dd>
            <dt>w<sub>M</sub></dt><dd>the model's weight: (1 − its error against the majority vote ⁄ the commonest-class error) · √(mean labels per row)</dd>
            <dt>π</dt><dd>the mean share of a row's annotators who agree with its majority vote</dd>
            <dt>q(x)</dt><dd>the consensus quality score shown per row in the queue (cleanlab's label quality of ŷ under p̃)</dd>
          </dl>
          <p className="helper" style={{ fontSize: 11.5 }}>{cl.consensus_is_argmax ? 'ŷ(x) is the arg max of p̃ on every row: checked.' : 'ŷ(x) differs from the arg max of p̃ on some rows (ties).'}</p>
        </div>}
      </div>
    </div>
  )
}

function Queue({ an, page, setPage, onChange }: { an: Annotations; page: number; setPage: (p: number) => void; onChange: () => void }) {
  const q = an.queue!
  const s = an.summary!
  const [items, setItems] = useState<AnnItem[]>(q.items)
  const [sel, setSel] = useState<AnnItem | null>(q.items[0] ?? null)
  const [who, setWho] = useState('you')
  const [pick, setPick] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ver, setVer] = useState('')
  useEffect(() => { setItems(q.items); setSel((x) => q.items.find((i) => i.row === x?.row) ?? q.items[0] ?? null) }, [q.items])
  const classes = an.classes
  const act = async (it: AnnItem, action: 'vote' | 'adjudicate' | 'clear') => {
    setErr('')
    try { await api.runs.answerAnnotations(an.run_id, [{ row: it.row, action, label: action === 'clear' ? undefined : pick[it.row] ?? it.consensus }], who); onChange() } catch (e) { setErr((e as Error).message) }
  }
  const simulate = async () => { setBusy(true); try { await api.runs.simulateAdjudication(an.run_id, 50); onChange() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }
  const version = async () => { setBusy(true); try { const r = await api.runs.annotationsVersion(an.run_id); setVer(r.dataset_version.id); onChange() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }
  const pages = Math.max(1, Math.ceil(q.total / PAGE))
  const st = sel
  return (
    <div className="card inbox open" data-testid="ann-queue">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Disagreement Queue</h3>
          <span className="helper" style={{ fontSize: 12 }}>rows the annotators split on, or whose consensus quality q(x) &lt; {s.consensus.queue_threshold}; lowest q first. Vote (one more annotation under your name) or adjudicate (the final label)</span></div>
        <StatusPill status="warning">{nfmt(q.total)} rows</StatusPill>
      </div>
      <div className="inbox-top">
        <div className="formula compact">
          {st ? <>
            <div className="eq">row <b className="mono">{st.row}</b>: p̃ = {st.posterior.map((p) => <span key={p.label}>{p.label} <b className="mono">{p.p.toFixed(3)}</b> · </span>)} → ŷ = <b>{st.consensus}</b>, q(x) = <b className="mono">{st.consensus_quality.toFixed(3)}</b></div>
            <dl className="where">
              <dt>votes</dt><dd>{st.votes.map((v) => `${v.annotator}: ${v.label}`).join(' · ')} → majority vote <b>{st.majority_vote}</b> ({pct(st.agreement, 0)} agree with ŷ)</dd>
              <dt>p̂(x)</dt><dd>{st.p_model.map((p) => `${p.label} ${p.p.toFixed(3)}`).join(' · ')} (model, out of fold)</dd>
              <dt>dataset label</dt><dd>{st.given ?? 'empty'}{st.adjudicated ? <> · adjudicated to <b>{st.final}</b></> : null}</dd>
            </dl>
          </> : <p className="helper">No rows in the queue.</p>}
        </div>
        <div className="recall-box" data-testid="ann-queue-stats">
          <span className="micro-label">Reviewer</span>
          <input className="input" value={who} onChange={(e) => setWho(e.target.value || 'you')} aria-label="Reviewer name" style={{ maxWidth: 220 }} />
          <dl className="where tight">
            <dt>adjudicated</dt><dd>{s.consensus.adjudicated} rows</dd>
            <dt>would change</dt><dd>{s.consensus.changed_vs_given ?? '—'} of the dataset's labels on the {nfmt(s.n_rows)} annotated rows</dd>
            {an.versions.length ? <><dt>versions</dt><dd>{an.versions.map((v) => <span key={v.id} className="mono">{v.id} ({v.relabelled} relabelled) </span>)}</dd></> : null}
          </dl>
        </div>
      </div>
      <div className="inbox-bar">
        {err && <span className="helper" role="alert">✕ {err}</span>}
        {ver && <span className="helper" data-testid="ann-version">✓ dataset version <b className="mono">{ver}</b>: the holdout rows and labels are unchanged</span>}
        <span className="spacer" />
        {an.simulated && <button className="btn sm" disabled={busy} onClick={simulate} data-testid="ann-simulate" title="Adjudicate the 50 lowest-quality open rows from the hidden truth, as simulated-adjudicator">Adjudicate 50 from Hidden Truth (Simulated)</button>}
        <button className="btn sm primary" disabled={busy} onClick={version} data-testid="ann-make-version">Save Final Labels as a Dataset Version</button>
      </div>
      <div className="table-wrap inbox-scroll">
        <table className="table inbox-table">
          <thead><tr><th>Row</th><th>Text / features</th><th>Votes</th><th>Majority</th><th>Consensus ŷ</th><th className="r">q(x)</th><th>Answer</th></tr></thead>
          <tbody>{items.map((it) => {
            const txt = Object.entries(it.fields)
            return <tr key={it.row} className={sel?.row === it.row ? 'sel' : ''} onClick={() => setSel(it)} data-testid="ann-item">
              <td className="mono">{it.row}</td>
              <td className="txt">{txt[0]?.[1]}<div className="muted" style={{ fontSize: 11 }}>{txt.slice(1).map(([k, v]) => `${k}=${v}`).join(' · ')}</div></td>
              <td style={{ fontSize: 12 }}>{it.votes.map((v, i) => <Fragment key={v.annotator}>{i ? <br /> : null}<span className="muted">{v.annotator}</span> {v.label === it.consensus ? v.label : <b>{v.label}</b>}</Fragment>)}</td>
              <td style={{ fontSize: 12 }}>{it.majority_vote}</td>
              <td><b>{it.final}</b>{it.adjudicated ? <div className="muted" style={{ fontSize: 10.5 }}>adjudicated (ŷ {it.consensus})</div> : <div className="muted" style={{ fontSize: 10.5 }}>p̃ {it.posterior[0]?.p.toFixed(2)}</div>}</td>
              <td className="r mono">{it.consensus_quality.toFixed(3)}</td>
              <td onClick={(e) => e.stopPropagation()}>
                <div className="ans">
                  <select className="select sm" value={pick[it.row] ?? it.final} onChange={(e) => setPick({ ...pick, [it.row]: e.target.value })} aria-label={`Label for row ${it.row}`}>
                    {classes.map((k) => <option key={k}>{k}</option>)}</select>
                  <button className="ab" onClick={() => act(it, 'vote')}>vote</button>
                  <button className={`ab ${it.adjudicated ? 'on' : ''}`} onClick={() => act(it, 'adjudicate')}>adjudicate</button>
                  {(it.adjudicated || it.votes.some((v) => v.annotator === who)) && <button className="ab" onClick={() => act(it, 'clear')}>clear</button>}
                </div>
              </td>
            </tr>
          })}</tbody>
        </table>
      </div>
      <div className="inbox-bar" style={{ borderTop: '1px solid var(--border-default)' }}>
        <span className="helper" style={{ fontSize: 12 }}>{nfmt(q.total)} rows · the holdout is never relabelled</span>
        <span className="spacer" />
        <button className="btn sm ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>←</button>
        <span className="mono" style={{ fontSize: 12 }}>{page + 1} / {pages}</span>
        <button className="btn sm ghost" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>→</button>
      </div>
    </div>
  )
}

function Load({ an, run, onLoaded, setErr }: { an: Annotations; run: Run; onLoaded: (a: Annotations) => void; setErr: (s: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [body, setBody] = useState(an.sample_defaults)
  const [file, setFile] = useState<File | null>(null)
  const [mode, setMode] = useState<'replace' | 'append'>('replace')
  const loaded = an.status !== 'none'
  const sample = async () => { setBusy(true); setErr(''); try { onLoaded(await api.runs.loadSampleAnnotations(run.id, body)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }
  const upload = async () => { if (!file) return; setBusy(true); setErr(''); try { onLoaded(await api.runs.uploadAnnotations(run.id, file, mode)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }
  return (
    <div className="card" data-testid="ann-load">
      <div className="card-head"><div className="left"><h3 className="card-title">{loaded ? 'Load Other Annotations' : 'Who Labelled These Rows, and How Well?'}</h3>
        <span className="helper" style={{ fontSize: 12 }}>several annotators per row → agreement, each annotator's quality, a consensus label with a quality score</span></div></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {an.sample_available && <div className="stack" style={{ gap: 'var(--space-8)' }}>
          <p className="helper" style={{ fontSize: 12.5 }}><b>The sample's simulated annotators.</b> {body.n_rows.toLocaleString()} training rows of this run, each labelled by {body.per_row} of five people of different quality, every label drawn from the sample's hidden truth:</p>
          <dl className="where tight">{an.sample_annotators.map((a) => <Fragment key={a.name}><dt>{a.name}</dt><dd>{a.profile}</dd></Fragment>)}</dl>
          <details className="an-adv"><summary className="micro-label">Advanced · rows, annotators per row, seed</summary>
            <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap', marginTop: 'var(--space-8)' }}>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">rows</span><NumberInput value={body.n_rows} min={50} max={10000} step={100} onChange={(v) => setBody({ ...body, n_rows: Math.round(v) })} label="Rows" /></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">annotators per row</span><NumberInput value={body.per_row} min={2} max={5} step={1} onChange={(v) => setBody({ ...body, per_row: Math.round(v) })} label="Per row" /></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">seed</span><NumberInput value={body.seed} min={0} max={9999} step={1} onChange={(v) => setBody({ ...body, seed: Math.round(v) })} label="Seed" /></label>
            </div>
          </details>
          <div className="row"><button className="btn primary" disabled={busy} onClick={sample} data-testid="ann-sample">{busy ? 'Loading…' : 'Load the Simulated Annotators'}</button></div>
        </div>}
        <div className="stack" style={{ gap: 'var(--space-8)' }}>
          <p className="helper" style={{ fontSize: 12.5 }}><b>Upload annotations</b>: a CSV in long format, one line per label: the row (<span className="mono">row</span>, the dataset's row number from 0, or an ID column of the dataset such as <span className="mono">ticket_id</span>), <span className="mono">annotator</span> and <span className="mono">label</span>. Holdout rows are left out.</p>
          <div className="row" style={{ gap: 'var(--space-8)', flexWrap: 'wrap' }}>
            <input type="file" accept=".csv,.tsv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Annotations file" data-testid="ann-file" />
            <select className="select" value={mode} onChange={(e) => setMode(e.target.value as 'replace' | 'append')} aria-label="Mode"><option value="replace">replace the annotations</option><option value="append" disabled={!loaded}>add to them</option></select>
            <button className="btn" disabled={busy || !file} onClick={upload} data-testid="ann-upload">Upload</button>
          </div>
        </div>
      </div>
    </div>
  )
}
