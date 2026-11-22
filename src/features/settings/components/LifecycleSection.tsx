import { useEffect, useState } from 'react'
import { holds as holdsApi, teamsApi, type Team } from '@/shared/api/governance'
import { gc, purges, retention, RT_CATEGORIES, RT_KEYS, RT_WHAT, type GcArmed, type GcPlan, type GcRun, type Periods, type PurgeRow, type RtCategory, type RtItem, type RtPlan, type RtRun } from '@/shared/api/lifecycle'
import { useApi } from '@/shared/api/mode'
import { useCan, why } from '@/shared/hooks/useCan'
import { useTeam } from '@/shared/lib/team'
import { PurgeDetail, lifecycleLine, useTeamNames } from '@/shared/components/PurgeDetail'
import { Badge, CardHead, Callout, CopyId, Steps, Why } from '@/shared/components/DataKit'

/** U3 (P24, plan §11 item 3): Settings › Data lifecycle. Retention per team (plan → dry run → run), GC (armed by a
 *  verify-all pass; dry run → run) and the purges waiting for a team. Nothing here runs on a timer and no category has a
 *  default period: a person starts every step, and every step is audited in the team's chain. Every button is greyed
 *  by /authz/check with the API's reason. */

import { when, day, bytes as kb } from '@/shared/lib/format'

export function LifecycleSection() {
  const { mode } = useApi()
  const { actor, team: current } = useTeam()
  const [all, setAll] = useState<Team[]>([])
  const [sel, setSel] = useState<string>('')
  useEffect(() => { if (mode === 'live' && actor) teamsApi.list(true).then(setAll).catch(() => setAll([])) }, [mode, actor])
  const choices = all.filter((t) => t.state !== 'archived')
  const t = choices.find((x) => x.id === sel) ?? choices.find((x) => x.id === current?.id) ?? choices.find((x) => x.my_role) ?? choices[0] ?? null
  if (mode !== 'live') return <div className="card card-body helper" data-testid="settings-lifecycle">Retention, GC and purge live in the API: switch to live mode.</div>
  if (!actor) return <div className="card card-body helper" data-testid="settings-lifecycle">Type your name in the sidebar first.</div>
  return (
    <div className="stack" style={{ gap: 'var(--space-12)' }} data-testid="settings-lifecycle">
      <div className="card lc-manual" data-testid="lc-manual">
        <div className="lc-banner">
          <div className="lc-banner-text"><b>Data lifecycle is manual.</b> Nothing here runs on a timer, and there are no default periods. A team's retention
            periods are whatever its owners set in the policy (or what you type for one plan); a category with no period has
            no candidates. A person starts each step, and legal holds win over all of it.
            <Steps steps={['Plan · read-only', 'Dry run · stored, audited', 'Run · acts on that dry run only']} at={-1} /></div>
          <label className="field"><span className="micro-label">team</span>
            <select className="select" value={t?.id ?? ''} onChange={(e) => setSel(e.target.value)} data-testid="lc-team">{choices.map((x) => <option key={x.id} value={x.id}>{x.name}{x.my_role ? ` (${x.my_role.replace(/_/g, ' ')})` : ''}</option>)}</select></label>
        </div>
      </div>
      {t && <RetentionCard key={t.id} t={t} actor={actor} />}
      {t && <PurgesCard key={`p:${t.id}`} t={t} actor={actor} />}
      <GcCard actor={actor} />
    </div>
  )
}

// -------------------------------------------------------------------------------------------------------- retention

