import { useEffect, useState } from 'react'
import { labels, wilson, num, specCol, type Calibration, type Layer, type LlmCols, type LlmPolicy, type LlmPreview, type Prompt } from '@/shared/api/labels'
import { pii, teamsApi } from '@/shared/api/governance'
import { useFetch } from '@/shared/hooks/useFetch'
import { useCan, why } from '@/shared/hooks/useCan'
import { Badge, CardHead, Callout, ConfirmButton, CopyId, CoverageBar, Field, Flash, Stats, Why } from '@/shared/components/DataKit'
import { day, int, pct, plural } from '@/shared/lib/format'
import { Busy, ReadErr, errText } from './parts'
import { settle } from './LabelsTab'

const DEFAULT_TEMPLATE = 'You label support tickets.\nClasses: {classes}\n\nGuidelines:\n{guidelines}\n\nTicket:\n{row}\n\nAnswer with one class, a confidence from 0 to 100 and a one-line reason.'
const usd = (v: unknown) => { const n = num(v); return n == null ? '—' : `$${n < 0.01 && n > 0 ? n.toFixed(4) : n.toFixed(2)}` }
const LIVE = /queued|running|leased|submitting/
const REASON: Record<string, string> = { target: 'the labelled column itself', free_text: 'free text not yet reviewed for PII' }
const reasonOf = (r: string) => REASON[r] ?? (r.startsWith('pii') ? `tagged ${r.slice(4).replace(/_/g, ' ')}: masked columns are not sent` : r)
/** The runs started from this screen, per layer (there is no list route): kept in the browser. */
const RUNS_KEY = (l: string) => `ensemble.labels.llmRuns.${l}`
const loadRuns = (l: string): string[] => { try { return JSON.parse(localStorage.getItem(RUNS_KEY(l)) || '[]') } catch { return [] } }
const saveRuns = (l: string, ids: string[]) => { try { localStorage.setItem(RUNS_KEY(l), JSON.stringify(ids.slice(0, 30))) } catch { /* private mode */ } }

/** LB-C: LLM labeling. The destination (off unless the team policy turns it on; the fake one by default, with why),
 *  the columns sent and excluded (PII), prompt versions, budget and estimate, preview, calibration against the
 *  calibration gold (accuracy with its Wilson interval, τ*), runs, then accept ≥ τ* or send the rest to people. */
