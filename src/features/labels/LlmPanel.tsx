import { useEffect, useState } from 'react'
import { labels, wilson, arr, num, specCol, type Calibration, type Layer, type LlmRun, type Prompt } from '@/shared/api/labels'
import { pii } from '@/shared/api/governance'
import { useFetch } from '@/shared/hooks/useFetch'
import { useCan, why } from '@/shared/hooks/useCan'
import { Badge, CardHead, Callout, ConfirmButton, CopyId, CoverageBar, Field, Flash, Stats, Why } from '@/shared/components/DataKit'
import { day, int, pct, plural } from '@/shared/lib/format'
import { Busy, ReadErr, errText, show } from './parts'
import { settle } from './LabelsTab'

const DEFAULT_TEMPLATE = 'You label rows of a table.\nClasses: {classes}\n\nGuidelines:\n{guidelines}\n\nRow:\n{row}\n\nAnswer with one class and a confidence from 0 to 1.'
const usd = (v: unknown) => { const n = num(v); return n == null ? '—' : `$${n < 0.01 && n > 0 ? n.toFixed(4) : n.toFixed(2)}` }
/** A rough estimate when the API has not priced the run yet: ~ (300 + 12 per context column) tokens in, 20 out per row,
 *  at the fake destination $0, otherwise $3 / $15 per million tokens. */
const roughUsd = (rows: number, nCols: number, dest: string) => (dest === 'fake' || dest === 'off' ? 0 : rows * (((300 + 12 * nCols) * 3 + 20 * 15) / 1e6))

/** LB-C: LLM labeling. Prompt versions, the PII guard (what is sent, what is excluded and why), the destination (off /
 *  fake unless a team policy and a key allow a real one), budget and estimate, preview, calibration against the gold
 *  set (accuracy with a Wilson interval and τ*), runs, and accept or send to people. */
