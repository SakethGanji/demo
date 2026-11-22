import { api } from '@/shared/api/client'
import type { InputRecord, Parity } from '@/shared/api/types'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'
import { CopyId, CoverageBar } from '@/shared/components/DataKit'

const PAR: Record<string, Status> = { ok: 'success', mismatch: 'danger', not_applicable: 'neutral', not_checked: 'warning' }
const PAR_WORD: Record<string, string> = { ok: 'parity ok', mismatch: 'parity mismatch', not_applicable: 'parity n/a (one table)', not_checked: 'parity not checked' }
const sh = (x?: string | null, n = 12) => (x ? `${x.slice(0, n)}…` : '—')
const num = (x?: number | null) => (x == null ? '—' : x.toLocaleString())
const cov = (x: unknown) => (typeof x === 'number' ? `${(x * 100).toFixed(1)} %` : String(x ?? '—'))

/** P15 (C §4.6): "Data used in development": the frame, the dataset and version (contract, certification), every input
 *  table with its sha and upload, the join spec and its coverage, the split, PII features; and the serving parity:
 *  the frame rebuilt from the pinned inputs with the serving join path, compared by __row_id. Counts and hashes only. */
export function InputsSection({ inputs: r, parity, vid }: { inputs: InputRecord; parity?: Parity; vid: string }) {
  if (r.error) return <p className="helper" data-testid="card-inputs" style={{ color: 'var(--status-warning)' }}>▲ The input record could not be built: {r.error}</p>
  const fr = r.frame, ds = r.dataset, ver = r.version, jn = r.join
  const p = parity
  return (
    <div className="stack" style={{ gap: 8, marginTop: 12 }} data-testid="card-inputs">
      <div className="inp-head">
        <b>Data used in development</b>
        {p && <span data-testid="card-parity" data-status={p.status} title={p.summary ?? p.reason ?? ''}><StatusPill status={PAR[p.status] ?? 'neutral'}>{PAR_WORD[p.status] ?? p.status}</StatusPill></span>}
        <span className="helper" style={{ fontSize: 12 }}>{r.recorded ? 'input record frozen at registration' : 'computed now: registered before the input record existed'}</span>
        <span className="spacer" />
        <a className="btn sm" href={api.registry.inputsZipUrl(vid)} download data-testid="card-inputs-zip" title="manifest.json (this record and parity), hashes.json, contracts.json, join_spec.json, README.txt: no rows">↓ inputs.zip</a>
      </div>
      <dl className="mc-kv">
        {fr && <div><dt>frame</dt><dd>{fr.kind === 'join_frame' ? '⋈ join frame' : `table ${fr.table ?? 'data'}`} · <span className="mono">{num(fr.rows)}</span> rows <CopyId value={fr.kind === 'join_frame' ? fr.id : fr.sha256} n={10} /></dd></div>}
        {ds && <div><dt>dataset</dt><dd title={`${ds.root} · team ${ds.team ?? '—'} · project ${ds.project ?? '—'}`}><b>{ds.name}</b> <CopyId value={ds.root} /></dd></div>}
        {ver && <div><dt>version</dt><dd>v{ver.v} by {ver.created_by}{ver.reason ? ` · “${ver.reason}”` : ''} · contract {ver.contract ? <span className="mono">{String(ver.contract.status ?? (ver.contract.result as { status?: string } | undefined)?.status ?? `v${ver.contract.version ?? '?'}`)}</span> : 'none'} · {ver.certification ? <span className="pt-chip">✓ {ver.certification.level} by {ver.certification.by}</span> : 'not certified'}</dd></div>}
        {r.split && <div><dt>split</dt><dd title={`${r.split.id ?? '—'} · sha ${r.split.sha256 ?? '—'}`}>{r.split.strategy ?? '—'} · target <span className="mono">{r.split.target ?? '—'}</span></dd></div>}
        <div><dt>PII features</dt><dd className="mono">{r.pii_features?.length ? r.pii_features.join(', ') : 'none'}</dd></div>
        <div><dt>protected, left out</dt><dd className="mono">{r.excluded_protected?.length ? r.excluded_protected.join(', ') : 'none'}</dd></div>
      </dl>
      <table className="table compact" data-testid="card-input-tables">
        <thead><tr><th>table</th><th>role</th><th>version</th><th className="r">rows</th><th>sha256</th><th>source file</th><th>uploaded by</th></tr></thead>
        <tbody>{(r.tables ?? []).map((t) => (
          <tr key={`${t.role}-${t.name}`}><td className="mono"><b>{t.name}</b>{t.dataset ? <span className="muted"> ({t.dataset.name})</span> : null}</td><td><span className="pt-chip">{t.role === 'grain' ? 'grain' : t.role.replace('join:', 'joined #')}</span></td>
            <td><CopyId value={t.dv} />{t.from_dv ? <span className="muted" title={`carried from ${t.from_dv}`}> · carried</span> : null}</td><td className="r mono">{num(t.rows)}</td>
            <td><CopyId value={t.sha256} n={8} /></td><td className="mono">{t.source_file ?? '—'}</td><td>{t.uploaded_by ?? '—'}</td></tr>))}</tbody>
      </table>
      {jn && <div className="inp-join" data-testid="card-join"><span>⋈ Join <b>{jn.name}</b> · spec v{jn.spec_version ?? '?'} <CopyId value={jn.spec_sha256} n={8} /> {jn.spec_frozen ? <span className="helper">the spec trained with</span> : <b className="g-warn">▲ the saved spec changed since training</b>}</span>
        {jn.coverage && Object.entries(jn.coverage).map(([k, x]) => <span key={k} className="inp-cov"><span className="mono">{k}</span>{typeof x === 'number' ? <CoverageBar value={x} /> : <span>{cov(x)}</span>}</span>)}</div>}
      {p && p.status !== 'not_applicable' && <ParityDetail p={p} />}
    </div>
  )
}