function RetentionCard({ t, actor }: { t: Team; actor: string }) {
  const canPlan = useCan('retention.plan', 'team', t.id, actor)
  const canRun = useCan('retention.run', 'team', t.id, actor)
  const [ov, setOv] = useState<Record<string, string>>({})
  const [plan, setPlan] = useState<RtPlan | null>(null)
  const [dry, setDry] = useState<(RtPlan & { plan_id: string }) | null>(null)
  const [pick, setPick] = useState<Set<string>>(new Set())
  const [confirm, setConfirm] = useState(false)
  const [res, setRes] = useState<RtRun | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const periods = (): Periods | undefined => {
    const out: Periods = {}
    for (const [k, v] of Object.entries(ov)) {
      if (v.trim() === '') continue
      const [c, key] = k.split('.') as [RtCategory, string]
      ;(out[c] ??= {})[key] = Number(v)
    }
    return Object.keys(out).length ? out : undefined
  }
  const badOv = Object.values(ov).some((v) => v.trim() !== '' && !(Number(v) >= 0))
  const go = <T,>(what: string, p: Promise<T>, then: (x: T) => void) => { setBusy(what); setErr(''); p.then(then).catch((e) => setErr(lifecycleLine(e))).finally(() => setBusy('')) }
  const doPlan = () => go('plan', retention.plan(t.id, periods()), (p) => { setPlan(p); setDry(null); setRes(null) })
  const doDry = () => go('dry', retention.dryRun(t.id, actor, periods()), (p) => { setDry(p); setPlan(p); setRes(null); setConfirm(false); setPick(new Set(RT_CATEGORIES.flatMap((c) => p.categories[c].candidates.map((i) => i.id)))) })
  const doRun = () => dry && go('run', retention.run(t.id, dry.plan_id, actor, [...pick]), (r) => { setRes(r); setConfirm(false) })
  const shown = dry ?? plan
  // P24 names a held version's cases; the other categories carry hold ids: read the team's holds to name those too
  const [cases, setCases] = useState<Record<string, string>>({})
  useEffect(() => { if (shown?.n_held) holdsApi.list({ team: t.id, include_released: true }).then((r) => setCases(Object.fromEntries(r.holds.map((h) => [h.id, h.case_ref])))).catch(() => {}) }, [shown, t.id])
  const pol = (t.policy?.retention ?? null) as Record<string, Record<string, unknown>> | null
  return (
    <div className="card" data-testid="retention-card">
      <CardHead level={2} title={<>Retention <span className="muted" style={{ fontWeight: 400 }}>· {t.name}</span></>} sub="an owner of the team (or an admin) starts a run"
        actions={<Steps steps={['Plan', 'Dry run', 'Run']} at={res ? 3 : dry ? 2 : plan ? 1 : 0} testId="rt-steps" />} />
      <div className="card-body stack" style={{ gap: 10 }}>
        <table className="table lc-t lc-periods" data-testid="rt-periods"><thead><tr><th>category · what a run does</th><th>period in the team policy</th><th>for this plan only (days)</th></tr></thead>
          <tbody>{RT_CATEGORIES.map((c) => (
            <tr key={c} data-testid={`rt-period-${c}`}><td><b>{RT_WHAT[c].name}</b><div className="helper" style={{ fontSize: 12 }}>{RT_WHAT[c].act}</div></td>
              <td className="mono" style={{ fontSize: 12 }}>{RT_KEYS[c].map((k) => { const v = pol?.[c]?.[k]; return <div key={k}>{k} = {v == null ? <span className="muted">no period set</span> : <b>{`${v} d`}</b>}</div> })}
                {c === 'versions' && pol?.versions?.keep_min != null && <div>keep_min = {String(pol.versions.keep_min)}</div>}</td>
              <td>{RT_KEYS[c].map((k) => <input key={k} className="input mono" style={{ width: 150, marginBottom: 2, display: 'block' }} placeholder={k} value={ov[`${c}.${k}`] ?? ''} disabled={!canPlan?.allow}
                onChange={(e) => setOv({ ...ov, [`${c}.${k}`]: e.target.value })} data-testid={`rt-ov-${c}-${k}`} />)}
                {c === 'versions' && <input className="input mono" style={{ width: 150, display: 'block' }} placeholder="keep_min (≥ 1)" value={ov['versions.keep_min'] ?? ''} disabled={!canPlan?.allow} onChange={(e) => setOv({ ...ov, 'versions.keep_min': e.target.value })} data-testid="rt-ov-versions-keep_min" />}</td>
            </tr>))}</tbody></table>
        <p className="helper" style={{ margin: 0, fontSize: 12 }}>No category has a default period. Leave a field empty and the team policy decides; if the policy has none either, that category has no candidates. A value typed here applies to this plan only and is recorded with the dry run.</p>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <button className="btn sm" disabled={!canPlan?.allow || !!busy || badOv} title={why(canPlan, 'Read-only: what a run would touch now')} onClick={doPlan} data-testid="rt-plan">{busy === 'plan' ? 'Planning…' : '1 · Plan'}</button>
          <button className="btn sm" disabled={!canPlan?.allow || !!busy || badOv} title={why(canPlan, 'Store this plan (24 h) and audit it: a run may act only on a dry run')} onClick={doDry} data-testid="rt-dry">{busy === 'dry' ? 'Dry run…' : '2 · Dry run'}</button>
          <button className="btn sm primary" disabled={!canRun?.allow || !dry || !confirm || !pick.size || !!busy || !!res} title={why(canRun, !dry ? 'Make a dry run first' : 'Act on the chosen items of this dry run that are still candidates')} onClick={doRun} data-testid="rt-run">{busy === 'run' ? 'Running…' : `3 · Run ${pick.size} item${pick.size === 1 ? '' : 's'}`}</button>
          {canPlan && !canPlan.allow && <Why testId="rt-plan-why">{why(canPlan)}</Why>}
          {canPlan?.allow && canRun && !canRun.allow && <Why testId="rt-run-why">Run: {why(canRun)}</Why>}
        </div>
        {err && <Callout tone="danger" testId="rt-err">{err}</Callout>}
        {shown && <RtPlanView p={shown} dry={!!dry} pick={pick} setPick={setPick} cases={cases} />}
        {dry && !res && shown!.n_candidates > 0 && <label className="row lc-confirm" style={{ gap: 8 }} data-testid="rt-confirm-row"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} data-testid="rt-confirm" />
          I have read dry run <span className="mono">{dry.plan_id}</span>: run the {pick.size} chosen item{pick.size === 1 ? '' : 's'} now. Version deletes become delete requests that a second person decides; bytes go with the next GC run.</label>}
        {res && <RtResult r={res} />}
      </div>
    </div>
  )
}