export function LlmPanel({ layer, root, vid, v, cols, by, onBack }: { layer: Layer; root: string; vid: string; v: number; cols: string[]; by: string; onBack: () => void }) {
  const [tick, setTick] = useState(0)
  const pol = useFetch(() => labels.llmPolicy(layer.id), [layer.id])
  const ps = useFetch(() => labels.prompts(layer.id), [layer.id, tick])
  const runs = useFetch(() => labels.llmRuns(layer.id), [layer.id, tick], { pollMs: 2000, while: (d) => !!d?.some((r) => /queued|running|leased|submitting/.test(r.status)) })
  const tasks = useFetch(() => labels.tasks(layer.id), [layer.id])
  const tags = useFetch(() => pii.get(vid, layer.table_name), [vid, layer.table_name])
  const can = useCan('label.llm_run', 'dataset', root, by)
  const [sel, setSel] = useState<string | null>(null)
  const [edit, setEdit] = useState<{ template: string; context: string[]; model: string } | null>(null)
  const [maxUsd, setMaxUsd] = useState(5)
  const [nRows, setNRows] = useState(50)
  const [task, setTask] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [runSel, setRunSel] = useState<string | null>(null)
  const prompts = (ps.data ?? []).slice().sort((a, b) => b.version - a.version)
  const p: Prompt | undefined = prompts.find((x) => x.id === sel) ?? prompts[0]
  useEffect(() => { if (tasks.data?.length && !task) setTask(tasks.data[0].id) }, [tasks.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const policy = pol.data
  const dest = policy?.enabled === false ? 'off' : policy?.destination ?? 'fake'
  const destWhy = policy?.reason ?? (dest === 'fake' ? 'No real destination is allowed for this team (no API key or the team policy does not enable one): the fake destination answers deterministically so the flow can be tried end to end, at $0.'
    : dest === 'off' ? 'LLM labeling is off in this team\'s policy: a maintainer turns it on in Settings › Teams.' : 'Allowed by the team policy.')
  const context = edit?.context ?? (p ? arr(p.context ?? p.context_json) : cols.filter((c) => c !== specCol(layer.schema?.spec)?.column).slice(0, 8))
  const masked = new Set([...(tags.data?.masked_columns ?? []), ...(policy?.pii?.masked ?? [])])
  const excluded = policy?.pii?.excluded ?? context.filter((c) => masked.has(c))
  const sent = policy?.pii?.sent ?? context.filter((c) => !excluded.includes(c))
  const est = roughUsd(nRows, sent.length, dest)
  const cur = (runs.data ?? []).find((r) => r.id === runSel) ?? (runs.data ?? [])[0]
  const act = async (key: string, f: () => Promise<unknown>, ok: (r: LlmRun) => string) => {
    setBusy(key); setMsg(null)
    try { const r = await settle(await f()) as LlmRun; setRunSel(r?.id ?? null); setMsg({ ok: true, text: ok(r) }); setTick((x) => x + 1) } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy('') }
  }
  const save = async () => {
    if (!edit) return
    setBusy('save')
    try { const np = await labels.createPrompt(layer.id, { name: p?.name ?? `${layer.name}-prompt`, template: edit.template, context: edit.context, model: edit.model }, by); setSel(np.id); setEdit(null); setTick((x) => x + 1); setMsg({ ok: true, text: `Prompt v${np.version} saved (${np.prompt_sha256?.slice(0, 10) ?? 'hashed'}).` }) }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy('') }
  }
  const off = dest === 'off'
  const gate = !by || !can?.allow || off
  const gateWhy = off ? 'LLM labeling is off for this team' : why(can, '')
  return (
    <div className="stack lbl" style={{ gap: 'var(--space-12)' }} data-testid="lbl-llm">
      <div className="card lbl-taskbar"><div className="lbl-taskbar-row">
        <button className="btn sm ghost" onClick={onBack} data-testid="lbl-back">← Labels</button>
        <div className="lbl-taskbar-title"><span className="micro-label">LLM labeling · {layer.name}</span><b>Pre-labels from a model, calibrated against people</b></div>
        <span className="spacer" />
        <span className="lbl-dest" data-testid="lbl-llm-dest" title={destWhy}><span className="micro-label">destination</span><Badge status={dest}>{dest}</Badge></span>
      </div></div>
      <Flash msg={msg} testId="lbl-llm-msg" />
      <div className="grid two">
        <div className="card" data-testid="lbl-llm-prompt">
          <CardHead title="Prompt" badge={p ? <span className="v-tag mono">v{p.version}</span> : <Badge status="none">none yet</Badge>} sub="versioned: every label records the prompt's sha"
            actions={<span className="row" style={{ gap: 6 }}>
              {prompts.length > 1 && <select className="select" value={p?.id} onChange={(e) => { setSel(e.target.value); setEdit(null) }} aria-label="Prompt version" data-testid="lbl-llm-version">{prompts.map((x) => <option key={x.id} value={x.id}>v{x.version} · {x.created_by ?? ''} {day(x.created_at)}</option>)}</select>}
              {!edit && <button className="btn sm" disabled={gate} title={gateWhy || 'Edit as a new version'} onClick={() => setEdit({ template: p?.template ?? DEFAULT_TEMPLATE, context, model: p?.model ?? 'claude-haiku-5-5' })} data-testid="lbl-llm-edit">{p ? 'New version…' : 'Write prompt…'}</button>}
            </span>} />
          <div className="card-body stack" style={{ gap: 8 }}>
            {ps.err && !ps.data ? <ReadErr err={ps.err} what="Prompts" /> : edit ? <>
              <Field label="Template" hint="{classes}, {guidelines}, {row} are filled in; the row holds only the columns sent"><textarea className="input mono lbl-guide-edit" rows={8} value={edit.template} onChange={(e) => setEdit({ ...edit, template: e.target.value })} data-testid="lbl-llm-template" /></Field>
              <Field label="Model" hint="a full model id, never an alias"><input className="input mono" value={edit.model} onChange={(e) => setEdit({ ...edit, model: e.target.value })} /></Field>
              <Field label="Columns in the row"><div className="lbl-cols">{cols.filter((c) => c !== specCol(layer.schema?.spec)?.column).map((c) => <label key={c} className={`lbl-col ${edit.context.includes(c) ? 'on' : ''} ${masked.has(c) ? 'pii' : ''}`}>
                <input type="checkbox" checked={edit.context.includes(c)} onChange={(e) => setEdit({ ...edit, context: e.target.checked ? [...edit.context, c] : edit.context.filter((x) => x !== c) })} />{masked.has(c) && '🔒 '}{c}</label>)}</div></Field>
              <div className="row" style={{ gap: 6 }}><button className="btn sm primary" disabled={busy === 'save'} onClick={save} data-testid="lbl-llm-save">Save as v{(p?.version ?? 0) + 1}</button><button className="btn sm ghost" onClick={() => setEdit(null)}>Cancel</button></div>
            </> : p ? <>
              <pre className="lbl-guidelines mono">{p.template}</pre>
              <span className="sub-line"><span className="mono">{p.model}</span> · effort {p.effort ?? 'low'} · <CopyId value={p.prompt_sha256} n={8} /></span>
            </> : !ps.data ? <Busy>Reading prompts…</Busy> : <span className="muted">No prompt yet: write one to preview on 20 rows.</span>}
          </div>
        </div>
        <div className="card" data-testid="lbl-llm-pii">
          <CardHead title="What is sent" badge={<Badge status={excluded.length ? 'excluded' : 'clean'}>{excluded.length ? `${excluded.length} excluded` : 'no PII column'}</Badge>} sub="PII-tagged columns never leave unless an approved request says so" />
          <div className="card-body stack" style={{ gap: 10 }}>
            <div className="lbl-pii-row"><span className="micro-label">sent</span><span className="lbl-chips" data-testid="lbl-llm-sent">{sent.length ? sent.map((c) => <span key={c} className="pt-chip">{c}</span>) : <span className="muted">nothing</span>}</span></div>
            <div className="lbl-pii-row"><span className="micro-label">excluded</span><span className="lbl-chips" data-testid="lbl-llm-excluded">{excluded.length ? excluded.map((c) => <span key={c} className="pt-chip lbl-excl" title={policy?.pii?.reason?.[c] ?? 'tagged PII: masked columns are not sent to an LLM'}>🔒 {c} <span className="muted">· PII</span></span>) : <span className="muted">none</span>}</span></div>
            {excluded.length > 0 && <Callout tone="info" title="Why they are excluded">Columns tagged PII on this dataset are dropped from the row before the prompt is built; free text in the sent columns is scanned and card / account numbers become <span className="mono">[REDACTED:…]</span>. To send one unmasked, an owner other than you approves a request with a reason.</Callout>}
            <div className="lbl-pii-row"><span className="micro-label">destination</span><span><Badge status={dest}>{dest}</Badge> <span className="muted">{destWhy}</span></span></div>
            {policy?.pii?.unmasked_request && <div className="lbl-pii-row"><span className="micro-label">unmask request</span><Badge status={policy.pii.unmasked_request.status} /></div>}
          </div>
        </div>
      </div>

      <div className="card" data-testid="lbl-llm-run-form">
        <CardHead title="Run" sub={`rows from v${v}; budget is a hard cap: the run stops at it and keeps what it has`} />
        <div className="card-body stack" style={{ gap: 10 }}>
          <div className="form-row">
            <Field label="Rows to label" hint="unlabelled rows, holdout excluded"><input className="input mono" type="number" min={1} style={{ width: 100 }} value={nRows} onChange={(e) => setNRows(Number(e.target.value) || 0)} data-testid="lbl-llm-n" /></Field>
            <Field label="Budget (max)"><span className="row" style={{ gap: 4 }}>$<input className="input mono" type="number" min={0} step={0.5} style={{ width: 80 }} value={maxUsd} onChange={(e) => setMaxUsd(Number(e.target.value) || 0)} data-testid="lbl-llm-budget" /></span></Field>
            <Field label="Gold set from task" hint="calibration grades the model on it"><select className="select" value={task} onChange={(e) => setTask(e.target.value)} data-testid="lbl-llm-task">
              <option value="">none</option>{(tasks.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
            <Stats items={[{ label: 'estimated cost', value: usd(cur?.purpose === 'preview' && cur.est_usd != null ? cur.est_usd : est), sub: dest === 'fake' ? 'fake: $0' : `${int(nRows)} rows × ${sent.length} cols`, testId: 'lbl-llm-est' }]} />
          </div>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <button className="btn sm" disabled={gate || !p || !!busy} title={gateWhy || (!p ? 'Write a prompt first' : 'Label 20 rows to see what it does (audited; counts toward the budget)')} onClick={() => act('prev', () => labels.preview(p!.id, 20, vid, by), (r) => `Preview: ${int(r.rows_done ?? 20)} rows labelled on ${r.destination ?? dest}, ${usd(r.spent_usd)} spent.`)} data-testid="lbl-llm-preview">{busy === 'prev' ? 'Previewing…' : 'Preview on 20 rows'}</button>
            <button className="btn sm" disabled={gate || !p || !task || !!busy} title={gateWhy || (!task ? 'A task with a gold set' : 'Label the gold rows and compare')} onClick={() => act('cal', () => labels.calibrate(p!.id, { version_id: vid, task_id: task, max_usd: maxUsd }, by), (r) => `Calibrated on ${int(r.calibration?.n ?? r.rows_done)} gold rows.`)} data-testid="lbl-llm-calibrate">{busy === 'cal' ? 'Calibrating…' : 'Calibrate against gold'}</button>
            <ConfirmButton className="btn sm primary" disabled={gate || !p || !!busy || nRows <= 0} title={gateWhy || `Label ${int(nRows)} rows, at most $${maxUsd}`} testId="lbl-llm-run" confirm={`Run on ${int(nRows)} rows`}
              warning={`${dest} · cap $${maxUsd} · ${sent.length} columns sent`} onConfirm={() => act('run', () => labels.run(p!.id, { version_id: vid, selector: { strategy: 'unlabelled', n: nRows }, k: 1, mode: 'online', max_usd: maxUsd }, by), (r) => `Run ${r.id} ${r.status}: ${int(r.rows_done)} of ${int(r.rows_total)} rows, ${usd(r.spent_usd)} spent.`)}>{busy === 'run' ? 'Running…' : 'Run…'}</ConfirmButton>
            {gate && gateWhy && <Why>{gateWhy}</Why>}
          </div>
        </div>
      </div>

      <div className="card" data-testid="lbl-llm-runs">
        <CardHead title="Runs" badge={runs.data ? <Badge status="none">{runs.data.length}</Badge> : undefined} sub="preview, calibration and labeling runs: rows, spend against the cap, PII redactions" />
        <div className="card-body">
          {runs.err && !runs.data ? <ReadErr err={runs.err} what="Runs" /> : !runs.data ? <Busy>Reading runs…</Busy> : !runs.data.length ? <span className="muted">No run yet.</span>
            : <div className="table-wrap"><table className="table">
              <thead><tr><th>Run</th><th>Purpose</th><th>Rows</th><th className="r">Spent / cap</th><th className="r">Redactions</th><th>Status</th><th>Made</th></tr></thead>
              <tbody>{runs.data.map((r) => <tr key={r.id} className={r.id === cur?.id ? 'sel' : ''} onClick={() => setRunSel(r.id)} style={{ cursor: 'pointer' }} data-testid={`lbl-llm-runrow-${r.purpose}`}>
                <td><CopyId value={r.id} /></td><td><Badge status="none">{r.purpose}</Badge></td>
                <td><CoverageBar m={r.rows_done ?? 0} N={r.rows_total ?? null} /> <span className="mono muted">{int(r.rows_done)} / {int(r.rows_total)}{r.rows_cached ? ` · ${int(r.rows_cached)} cached` : ''}</span></td>
                <td className="r mono">{usd(r.spent_usd)} / {usd(r.max_usd)}</td><td className="r mono">{int(r.pii_redactions ?? 0)}</td>
                <td><Badge status={r.status} /></td><td className="muted">{r.created_by} · {day(r.created_at)}</td></tr>)}</tbody></table></div>}
        </div>
      </div>
      {cur && <RunReport key={cur.id} id={cur.id} tasks={tasks.data ?? []} task={task} by={by} gate={gate} gateWhy={gateWhy} calibration={p?.calibration ?? null} onDone={(t) => { setMsg({ ok: true, text: t }); setTick((x) => x + 1) }} onErr={(e) => setMsg({ ok: false, text: errText(e) })} />}
    </div>
  )
}

function RunReport({ id, tasks, task, by, gate, gateWhy, calibration, onDone, onErr }: { id: string; tasks: { id: string; name: string }[]; task: string; by: string; gate: boolean; gateWhy: string | undefined
  calibration: Calibration | null; onDone: (t: string) => void; onErr: (e: unknown) => void }) {
  const r = useFetch(() => labels.llmRun(id), [id], { pollMs: 2000, while: (d) => !!d && /queued|running|leased|submitting/.test(d.status) })
  const [to, setTo] = useState(task)
  if (!r.data) return r.err ? <Callout tone="danger">{r.err}</Callout> : <Busy>Reading the run…</Busy>
  const x = r.data
  const cal = x.calibration ?? (x.purpose === 'calibrate' ? null : calibration)
  const n = cal?.n ?? 0, k = cal?.correct ?? (cal?.accuracy != null && n ? Math.round(cal.accuracy * n) : 0)
  const ci = cal?.wilson ?? cal?.ci ?? (n ? wilson(k, n) : null)
  const tau = cal?.tau_star ?? cal?.tau ?? null
  return (
    <div className="card" data-testid="lbl-llm-report">
      <CardHead title={<>Run <span className="mono">{x.id}</span></>} badge={<Badge status={x.status} />} sub={`${x.purpose} · ${x.destination} · columns sent: ${arr(x.columns_sent).join(', ') || '—'}`} />
      <div className="card-body stack" style={{ gap: 10 }}>
        <Stats items={[{ label: 'rows', value: `${int(x.rows_done)} / ${int(x.rows_total)}` }, { label: 'spent', value: usd(x.spent_usd), sub: `cap ${usd(x.max_usd)}` },
          { label: 'tokens in / out', value: `${int(x.tokens_in ?? 0)} / ${int(x.tokens_out ?? 0)}` }, { label: 'PII redactions', value: int(x.pii_redactions ?? 0) }]} />
        {cal ? <div className="lbl-cal" data-testid="lbl-llm-calibration">
          <div className="lbl-cal-head"><span className="micro-label">calibration against gold</span>
            <b className="mono lbl-cal-acc" data-testid="lbl-llm-acc">{pct(cal.accuracy ?? (n ? k / n : null))}</b>
            <span className="mono muted" title="Wilson score interval, 95 %">95 % CI {ci ? `${pct(ci[0])} – ${pct(ci[1])}` : '—'}</span>
            <span className="muted">on {plural(n, 'gold row')}</span>
            {tau != null && <span className="pt-chip" title="the confidence threshold above which LLM labels are as accurate as the target">τ* = {tau.toFixed(2)}{cal.coverage_at_tau != null ? ` · covers ${pct(cal.coverage_at_tau, 0)}` : ''}{cal.accuracy_at_tau != null ? ` · ${pct(cal.accuracy_at_tau, 0)} accurate` : ''}</span>}
          </div>
          {ci && <div className="lbl-ci" aria-hidden><span className="lbl-ci-track"><i style={{ left: `${ci[0] * 100}%`, width: `${(ci[1] - ci[0]) * 100}%` }} /><b style={{ left: `${(cal.accuracy ?? k / Math.max(1, n)) * 100}%` }} /></span><span className="mono muted">0</span><span className="spacer" /><span className="mono muted">100 %</span></div>}
          {cal.per_class && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>{Object.entries(cal.per_class).map(([c, s]) => <span key={c} className="pt-chip">{c} <b className="mono">{pct(s.accuracy, 0)}</b> <span className="muted">n {s.n}</span></span>)}</div>}
          {(cal.reliability?.length ?? 0) > 0 && <table className="table lbl-rel"><thead><tr><th>Confidence</th><th className="r">Accuracy</th><th className="r">n</th></tr></thead>
            <tbody>{cal.reliability!.map((b) => <tr key={b.bin}><td className="mono">{b.conf.toFixed(2)}</td><td className="r"><CoverageBar value={b.acc} /></td><td className="r mono">{b.n}</td></tr>)}</tbody></table>}
        </div> : x.purpose !== 'preview' && <Callout tone="info" title="Not calibrated yet">Calibrate against a task's gold set first: accepting needs τ*.</Callout>}
        {(x.results?.length ?? 0) > 0 && <details><summary>{plural(x.results!.length, 'row')} labelled</summary><table className="table"><tbody>{x.results!.slice(0, 20).map((y) => <tr key={y.row_id}><td className="mono">#{y.row_id}</td><td><b>{show(y.value)}</b></td><td className="mono muted">{y.confidence != null ? Number(y.confidence).toFixed(2) : ''}</td>{y.gold !== undefined && <td>{show(y.gold) === show(y.value) ? <Badge status="ok">matches gold</Badge> : <Badge status="mismatch">gold {show(y.gold)}</Badge>}</td>}</tr>)}</tbody></table></details>}
        {x.purpose === 'label' && x.status === 'succeeded' && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <button className="btn sm primary" disabled={gate || tau == null} title={gateWhy || (tau == null ? 'Calibrate first: accepting uses τ*' : `Accept the labels at confidence ≥ ${tau.toFixed(2)}; the rest stay pre-labels`)}
            onClick={() => labels.acceptRun(x.id, { tau }, by).then((a) => onDone(`${int(a.accepted ?? 0)} LLM labels accepted at ≥ τ*; the rest stay pre-labels for people.`)).catch(onErr)} data-testid="lbl-llm-accept">Accept ≥ τ*</button>
          <select className="select" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Task">{tasks.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
          <button className="btn sm" disabled={gate || !to} title={gateWhy || 'Queue these rows for people, with the LLM answer as a pre-label (never on gold or blind items)'}
            onClick={() => labels.toHumans(x.id, { task_id: to, tau }, by).then((a) => onDone(`${int(a.added ?? 0)} rows sent to people with the LLM's pre-label.`)).catch(onErr)} data-testid="lbl-llm-humans">Send to people</button>
          {(x.accepted != null || x.sent_to_humans != null) && <span className="muted">{int(x.accepted ?? 0)} accepted · {int(x.sent_to_humans ?? 0)} sent to people</span>}
        </div>}
        {x.status === 'running' && <button className="btn sm ghost" onClick={() => labels.cancelRun(x.id, by).then(() => r.reload()).catch(onErr)}>Cancel</button>}
        {x.error && <Callout tone="danger">{x.error}</Callout>}
      </div>
    </div>
  )
}