export function LlmPanel({ layer, root, vid, v, cols, by, onBack }: { layer: Layer; root: string; vid: string; v: number; cols: string[]; by: string; onBack: () => void }) {
  const [tick, setTick] = useState(0)
  const team = useFetch(() => teamsApi.get(layer.team_id ?? 'tm_default'), [layer.team_id, tick])
  const ps = useFetch(() => labels.prompts(layer.id), [layer.id, tick])
  const tasks = useFetch(() => labels.tasks(layer.id), [layer.id])
  const tags = useFetch(() => pii.get(vid, layer.table_name).catch(() => null), [vid, layer.table_name, tick])
  const can = useCan('label.llm_run', 'dataset', root, by)
  const canTeam = useCan('team.settings', 'team', layer.team_id ?? 'tm_default', by)
  const canTag = useCan('data.tag_pii', 'dataset', root, by)
  const [runIds, setRunIds] = useState<string[]>(() => loadRuns(layer.id))
  const [sel, setSel] = useState<string | null>(null)
  const [edit, setEdit] = useState<{ template: string; context: string[]; model: string } | null>(null)
  const [maxUsd, setMaxUsd] = useState(2)
  const [nRows, setNRows] = useState(50)
  const [task, setTask] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [runSel, setRunSel] = useState<string | null>(runIds[0] ?? null)
  const [prev, setPrev] = useState<LlmPreview | null>(null)
  const [cap, setCap] = useState(20)
  useEffect(() => { if (tasks.data?.length && !task) setTask(tasks.data[0].id) }, [tasks.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const target = specCol(layer.schema?.spec)?.column ?? ''
  const prompts = (ps.data ?? []).slice().sort((a, b) => b.version - a.version)
  const p: Prompt | undefined = prompts.find((x) => x.id === sel) ?? prompts[0]
  const policy: LlmPolicy = ((team.data?.policy ?? {}) as { llm_labeling?: LlmPolicy }).llm_labeling ?? {}
  const dests = policy.enabled ? (policy.destinations ?? []) : []
  const dest = dests.includes('fake') ? 'fake' : dests[0] ?? 'off'
  const destWhy = dest === 'off' ? 'LLM labeling is off for this team (team policy llm_labeling.enabled is false: the default). An owner turns it on.'
    : dest === 'fake' ? 'The fake destination: a deterministic oracle that answers without sending anything anywhere, at $0, so the whole flow can be tried. A real model needs an API key and an admin-allowed destination.'
    : `Allowed by the team policy and the admin allowlist: rows go to ${dest}.`
  const context = edit?.context ?? p?.spec?.context ?? cols.filter((c) => c !== target).slice(0, 8)
  const masked = new Set(tags.data?.masked_columns ?? [])
  // LB-B: every column sent needs a steward-reviewed PII tag (409 pii_unreviewed); a suggested tag is not reviewed
  const tagOf = new Map((tags.data?.tags ?? []).map((t) => [t.column, t]))
  const unreviewed = tags.data ? context.filter((c) => { const t = tagOf.get(c); return !t || t.source === 'suggested' }) : []
  const plan: LlmCols = prev?.columns ?? { sent: context.filter((c) => !masked.has(c) && !unreviewed.includes(c)), excluded: [...context.filter((c) => masked.has(c)).map((c) => ({ column: c, reason: 'pii' })), ...(target ? [{ column: target, reason: 'target' }] : [])] }
  const sent = [...(plan.sent ?? []), ...(plan.masked ?? []).map((c) => `${c} (masked)`), ...(plan.free_text ?? []).map((c) => `${c} (redacted)`)]
  const excluded = plan.excluded ?? []
  const nPii = excluded.filter((x) => x.reason.startsWith('pii')).length
  const est = prev?.estimate?.est_usd != null ? (prev.estimate.est_usd / Math.max(1, prev.labels?.length ?? 20)) * nRows : dest === 'fake' ? 0 : null
  const addRun = (id: string) => { const ids = [id, ...runIds.filter((x) => x !== id)]; setRunIds(ids); saveRuns(layer.id, ids); setRunSel(id) }
  const act = async <T,>(key: string, f: () => Promise<T>, ok: (r: T) => string, isRun = false) => {
    setBusy(key); setMsg(null)
    try { const r = await f(); if (isRun) addRun((r as { id: string }).id); setMsg({ ok: true, text: ok(r) }); setTick((x) => x + 1) } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy('') }
  }
  const setPolicy = (next: LlmPolicy, text: string) => act('on', () => teamsApi.update(layer.team_id ?? 'tm_default', { policy: { ...(team.data?.policy ?? {}), llm_labeling: next } }, by), () => text)
  const turnOn = () => setPolicy({ ...policy, enabled: true, destinations: Array.from(new Set([...(policy.destinations ?? []), 'fake'])), max_usd_month: policy.max_usd_month || cap },
    `LLM labeling is on for this team: the fake destination, a monthly cap of $${policy.max_usd_month || cap} (team policy, audited).`)
  const monthCap = Number(policy.max_usd_month ?? 0)
  const save = async () => {
    if (!edit) return
    setBusy('save')
    try { const np = await labels.createPrompt(layer.id, { name: p?.name ?? `${layer.name}-prompt`, column: target, template: edit.template, context: edit.context, model: edit.model }, by); setSel(np.id); setEdit(null); setPrev(null); setTick((x) => x + 1); setMsg({ ok: true, text: `Prompt v${np.version} saved (sha ${np.prompt_sha256?.slice(0, 10) ?? '…'}).` }) }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy('') }
  }
  const review = () => act('tag', () => pii.set(vid, layer.table_name, unreviewed.map((c) => ({ column: c, tag: 'none', mask: 'none' })), by),
    () => `${plural(unreviewed.length, 'column')} reviewed as holding no PII (tag none, audited): ${unreviewed.join(', ')}.`)
  const off = dest === 'off'
  const gate = !by || !can?.allow || off
  const gateWhy = off ? 'LLM labeling is off for this team' : why(can, '')
  const sendWhy = unreviewed.length ? `${unreviewed.join(', ')} ${unreviewed.length === 1 ? 'has' : 'have'} no reviewed PII tag yet: review first` : ''
  return (
    <div className="stack lbl" style={{ gap: 'var(--space-12)' }} data-testid="lbl-llm">
      <div className="card lbl-taskbar"><div className="lbl-taskbar-row">
        <button className="btn sm ghost" onClick={onBack} data-testid="lbl-back">← Labels</button>
        <div className="lbl-taskbar-title"><span className="micro-label">LLM labeling · {layer.name}</span><b>Pre-labels from a model, calibrated against people</b></div>
        <span className="spacer" />
        <span className="lbl-dest" data-testid="lbl-llm-dest" title={destWhy}><span className="micro-label">destination</span><Badge status={dest}>{dest}</Badge></span>
      </div></div>
      {off && team.data && <Callout tone="info" title="LLM labeling is off for this team" testId="lbl-llm-off">
        {destWhy} Turning it on with the <b>fake</b> destination sends nothing outside: it is for trying the flow and for verification.
        <div className="row" style={{ gap: 6, marginTop: 6, alignItems: 'center' }}>
          <label className="helper row" style={{ gap: 4 }}>monthly cap $<input className="input mono" type="number" min={1} style={{ width: 70 }} value={cap} onChange={(e) => setCap(Number(e.target.value) || 0)} data-testid="lbl-llm-cap" /></label>
          <button className="btn sm primary" disabled={!by || !canTeam?.allow || busy === 'on' || cap <= 0} title={why(canTeam, 'Set the team policy llm_labeling: {enabled, destinations: [fake], max_usd_month}')} onClick={turnOn} data-testid="lbl-llm-enable">Turn on (fake destination)</button>
          {canTeam && !canTeam.allow && <Why>{why(canTeam)}</Why>}</div></Callout>}
      {!off && team.data && monthCap <= 0 && <Callout tone="warn" title="No monthly budget for LLM labeling" testId="lbl-llm-nocap">
        The team policy has no <span className="mono">max_usd_month</span>, so every run is refused (over_month_budget), even on the fake destination.
        <div className="row" style={{ gap: 6, marginTop: 6, alignItems: 'center' }}>
          <label className="helper row" style={{ gap: 4 }}>monthly cap $<input className="input mono" type="number" min={1} style={{ width: 70 }} value={cap} onChange={(e) => setCap(Number(e.target.value) || 0)} /></label>
          <button className="btn sm" disabled={!by || !canTeam?.allow || cap <= 0} title={why(canTeam, 'Set max_usd_month in the team policy')} onClick={() => setPolicy({ ...policy, max_usd_month: cap }, `Monthly cap set to $${cap} (team policy, audited).`)} data-testid="lbl-llm-setcap">Set the cap</button></div></Callout>}
      <Flash msg={msg} testId="lbl-llm-msg" />
      <div className="grid two">
        <div className="card" data-testid="lbl-llm-prompt">
          <CardHead title="Prompt" badge={p ? <span className="v-tag mono">v{p.version}</span> : <Badge status="none">none yet</Badge>} sub="versioned: every label records the prompt's sha"
            actions={<span className="row" style={{ gap: 6 }}>
              {prompts.length > 1 && <select className="select" value={p?.id} onChange={(e) => { setSel(e.target.value); setEdit(null); setPrev(null) }} aria-label="Prompt version" data-testid="lbl-llm-version">{prompts.map((x) => <option key={x.id} value={x.id}>v{x.version} · {x.created_by ?? ''} {day(x.created_at)}</option>)}</select>}
              {!edit && <button className="btn sm" disabled={!by || !can?.allow} title={why(can, 'Edit as a new version')} onClick={() => setEdit({ template: p?.spec?.template || DEFAULT_TEMPLATE, context, model: p?.model ?? 'fake-oracle' })} data-testid="lbl-llm-edit">{p ? 'New version…' : 'Write prompt…'}</button>}
            </span>} />
          <div className="card-body stack" style={{ gap: 8 }}>
            {ps.err && !ps.data ? <ReadErr err={ps.err} what="Prompts" /> : edit ? <>
              <Field label="Template" hint="{classes}, {guidelines}, {row} are filled in; the row holds only the columns sent"><textarea className="input mono lbl-guide-edit" rows={8} value={edit.template} onChange={(e) => setEdit({ ...edit, template: e.target.value })} data-testid="lbl-llm-template" /></Field>
              <Field label="Model" hint="a full model id, never an alias (fake-oracle on the fake destination)"><input className="input mono" value={edit.model} onChange={(e) => setEdit({ ...edit, model: e.target.value })} /></Field>
              <Field label="Columns in the row"><div className="lbl-cols">{cols.filter((c) => c !== target).map((c) => <label key={c} className={`lbl-col ${edit.context.includes(c) ? 'on' : ''} ${masked.has(c) ? 'pii' : ''}`}>
                <input type="checkbox" checked={edit.context.includes(c)} onChange={(e) => setEdit({ ...edit, context: e.target.checked ? [...edit.context, c] : edit.context.filter((x) => x !== c) })} />{masked.has(c) && '🔒 '}{c}</label>)}</div></Field>
              <div className="row" style={{ gap: 6 }}><button className="btn sm primary" disabled={busy === 'save'} onClick={save} data-testid="lbl-llm-save">Save as v{(p?.version ?? 0) + 1}</button><button className="btn sm ghost" onClick={() => setEdit(null)}>Cancel</button></div>
            </> : p ? <>
              <pre className="lbl-guidelines mono">{p.spec?.template || '(the default template)'}</pre>
              <span className="sub-line"><span className="mono">{p.model}</span> · effort {p.effort ?? 'low'} · column <span className="mono">{p.spec?.column}</span> · <CopyId value={p.prompt_sha256} n={8} />
                {p.calibration && <> · <Badge status={p.calibration.passed ? 'pass' : 'warn'}>{p.calibration.passed ? `calibrated, τ* ${p.calibration.tau_star?.toFixed(2)}` : 'calibration not passed'}</Badge></>}</span>
            </> : !ps.data ? <Busy>Reading prompts…</Busy> : <span className="muted">No prompt yet: write one, then preview it on up to 20 rows.</span>}
          </div>
        </div>
        <div className="card" data-testid="lbl-llm-pii">
          <CardHead title="What is sent" badge={<Badge status={nPii ? 'excluded' : 'clean'}>{nPii ? `${nPii} PII column${nPii > 1 ? 's' : ''} excluded` : 'no PII column'}</Badge>}
            sub={prev ? 'the column plan of the last preview' : 'PII-tagged columns never leave unless an approved request says so'} />
          <div className="card-body stack" style={{ gap: 10 }}>
            <div className="lbl-pii-row"><span className="micro-label">sent</span><span className="lbl-chips" data-testid="lbl-llm-sent">{sent.length ? sent.map((c) => <span key={c} className="pt-chip">{c}</span>) : <span className="muted">nothing</span>}</span></div>
            <div className="lbl-pii-row"><span className="micro-label">excluded</span><span className="lbl-chips" data-testid="lbl-llm-excluded">{excluded.length ? excluded.map((x) => <span key={x.column} className={`pt-chip ${x.reason.startsWith('pii') ? 'lbl-excl' : ''}`} title={reasonOf(x.reason)}>{x.reason.startsWith('pii') && '🔒 '}{x.column} <span className="muted">· {x.reason === 'target' ? 'the label' : x.reason.startsWith('pii') ? 'PII' : x.reason.replace('_', ' ')}</span></span>) : <span className="muted">none</span>}</span></div>
            {nPii > 0 && <Callout tone="info" title="Why they are excluded">Columns tagged PII on this dataset are dropped from the row before the prompt is built; free text in the sent columns is scanned and card / account numbers become <span className="mono">[REDACTED:…]</span>. Sending one unmasked needs a request with a reason, approved by an owner other than you.</Callout>}
            {unreviewed.length > 0 && <Callout tone="warn" title={`${plural(unreviewed.length, 'column')} without a reviewed PII tag`} testId="lbl-llm-unreviewed">
              Nothing is sent until a steward has looked at every column in the row: {unreviewed.map((c) => <span key={c} className="pt-chip" style={{ marginRight: 4 }}>{c}</span>)}
              <div className="row" style={{ gap: 6, marginTop: 6 }}>
                <ConfirmButton className="btn sm" disabled={!by || !canTag?.allow || busy === 'tag'} title={why(canTag, 'Tag these columns "none" (no PII), as a steward; audited')} onConfirm={review} testId="lbl-llm-review-pii"
                  confirm="They hold no PII" warning={unreviewed.join(', ')}>Review as no PII…</ConfirmButton>
                <span className="muted" style={{ fontSize: 12 }}>or tag them on the dataset's Governance card</span></div></Callout>}
            <div className="lbl-pii-row"><span className="micro-label">destination</span><span><Badge status={dest}>{dest}</Badge> <span className="muted">{destWhy}</span></span></div>
            {prev && <div className="lbl-pii-row"><span className="micro-label">preview</span><span className="muted">{plural(prev.labels?.length ?? 0, 'row')} · {usd(prev.spent_usd)} · {int(prev.pii_redactions ?? 0)} redactions</span></div>}
          </div>
        </div>
      </div>

      <div className="card" data-testid="lbl-llm-run-form">
        <CardHead title="Run" sub={`rows from v${v}; the budget is a hard cap: the run stops at it and keeps what it has`} />
        <div className="card-body stack" style={{ gap: 10 }}>
          <div className="form-row">
            <Field label="Rows to label" hint="unlabelled rows; holdout and gold excluded"><input className="input mono" type="number" min={1} style={{ width: 100 }} value={nRows} onChange={(e) => setNRows(Number(e.target.value) || 0)} data-testid="lbl-llm-n" /></Field>
            <Field label="Budget" hint="a hard cap"><span className="row" style={{ gap: 4 }}>$<input className="input mono" type="number" min={0} step={0.5} style={{ width: 80 }} value={maxUsd} onChange={(e) => setMaxUsd(Number(e.target.value) || 0)} data-testid="lbl-llm-budget" /></span></Field>
            <Field label="Task" hint="its calibration gold; where low-confidence rows go"><select className="select" value={task} onChange={(e) => setTask(e.target.value)} data-testid="lbl-llm-task">
              <option value="">none</option>{(tasks.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
            <Stats items={[{ label: 'estimated cost', value: est == null ? 'preview first' : usd(est), sub: dest === 'fake' ? 'fake destination: $0' : prev ? `from the preview, × ${int(nRows)} rows` : 'a preview prices it', testId: 'lbl-llm-est' },
              { label: 'team monthly cap', value: monthCap ? usd(monthCap) : 'none', sub: 'team policy; runs reserve their cap' }]} />
          </div>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <button className="btn sm" disabled={gate || !p || !!busy || !!sendWhy} title={gateWhy || sendWhy || (!p ? 'Write a prompt first' : 'Label up to 20 rows to see what it does (audited; counts toward the budget)')}
              onClick={() => act('prev', async () => { const r = await labels.preview(p!.id, 20, vid, by, dest); setPrev(r); return r }, (r) => `Preview: ${plural(r.labels?.length ?? 0, 'row')} labelled on ${dest}, ${usd(r.spent_usd)} spent.`)} data-testid="lbl-llm-preview">{busy === 'prev' ? 'Previewing…' : 'Preview on 20 rows'}</button>
            <button className="btn sm" disabled={gate || !p || !task || !!busy || !!sendWhy} title={gateWhy || sendWhy || (!task ? 'A task with calibration gold' : 'Label the calibration gold and find τ*')}
              onClick={() => act('cal', () => labels.calibrate(p!.id, { version_id: vid, task_id: task, max_usd: maxUsd, destination: dest }, by), (r) => `Calibration ${r.id} started on ${plural(r.rows ?? 0, 'gold row')}.`, true)} data-testid="lbl-llm-calibrate">{busy === 'cal' ? 'Starting…' : 'Calibrate against gold'}</button>
            <ConfirmButton className="btn sm primary" disabled={gate || !p || !!busy || nRows <= 0 || !!sendWhy} title={gateWhy || sendWhy || `Label ${int(nRows)} rows, at most $${maxUsd}`} testId="lbl-llm-run" confirm={`Run on ${int(nRows)} rows`}
              warning={`${dest} · cap $${maxUsd} · ${sent.length} columns sent`}
              onConfirm={() => act('run', () => labels.run(p!.id, { version_id: vid, selector: { strategy: 'unlabelled', n: nRows }, k: 1, max_usd: maxUsd, stop_at_cap: true, destination: dest, ...(task ? { task_id: task } : {}) }, by), (r) => `Run ${r.id} started on ${plural(r.rows ?? nRows, 'row')}.`, true)}>{busy === 'run' ? 'Starting…' : 'Run…'}</ConfirmButton>
            {(gate && gateWhy) || sendWhy ? <Why>{gateWhy || sendWhy}</Why> : null}
          </div>
          {prev && (prev.labels?.length ?? 0) > 0 && <details data-testid="lbl-llm-preview-rows"><summary>Preview: {plural(prev.labels!.length, 'row')}</summary>
            <table className="table"><tbody>{prev.labels!.map((y) => <tr key={y.row_id}><td className="mono">#{y.row_id}</td><td><b>{y.label}</b></td><td className="mono muted">{y.confidence != null ? Number(y.confidence).toFixed(2) : ''}</td><td className="muted lbl-ctx-v">{y.rationale}</td></tr>)}</tbody></table></details>}
        </div>
      </div>

      {runIds.length > 0 && <div className="card" data-testid="lbl-llm-runs">
        <CardHead title="Runs" badge={<Badge status="none">{runIds.length}</Badge>} sub="calibration and labeling runs started here: rows, spend against the cap, PII redactions" />
        <div className="card-body"><div className="table-wrap"><table className="table">
          <thead><tr><th>Run</th><th>Purpose</th><th>Rows</th><th className="r">Spent / cap</th><th className="r">Redactions</th><th>Status</th></tr></thead>
          <tbody>{runIds.map((id) => <RunRow key={id} id={id} on={id === runSel} pick={() => setRunSel(id)} />)}</tbody></table></div></div>
      </div>}
      {runSel && <RunReport key={runSel} id={runSel} layer={layer} vid={vid} task={task} gate={gate} gateWhy={gateWhy} by={by} onDone={(t) => { setMsg({ ok: true, text: t }); setTick((x) => x + 1) }} onErr={(e) => setMsg({ ok: false, text: errText(e) })} />}
    </div>
  )
}

function RunRow({ id, on, pick }: { id: string; on: boolean; pick: () => void }) {
  const r = useFetch(() => labels.llmRun(id), [id], { pollMs: 1500, while: (d) => !d || LIVE.test(d.status) })
  const x = r.data
  if (!x) return <tr><td><CopyId value={id} /></td><td colSpan={5} className="muted">{r.err || '…'}</td></tr>
  return (
    <tr className={on ? 'sel' : ''} onClick={pick} style={{ cursor: 'pointer' }} data-testid={`lbl-llm-runrow-${x.purpose}`}>
      <td><CopyId value={x.id} /><span className="sub-line">{x.created_by} · {day(x.created_at)}</span></td><td><Badge status="none">{x.purpose}</Badge></td>
      <td><CoverageBar m={x.rows_done ?? 0} N={x.rows_total ?? x.rows_done ?? null} /> <span className="mono muted">{int(x.rows_done)}{x.rows_cached ? ` · ${int(x.rows_cached)} cached` : ''}</span></td>
      <td className="r mono">{usd(x.spent_usd)} / {usd(x.max_usd)}</td><td className="r mono">{int(x.pii_redactions ?? 0)}</td>
      <td><Badge status={x.status}>{x.status.replace(/_/g, ' ')}</Badge></td>
    </tr>
  )
}

function RunReport({ id, layer, vid, task, by, gate, gateWhy, onDone, onErr }: { id: string; layer: Layer; vid: string; task: string; by: string; gate: boolean; gateWhy: string | undefined
  onDone: (t: string) => void; onErr: (e: unknown) => void }) {
  const r = useFetch(() => labels.llmRun(id), [id], { pollMs: 1500, while: (d) => !d || LIVE.test(d.status) })
  const pr = useFetch(r.data ? () => labels.prompts(layer.id).then((ps) => ps.find((p) => p.id === r.data!.prompt_id) ?? null) : null, [r.data?.prompt_id, r.data?.status])
  if (!r.data) return r.err ? <Callout tone="danger">{r.err}</Callout> : <Busy>Reading the run…</Busy>
  const x = r.data
  const cal: Calibration | null = x.result?.calibration ?? (x.purpose === 'label' ? pr.data?.calibration ?? null : null)
  const n = cal?.n ?? 0
  const k = cal?.accuracy != null ? Math.round(cal.accuracy * n) : 0
  const ci = n ? wilson(k, n) : null
  const tau = x.result?.calibration_used?.tau_star ?? cal?.tau_star ?? null
  const cols = x.columns_sent ?? {}
  return (
    <div className="card" data-testid="lbl-llm-report">
      <CardHead title={<>Run <span className="mono">{x.id}</span></>} badge={<Badge status={x.status}>{x.status.replace(/_/g, ' ')}</Badge>}
        sub={`${x.purpose} · ${x.destination} · sent: ${[...(cols.sent ?? []), ...(cols.masked ?? [])].join(', ') || '—'}${cols.excluded?.length ? ` · excluded: ${cols.excluded.map((c) => c.column).join(', ')}` : ''}`} />
      <div className="card-body stack" style={{ gap: 10 }}>
        <Stats items={[{ label: 'rows', value: `${int(x.rows_done)}`, sub: x.rows_cached ? `${int(x.rows_cached)} from the cache` : undefined }, { label: 'spent', value: usd(x.spent_usd), sub: `cap ${usd(x.max_usd)}${x.result?.budget_stopped ? ' · stopped at the cap' : ''}` },
          { label: 'tokens in / out', value: `${int(x.tokens_in ?? 0)} / ${int(x.tokens_out ?? 0)}` }, { label: 'PII redactions', value: int(x.pii_redactions ?? 0) },
          ...(x.purpose === 'label' && x.result ? [{ label: 'accepted ≥ τ*', value: int(x.result.auto_accepted ?? 0), sub: `${int(x.result.prelabel_only ?? 0)} pre-labels only`, testId: 'lbl-llm-auto' }] : [])]} />
        {LIVE.test(x.status) && <Busy>Labelling… {int(x.rows_done)} rows so far</Busy>}
        {cal ? <div className="lbl-cal" data-testid="lbl-llm-calibration">
          <div className="lbl-cal-head"><span className="micro-label">{x.purpose === 'calibrate' ? 'calibration against gold' : 'the calibration this run used'}</span>
            <b className="mono lbl-cal-acc" data-testid="lbl-llm-acc">{pct(cal.accuracy ?? null)}</b>
            <span className="mono muted" title="Wilson score interval, 95 %" data-testid="lbl-llm-ci">95 % CI {ci ? `${pct(ci[0])} – ${pct(ci[1])}` : '—'}</span>
            <span className="muted">on {plural(n, 'gold row')}</span>
            <Badge status={cal.passed ? 'pass' : 'warn'}>{cal.passed ? 'passed' : 'not passed'}</Badge>
            {tau != null && <span className="pt-chip" title="the confidence above which LLM labels reach the target accuracy (found on one half, certified on the other)">τ* = {tau.toFixed(2)}{cal.certify ? ` · covers ${pct(cal.certify.coverage, 0)} · ${pct(cal.certify.acc, 0)} accurate (≥ ${pct(cal.certify.acc_lo ?? null, 0)})` : ''}</span>}
            {cal.target != null && <span className="muted">target {pct(cal.target, 0)}</span>}
          </div>
          {ci && <div className="lbl-ci" aria-hidden><span className="mono muted">0</span><span className="lbl-ci-track"><i style={{ left: `${ci[0] * 100}%`, width: `${(ci[1] - ci[0]) * 100}%` }} /><b style={{ left: `${(cal.accuracy ?? 0) * 100}%` }} /></span><span className="mono muted">100 %</span></div>}
          {cal.per_class && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>{Object.entries(cal.per_class).map(([c, s]) => <span key={c} className="pt-chip">{c} <b className="mono">{pct(s.recall ?? s.accuracy ?? null, 0)}</b> <span className="muted">n {s.n}</span></span>)}</div>}
          {(cal.thresholds?.length ?? 0) > 0 && <details><summary>Coverage and accuracy by threshold</summary><table className="table lbl-rel"><thead><tr><th>τ</th><th>Coverage</th><th className="r">Accuracy (lower 95 %)</th><th className="r">n</th></tr></thead>
            <tbody>{cal.thresholds!.map((b) => <tr key={b.tau} className={b.tau === tau ? 'sel' : ''}><td className="mono">{b.tau.toFixed(2)}</td><td><CoverageBar value={b.coverage} /></td><td className="r mono">{pct(b.acc, 0)} ({pct(b.acc_lo ?? null, 0)})</td><td className="r mono">{b.n}</td></tr>)}</tbody></table></details>}
        </div> : x.purpose === 'label' && <Callout tone="info" title="Not calibrated">No passed calibration for this prompt: every LLM label stays a pre-label for people. Calibrate against the task's calibration gold to find τ*.</Callout>}
        {x.purpose === 'label' && !LIVE.test(x.status) && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <button className="btn sm primary" disabled={gate || tau == null} title={gateWhy || (tau == null ? 'Calibrate first: accepting uses τ*' : `Take a snapshot that applies LLM labels at confidence ≥ ${tau.toFixed(2)}; the rest stay pre-labels`)}
            onClick={() => labels.snapshot(layer.id, { method: 'majority', accept: { llm: { min_confidence: tau } } }, by).then(settle).then((s) => onDone(`Accepted ≥ τ* in snapshot ${(s as { id?: string }).id ?? ''}: ${int((s as { counts?: { applied?: number } }).counts?.applied ?? 0)} labels applied.`)).catch(onErr)} data-testid="lbl-llm-accept">Accept ≥ τ* (snapshot)</button>
          <button className="btn sm" disabled={gate || !task} title={gateWhy || (!task ? 'Pick the task' : 'Queue the rows below τ* for people, with the LLM answer as a pre-label (never on gold or blind items)')}
            onClick={() => labels.fill(task, { strategy: 'llm_low_conf', n: Math.max(1, x.rows_done ?? 50), version_id: vid }, by).then((f) => onDone(`${int((f as { items?: number }).items ?? 0)} low-confidence rows sent to people, the LLM answer as a pre-label.`)).catch(onErr)} data-testid="lbl-llm-humans">Send the rest to people</button>
          {tau == null && !gate && <Why>{cal && !cal.passed ? `Accept needs τ*: the calibration did not reach the ${pct(cal.target ?? null, 0)} target, so every LLM label stays a pre-label for people.` : 'Accept needs τ*: calibrate first.'}</Why>}
        </div>}
        {x.error && <Callout tone="danger">{x.error}</Callout>}
        {x.result?.pii_values_found ? <span className="muted">{int(x.result.pii_values_found)} PII values found in free text and redacted before sending.</span> : null}
      </div>
    </div>
  )
}