const itemLabel = (i: RtItem) => i.kind === 'version' || i.version_id && !i.join_frame_id ? `v${i.v ?? '?'} ${i.name ?? ''} (${i.version_id})` : i.join_frame_id ? `frame ${i.join_frame_id} of ${i.version_id}` : i.run_id ? `run ${i.run_id}${i.name ? ` · ${i.name}` : ''}` : i.upload_id ? `upload ${i.upload_id}` : i.id
const holdText = (h: RtItem['holds'], cases: Record<string, string> = {}) => [...new Set((h ?? []).map((x) => (typeof x === 'string' ? cases[x] ?? x : x.case_ref)))].join(', ')

function RtPlanView({ p, dry, pick, setPick, cases }: { p: RtPlan; dry: boolean; pick: Set<string>; setPick: (s: Set<string>) => void; cases: Record<string, string> }) {
  return (
    <div className="stack" style={{ gap: 6 }} data-testid={dry ? 'rt-dryrun' : 'rt-planview'}>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        {dry ? <><Badge status="pending">dry run · stored, audited</Badge><span className="mono id-chip" data-testid="rt-plan-id" title={p.plan_id}>{p.plan_id}</span></> : <Badge status="none">plan · read-only, nothing stored</Badge>}
        <span className="helper" data-testid="rt-counts"><b className="lc-big">{p.n_candidates}</b> candidate{p.n_candidates === 1 ? '' : 's'} · <b className={p.n_held ? 'g-warn' : ''}>{p.n_held}</b> held by a legal hold · {when(p.at)}</span>
        {Object.keys(p.periods_from).length > 0 && <span className="helper mono" style={{ fontSize: 11 }}>{Object.entries(p.periods_from).map(([k, v]) => `${k}: ${v}`).join(' · ')}</span>}
      </div>
      {p.no_period_set.length > 0 && <p className="helper" style={{ margin: 0 }} data-testid="rt-no-period">No period set for {p.no_period_set.map((c) => RT_WHAT[c].name.toLowerCase()).join(', ')}: nothing there is a candidate.</p>}
      <table className="table lc-t lc-items"><thead><tr>{dry && <th />}<th>category</th><th>item</th><th>what happens</th><th>why</th></tr></thead>
        <tbody>{RT_CATEGORIES.flatMap((c) => [
          ...p.categories[c].candidates.map((i) => <tr key={i.id} data-testid={`rt-cand-${i.id}`}>
            {dry && <td><input type="checkbox" aria-label={`include ${i.id}`} checked={pick.has(i.id)} onChange={(e) => { const s = new Set(pick); if (e.target.checked) s.add(i.id); else s.delete(i.id); setPick(s) }} /></td>}
            <td>{RT_WHAT[c].name}</td><td className="mono" style={{ fontSize: 12 }}>{itemLabel(i)}</td>
            <td><Badge status="candidate">{i.action?.replace(/_/g, ' ')}</Badge>{i.bytes ? <span className="helper"> {kb(i.would_free_bytes ?? i.bytes)}</span> : null}</td><td className="helper" style={{ fontSize: 12 }}>{i.reason}</td></tr>),
          ...p.categories[c].kept.map((i) => <tr key={`k:${i.id}`} className="muted">{dry && <td />}<td>{RT_WHAT[c].name}</td><td className="mono" style={{ fontSize: 12 }}>{itemLabel(i)}</td><td><Badge status="kept" /></td><td style={{ fontSize: 12 }}>{i.why}</td></tr>),
          ...p.categories[c].held.map((i) => <tr key={`h:${i.id}`} data-testid={`rt-held-${i.id}`}>{dry && <td />}<td>{RT_WHAT[c].name}</td><td className="mono" style={{ fontSize: 12 }}>{itemLabel(i)}</td><td><Badge status="held" /></td><td className="helper" style={{ fontSize: 12 }}>legal hold {holdText(i.holds, cases)} covers it: never touched while it is held</td></tr>),
        ])}
          {p.n_candidates + p.n_held + RT_CATEGORIES.reduce((n, c) => n + p.counts[c].kept, 0) === 0 && <tr><td colSpan={dry ? 5 : 4} className="helper">Nothing to show: no candidate, nothing kept back, nothing held.</td></tr>}
        </tbody></table>
    </div>
  )
}