/** The parity checks one by one: the spec, the training frame's cache, each pinned input, the rebuilt frame's rows and
 *  cells by __row_id, then the version's predictions on the rebuilt holdout. */
export function ParityDetail({ p }: { p: Parity }) {
  const pr = p.predictions
  return (
    <div className={`parity ${p.status === 'mismatch' ? 'bad' : ''}`} data-testid="parity-detail">
      <div className="parity-sum"><b>Serving parity:</b> {p.summary ?? p.reason ?? p.status}</div>
      {p.cache && <Check ok={p.cache.ok} what="training frame cache" detail={`recorded ${sh(p.cache.recorded)} · now ${sh(p.cache.now)}`} />}
      {(p.inputs ?? []).map((x) => <Check key={`${x.version_id}-${x.table}`} ok={x.ok} what={`input ${x.table}`} detail={`pinned ${sh(x.pinned)} · now ${sh(x.now)} · ${x.version_id}`} />)}
      {p.rows && <div className={`parity-row ${p.rows.training === p.rows.serving && !p.rows.ids_unmatched ? 'ok' : 'no'}`}><span className="parity-mark">{p.rows.training === p.rows.serving && !p.rows.ids_unmatched ? '✓' : '✕'}</span> rows: training {num(p.rows.training)} · serving {num(p.rows.serving)} · __row_id unmatched {num(p.rows.ids_unmatched)}{p.columns_compared != null ? ` · ${p.columns_compared} columns compared` : ''}{p.mismatched_cells && Object.keys(p.mismatched_cells).length ? ` · cells differ: ${Object.entries(p.mismatched_cells).map(([c, n]) => `${c} ${n}`).join(', ')}` : ''}{p.byte_equal ? ' · byte-equal' : ''}</div>}
      {pr && <div className={`parity-row ${(pr.ok ?? pr.status === 'ok') ? 'ok' : 'no'}`}><span className="parity-mark">{(pr.ok ?? pr.status === 'ok') ? '✓' : '✕'}</span> predictions on the rebuilt holdout: {num(pr.rows as number | undefined)} rows · max |Δp| = {pr.max_abs_diff ?? '—'}{pr.labels_equal != null ? ` · labels ${pr.labels_equal ? 'equal' : 'differ'}` : ''}</div>}
    </div>
  )
}

/** One parity check: ✓ / ✕, what was compared, and the hashes or counts behind it. */
function Check({ ok, what, detail }: { ok: boolean; what: string; detail: string }) {
  return <div className={`parity-row ${ok ? 'ok' : 'no'}`}><span className="parity-mark">{ok ? '✓' : '✕'}</span> <b>{what}</b> <span className="mono muted">{detail}</span></div>
}
