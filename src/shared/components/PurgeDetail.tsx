import { useEffect, useState } from 'react'
import { purges, LIFECYCLE_CODES, type Purge, type RunVerify } from '@/shared/api/lifecycle'
import { refusal, teamsApi, type Team } from '@/shared/api/governance'
import { useCan, why } from '@/shared/hooks/useCan'

/** U3 (P24 §5.10, plan §11 item 5): one purge request. The impact report says what the purge reaches in every team (the
 *  versions holding the values, the runs trained on them or pinning the bytes, shares whose grantee trained on them,
 *  join frames, models and champions, and each blob with why it is listed). An owner of every involved team approves,
 *  a different person per team and never the requester; the last approval executes it (all-or-nothing), and the
 *  executed state lists what went. A run trained on purged bytes re-verifies as "purged", not as a mismatch. Used on
 *  the dataset page and in Settings › Data lifecycle. */

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—')
const kb = (b?: number | null) => (b == null ? '—' : b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(2)} MB`)
export const lifecycleLine = (e: unknown) => { const r = refusal(e); const s = r.code ? LIFECYCLE_CODES[r.code] : ''; return `${s ? `${s} ` : ''}${r.message}${r.code ? ` (${r.code})` : ''}` }

let teamCache: Promise<Team[]> | null = null
/** Every team's name (the impact report names teams by id). */
export function useTeamNames(): (id: string) => string {
  const [list, setList] = useState<Team[]>([])
  useEffect(() => { (teamCache ??= teamsApi.list(true).catch(() => { teamCache = null; return [] })).then(setList) }, [])
  return (id) => list.find((t) => t.id === id)?.name ?? id
}

const STATUS: Record<Purge['status'], string> = { pending: 'waiting for approvals', approved: 'executed, bytes still being deleted', used: 'executed', rejected: 'rejected', withdrawn: 'withdrawn' }

export function PurgeDetail({ id, by, onChanged }: { id: string; by: string; onChanged?: (p: Purge) => void }) {
  const [p, setP] = useState<Purge | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState('')
  const name = useTeamNames()
  useEffect(() => { let live = true; purges.get(id).then((x) => live && setP(x)).catch((e) => live && setErr(lifecycleLine(e))); return () => { live = false } }, [id])
  if (err && !p) return <p className="helper up-err" data-testid="purge-err">✕ {err}</p>
  if (!p) return <p className="helper">Reading purge {id}…</p>
  const im = p.impact
  const act = (team: string, approve: boolean) => {
    setBusy(team); setMsg(null)
    purges.decide(p.id, team, approve, by).then((x) => {
      setP(x); onChanged?.(x)
      setMsg({ ok: true, text: x.executed ? `Approved for ${name(team)}: every team has approved, so the purge was executed.` : approve ? `Approved for ${name(team)}. Still waiting for ${x.waiting_for.map(name).join(', ')}.` : `Rejected for ${name(team)}: the purge will not run.` })
    }).catch((e) => setMsg({ ok: false, text: lifecycleLine(e) })).finally(() => setBusy(''))
  }
  const mine = p.requested_by.toLowerCase() === by.toLowerCase()
  const approvedByMe = Object.values(p.approvals).some((a) => a.by.toLowerCase() === by.toLowerCase())
  return (
    <div className="stack purge-detail" style={{ gap: 10 }} data-testid="purge-detail" data-status={p.status} data-purge={p.id}>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <b>Purge <span className="mono">{p.id}</span></b>
        <span className={`pt-chip purge-st-${p.status}`} data-testid="purge-status">{STATUS[p.status]}</span>
        <span className="helper">{p.table}: {p.columns.length ? <>columns <span className="mono">{p.columns.join(', ')}</span></> : null}{p.columns.length && p.n_row_ids ? ' and ' : ''}{p.n_row_ids ? `${p.n_row_ids} row id(s)` : ''} · asked by {p.requested_by} {when(p.requested_at)} · “{p.reason}”</span>
      </div>
      <p className="helper" style={{ margin: 0 }}>{p.executed
        ? <>The values now live nowhere: <span className="mono">{p.new_version_id}</span>, written at the request without them, is what remains of the dataset.</>
        : <>The request already wrote <span className="mono">{p.new_version_id}</span>: a new version without these values (the other tables carried, the kept rows' splits carried). {p.status === 'pending' ? 'Until every involved team approves, the old versions and their bytes stay as they are.' : 'The old versions and their bytes were left as they are.'}</>}</p>
      {msg && <p className={`helper data-msg ${msg.ok ? 'ok' : 'bad'}`} role={msg.ok ? 'status' : 'alert'} style={{ margin: 0 }} data-testid="purge-msg">{msg.ok ? '✓' : '✕'} {msg.text}</p>}

      <div className="purge-impact" data-testid="purge-impact">
        <span className="micro-label">impact report · {im.teams.length} team{im.teams.length === 1 ? '' : 's'} · {im.blobs.length} blobs ({kb(im.bytes)})</span>
        <table className="table lc-t"><thead><tr><th>team</th><th>what it holds</th><th>approval</th></tr></thead>
          <tbody>{p.teams_needed.map((t) => {
            const b = im.by_team[t] ?? { versions: [], runs: [], refs: 0, shares: [] }
            const a = p.approvals[t]
            return <tr key={t} data-testid={`purge-team-${t}`}>
              <td><b>{name(t)}</b>{t === im.owner_team && <span className="pt-chip" style={{ marginLeft: 4 }}>owner of the data</span>}<div className="mono muted" style={{ fontSize: 11 }}>{t}</div></td>
              <td className="helper" style={{ fontSize: 12 }}>
                {b.versions.length > 0 && <div>versions deleted: <span className="mono">{b.versions.map((v) => { const x = im.versions.find((y) => y.id === v); return x ? `v${x.version_no}${x.other_dataset ? ` of ${x.name}` : ''}` : v }).join(', ')}</span></div>}
                {b.runs.length > 0 && <div>runs that lose their data: <span className="mono">{b.runs.join(', ')}</span></div>}
                {b.shares.length > 0 && <div>through share{b.shares.length > 1 ? 's' : ''} <span className="mono">{b.shares.join(', ')}</span>{im.shares.filter((s) => b.shares.includes(s.share_id)).map((s) => ` (scope ${s.scope}, PII ${s.pii}, ${s.grantee_runs.length} run${s.grantee_runs.length === 1 ? '' : 's'} on it)`).join('')}</div>}
                {b.refs > 0 && <div>{b.refs} blob reference{b.refs === 1 ? '' : 's'}</div>}
                {!b.versions.length && !b.runs.length && !b.shares.length && !b.refs && <div>involved as the owner</div>}
              </td>
              <td>{a ? <span data-testid={`purge-approved-${t}`}>✓ {a.by} <span className="helper">{when(a.at)}</span></span>
                : p.status === 'pending' ? <TeamDecide team={t} by={by} mine={mine} approvedByMe={approvedByMe} busy={busy === t} onDecide={(ok) => act(t, ok)} />
                  : <span className="helper">—</span>}</td>
            </tr>
          })}</tbody></table>
        <details data-testid="purge-reach"><summary className="micro-label">everything it reaches</summary>
          <div className="helper stack" style={{ gap: 4, fontSize: 12, marginTop: 6 }}>
            <div><b>Versions holding the values:</b> {im.versions_with_values.map((v) => `v${v.v}${v.state === 'deleted' ? ' (already deleted)' : ''}`).join(', ') || 'none'}</div>
            <div><b>Runs:</b> {im.runs.length ? im.runs.map((r) => `${r.id} (${r.via})`).join(' · ') : 'none'}</div>
            <div><b>Join frames:</b> {im.join_frames.join(', ') || 'none'} · <b>models:</b> {im.models.map((m) => `${m.model_name} v${m.version}`).join(', ') || 'none'} · <b>champions:</b> {im.champions.length || 'none'}</div>
            <table className="table lc-t"><thead><tr><th>blob</th><th>why it is listed</th><th className="r">bytes</th><th>state</th></tr></thead>
              <tbody>{im.blobs.map((b) => <tr key={b.sha256}><td className="mono">{b.sha256.slice(0, 12)}</td><td>{b.why.join('; ')}</td><td className="r mono">{kb(b.bytes)}</td><td>{b.state}</td></tr>)}</tbody></table>
            <div>{im.note}</div>
          </div></details>
        {im.holds.length > 0 && <p className="data-warn" data-testid="purge-holds">A legal hold covers part of this ({im.holds.map((h) => h.case_ref).join(', ')}): the purge cannot execute until it is released.</p>}
      </div>
      {p.status === 'pending' && mine && <p className="helper" style={{ margin: 0 }}>You asked for this purge, so an owner of each team above decides, never you (four eyes). <button className="chip-btn" onClick={() => purges.withdraw(p.id, by).then((x) => { setP(x); onChanged?.(x) }).catch((e) => setMsg({ ok: false, text: lifecycleLine(e) }))} data-testid="purge-withdraw">withdraw</button></p>}
      {p.executed && <Executed p={p} by={by} />}
    </div>
  )
}

function TeamDecide({ team, by, mine, approvedByMe, busy, onDecide }: { team: string; by: string; mine: boolean; approvedByMe: boolean; busy: boolean; onDecide: (approve: boolean) => void }) {
  const can = useCan('data.purge_decide', 'team', team, by)
  const no = !by || mine || approvedByMe || !can?.allow
  const reason = mine ? 'Four eyes: you asked for this purge, so someone else decides.' : approvedByMe ? 'You already approved for another team: each team is a different person.' : why(can, 'Approve for this team: when every team has approved, the purge executes')
  return <span className="stack" style={{ gap: 2 }}>
    <span className="row" style={{ gap: 4 }}>
      <button className="btn sm primary" disabled={no || busy} title={reason} onClick={() => onDecide(true)} data-testid={`purge-approve-${team}`}>{busy ? '…' : 'Approve'}</button>
      <button className="btn sm ghost" disabled={no || busy} title={reason} onClick={() => onDecide(false)} data-testid={`purge-reject-${team}`}>Reject</button></span>
    {no && can && <span className="helper" style={{ fontSize: 11 }}>{reason}</span>}
  </span>
}

function Executed({ p, by }: { p: Purge; by: string }) {
  const x = p.executed!
  const canVerify = useCan('admin.lake', null, null, by)
  const [ver, setVer] = useState<Record<string, RunVerify | string>>({})
  const runs = p.impact.runs
  const verify = (r: string) => purges.verifyRun(r).then((v) => setVer((o) => ({ ...o, [r]: v }))).catch((e) => setVer((o) => ({ ...o, [r]: lifecycleLine(e) })))
  return (
    <div className="purge-executed" data-testid="purge-executed">
      <span className="micro-label">executed {when(x.at)} by {x.by}</span>
      <ul className="helper" style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12.5 }}>
        <li>versions deleted: <span className="mono">{x.versions_deleted.join(', ') || 'none'}</span>{x.default_moved ? <> · the default moved {x.default_moved.from} → <span className="mono">{x.default_moved.to}</span></> : null}</li>
        <li>{x.blobs.length} blob{x.blobs.length === 1 ? '' : 's'} marked; {x.bytes_deleted ?? 0} deleted from the store (every S3 version){(x.bytes_failed?.length ?? 0) > 0 ? `, ${x.bytes_failed!.length} failed (resume below)` : ''}</li>
        <li>{x.run_pins_marked} run pin{x.run_pins_marked === 1 ? '' : 's'} replaced by a “purged” marker; {x.refs_dropped} other reference{x.refs_dropped === 1 ? '' : 's'} dropped</li>
      </ul>
      {p.status === 'approved' && <button className="btn sm" onClick={() => purges.resume(p.id, by)} data-testid="purge-resume">Resume the byte deletes</button>}
      {runs.length > 0 && <div className="stack" style={{ gap: 4 }}>
        <span className="helper" style={{ fontSize: 12 }}>Runs trained on the purged data keep their record. A re-verify says <b>purged</b> (not a mismatch) and still checks the split.</span>
        {runs.map((r) => {
          const v = ver[r.id]
          return <div key={r.id} className="row" style={{ gap: 6, flexWrap: 'wrap' }} data-testid={`purge-run-${r.id}`}>
            <span className="mono">{r.id}</span><span className="helper">{r.via}</span>
            <button className="btn sm ghost" disabled={!canVerify?.allow} title={why(canVerify, 'Re-hash what this run was trained on')} onClick={() => verify(r.id)} data-testid={`purge-verify-${r.id}`}>Re-verify</button>
            {typeof v === 'string' ? <span className="helper up-err">✕ {v}</span>
              : v ? <span data-testid={`purge-verified-${r.id}`}><span className={`pt-chip ${v.status === 'purged' ? 'purge-st-used' : ''}`}>{v.status ?? (v.ok ? 'ok' : 'failed')}</span>
                <span className="helper"> data {v.data?.ok == null ? 'not checked: its bytes were purged' : v.data.ok ? 'ok' : 'MISMATCH'}{v.purged ? ` (purge ${v.purged.purge_ids.join(', ')}, ${v.purged.blobs.length} blob${v.purged.blobs.length === 1 ? '' : 's'})` : ''} · split {v.split?.ok == null ? '—' : v.split.ok ? 'ok' : 'failed'}</span></span> : null}
          </div>
        })}
      </div>}
    </div>
  )
}