function RtResult({ r }: { r: RtRun }) {
  return (
    <div className="lc-result" data-testid="rt-result">
      <b>Run done:</b> {r.acted} acted, {r.failed} failed, {r.skipped} skipped · {Object.entries(r.by_action).filter(([, n]) => n).map(([a, n]) => `${a.replace(/_/g, ' ')} ${n}`).join(', ') || 'nothing'}
      <ul className="helper" style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
        {r.actions.map((a) => <li key={a.id}>{a.ok ? '✓' : '✕'} <span className="mono">{itemLabel(a)}</span>: {a.action?.replace(/_/g, ' ')}{a.request_id ? ` → request ${a.request_id} (${a.status})` : ''}{a.note ? `: ${a.note}` : ''}{!a.ok && a.error ? `: ${typeof a.error === 'string' ? a.error : (a.error as { message?: string }).message ?? JSON.stringify(a.error)}` : ''}</li>)}
        {r.skipped_items.map((s) => <li key={s.id} className="muted">skipped <span className="mono">{s.id}</span>: {s.why}</li>)}
      </ul>
      <span className="helper" style={{ fontSize: 12 }}>Dry run {r.plan_id} is now used: a second run needs a new dry run.</span>
    </div>
  )
}

// -------------------------------------------------------------------------------------------------------------- GC

function GcCard({ actor }: { actor: string }) {
  const can = useCan('admin.lake', null, null, actor)
  const [armed, setArmed] = useState<GcArmed | null>(null)
  const [plan, setPlan] = useState<GcPlan | null>(null)
  const [res, setRes] = useState<GcRun | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => { if (can?.allow) gc.armed().then(setArmed).catch((e) => setErr(lifecycleLine(e))) }, [can?.allow, tick])
  const go = <T,>(what: string, p: Promise<T>, then: (x: T) => void) => { setBusy(what); setErr(''); p.then(then).catch((e) => setErr(lifecycleLine(e))).finally(() => setBusy('')) }
  return (
    <div className="card" data-testid="gc-card">
      <CardHead level={2} title="Garbage collection" sub="deletes the bytes no version, run, upload or frame holds any more · platform admins"
        badge={armed ? <span data-testid="gc-armed" title={armed.armed ? `armed ${when(armed.armed_at)}` : undefined}><Badge status={armed.armed ? 'armed' : 'disarmed'}>{armed.armed ? `armed ${day(armed.armed_at)}` : 'disarmed'}</Badge></span> : undefined}
        actions={<Steps steps={['Dry run', 'Run']} at={res ? 2 : plan ? 1 : 0} />} />
      <div className="card-body stack" style={{ gap: 8 }}>
        {can && !can.allow && <Why testId="gc-why">{why(can)}</Why>}
        {armed && <p className="helper" style={{ margin: 0 }} data-testid="gc-armed-note">{armed.armed
          ? <>GC is armed: the first verify-all pass that re-hashed every version and legacy JSON blob armed it ({armed.verify_all_passes.length} pass{armed.verify_all_passes.length === 1 ? '' : 'es'} recorded). A run still deletes only what its dry run listed.</>
          : <>GC is disarmed: a dry run works, a run is refused until a verify-all pass has re-hashed every version on this deployment. </>}
          {!armed.armed && <button className="btn sm" style={{ marginLeft: 6 }} disabled={!can?.allow || !!busy} onClick={() => go('verify', gc.verifyAll(), (v) => { setTick((x) => x + 1); if (!v.ok) setErr(`verify-all found problems: ${JSON.stringify(v.problems ?? []).slice(0, 300)}`) })} data-testid="gc-verify-all">{busy === 'verify' ? 'Verifying every version…' : 'Run verify-all (arms GC on a pass)'}</button>}</p>}
        <div className="row" style={{ gap: 6 }}>
          <button className="btn sm" disabled={!can?.allow || !!busy} title={why(can, 'List what a run would delete, store the list (24 h) and audit it')} onClick={() => go('dry', gc.dryRun(actor), (p) => { setPlan(p); setRes(null); setConfirm(false) })} data-testid="gc-dry">{busy === 'dry' ? 'Dry run…' : '1 · Dry run'}</button>
          <button className="btn sm primary" disabled={!can?.allow || !plan || !confirm || !!busy || !!res || !armed?.armed} title={why(can, !plan ? 'Make a dry run first' : !armed?.armed ? 'GC is disarmed' : 'Delete the listed blobs that are still unreferenced')} onClick={() => plan && go('run', gc.run(plan.plan_id, actor), (r) => { setRes(r); setConfirm(false); setTick((x) => x + 1) })} data-testid="gc-run">{busy === 'run' ? 'Deleting…' : `2 · Run ${plan?.candidates.length ?? 0} delete${plan?.candidates.length === 1 ? '' : 's'}`}</button>
        </div>
        {err && <Callout tone="danger" testId="gc-err">{err}</Callout>}
        {plan && <GcPlanView p={plan} />}
        {plan && !res && plan.candidates.length > 0 && <label className="row lc-confirm" style={{ gap: 8 }}><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} data-testid="gc-confirm" aria-label="confirm the GC run" />
          I have read dry run <span className="mono">{plan.plan_id}</span>: delete these {plan.candidates.length} blobs ({kb(plan.candidate_bytes)}) from the store, every S3 version included. Their rows stay as “purged”.</label>}
        {res && <div className="lc-result" data-testid="gc-result"><b>Run of {res.plan_id}:</b> {res.deleted} deleted ({kb(res.bytes)}), {res.skipped} skipped, {res.failed} failed{res.stale_tmp_removed ? `, ${res.stale_tmp_removed} stale tmp dirs removed` : ''}.
          {res.skipped_blobs.length > 0 && <div className="helper" style={{ fontSize: 12 }}>skipped: {res.skipped_blobs.map((s) => `${s.sha256.slice(0, 10)} (${s.why})`).join('; ')}</div>}
          {res.orphans_reported_only > 0 && <div className="helper" style={{ fontSize: 12 }}>{res.orphans_reported_only} orphan object(s) are reported, never deleted (they have no blob row).</div>}
          <div className="helper" style={{ fontSize: 12 }}>This dry run is now used: the next run needs a new dry run.</div></div>}
      </div>
    </div>
  )
}

function GcPlanView({ p }: { p: GcPlan }) {
  const byKind = p.candidates.reduce<Record<string, number>>((m, c) => { m[c.kind] = (m[c.kind] ?? 0) + 1; return m }, {})
  return (
    <div className="stack" style={{ gap: 6 }} data-testid="gc-plan">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <Badge status="pending">dry run · stored, audited</Badge><span className="mono id-chip" data-testid="gc-plan-id" title={p.plan_id}>{p.plan_id}</span>
        <span className="helper" data-testid="gc-counts"><b className="lc-big">{p.candidates.length}</b> to delete ({kb(p.candidate_bytes)}){Object.keys(byKind).length ? `: ${Object.entries(byKind).map(([k, n]) => `${n} ${k}`).join(', ')}` : ''} · {p.held.length} held · {p.unreferenced_in_grace.length} still in grace · {p.orphans.length} orphans (reported only)</span>
      </div>
      <span className="helper" style={{ fontSize: 12 }}>Grace: unreferenced live blobs wait {p.grace.live_days} d, pending ones {p.grace.pending_hours} h, orphans {p.grace.orphan_hours} h. Store holds {Object.entries(p.blobs).map(([s, n]) => `${n} ${s}`).join(', ')}; {p.blob_refs} references.</span>
      {p.candidates.length > 0 && <table className="table lc-t"><thead><tr><th>blob</th><th>kind</th><th className="r">bytes</th><th>since</th></tr></thead>
        <tbody>{p.candidates.slice(0, 40).map((c) => <tr key={c.sha256} data-testid={`gc-cand-${c.sha256.slice(0, 12)}`}><td><CopyId value={c.sha256} n={12} /></td><td>{c.kind}</td><td className="r mono">{kb(c.bytes)}</td><td className="helper" style={{ fontSize: 12 }}>{when(c.created_at)}</td></tr>)}
          {p.candidates.length > 40 && <tr><td colSpan={4} className="helper">… and {p.candidates.length - 40} more</td></tr>}</tbody></table>}
      {p.held.length > 0 && <div className="data-warn" data-testid="gc-held">{p.held.length} unreferenced blob{p.held.length === 1 ? '' : 's'} belonged to something under a legal hold ({[...new Set(p.held.flatMap((h) => (h.holds ?? []).map((x) => x.case_ref)))].join(', ')}): never deleted while held.</div>}
      {p.integrity.length > 0 && <div className="data-warn">Integrity: {p.integrity.map((i) => `${i.key}: ${i.finding}`).join('; ')}</div>}
      {p.deleted_versions_with_refs.length > 0 && <span className="helper" style={{ fontSize: 12 }}>{p.deleted_versions_with_refs.length} version(s) deleted before P24 still hold refs: a run drops them, the next dry run lists their bytes.</span>}
      <span className="helper" style={{ fontSize: 12 }}>{p.note}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------------- purges

function PurgesCard({ t, actor }: { t: Team; actor: string }) {
  const [list, setList] = useState<PurgeRow[] | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const name = useTeamNames()
  useEffect(() => { purges.list(t.id).then((l) => { setList(l); setSel((s) => s ?? l.find((p) => p.status === 'pending')?.id ?? null) }).catch(() => setList([])) }, [t.id, tick])
  return (
    <div className="card" data-testid="purges-card">
      <CardHead level={2} title={<>Purges <span className="muted" style={{ fontWeight: 400 }}>· involving {t.name}</span></>} sub="an owner of every involved team approves, each a different person, never the requester"
        badge={list?.some((p) => p.status === 'pending') ? <Badge status="pending">{list.filter((p) => p.status === 'pending').length} waiting</Badge> : undefined} />
      <div className="card-body stack" style={{ gap: 6 }}>
        {!list ? <span className="helper">Reading the purges…</span> : !list.length ? <span className="helper">No purge involves {t.name}. A maintainer asks for one on the dataset's page.</span>
          : <table className="table lc-t lc-items"><thead><tr><th>what</th><th>asked by</th><th>approvals</th><th>status</th><th>purge</th></tr></thead>
            <tbody>{list.map((p) => <tr key={p.id} className={sel === p.id ? 'sel' : ''} style={{ cursor: 'pointer' }} onClick={() => setSel(sel === p.id ? null : p.id)} data-testid={`purges-row-${p.id}`}>
              <td title={p.root_id}><span className="mono">{p.table}.{(p.columns ?? []).join(',') || 'rows'}</span></td><td>{p.requested_by} <span className="sub-line">{day(p.requested_at)}</span></td>
              <td style={{ fontSize: 12 }}>{p.teams_needed.map((x) => <span key={x} className={`lc-appr ${p.approvals[x] ? 'ok' : ''}`}>{p.approvals[x] ? '✓' : '○'} {name(x)}{p.approvals[x] ? ` · ${p.approvals[x]}` : ''}</span>)}</td>
              <td><Badge status={p.status === 'used' ? 'executed' : p.status}>{p.status === 'used' ? 'executed' : p.status}</Badge></td><td><CopyId value={p.id} /></td></tr>)}</tbody></table>}
        {sel && <PurgeDetail key={`${sel}:${tick}`} id={sel} by={actor} onChanged={() => setTick((x) => x + 1)} />}
      </div>
    </div>
  )
}
