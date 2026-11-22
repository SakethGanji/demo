import { api } from '@/shared/api/client'
import type { InputRecord, Parity } from '@/shared/api/types'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'

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
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <b>Data used in development</b>
        <span className="helper" style={{ fontSize: 12 }}>{r.recorded ? 'input record frozen at registration' : 'computed now: registered before the input record existed'}</span>
        {p && <span data-testid="card-parity" data-status={p.status} title={p.summary ?? p.reason ?? ''}><StatusPill status={PAR[p.status] ?? 'neutral'}>{PAR_WORD[p.status] ?? p.status}</StatusPill></span>}
        <a className="btn sm" href={api.registry.inputsZipUrl(vid)} download data-testid="card-inputs-zip" title="manifest.json (this record and parity), hashes.json, contracts.json, join_spec.json, README.txt: no rows">inputs.zip</a>
      </div>
      <dl className="mc-kv">
        {fr && <div><dt>frame</dt><dd className="mono">{fr.kind === 'join_frame' ? `join frame ${fr.id}` : `table ${fr.table ?? 'data'} of ${fr.id}`} · {num(fr.rows)} rows · sha {sh(fr.sha256, 16)}{fr.frame_sha256 ? ` · identity ${sh(fr.frame_sha256)}` : ''}</dd></div>}
        {ds && <div><dt>dataset</dt><dd>{ds.name} <span className="mono muted">{ds.root}</span> · team <span className="mono">{ds.team ?? '—'}</span> · project <span className="mono">{ds.project ?? '—'}</span></dd></div>}
        {ver && <div><dt>version</dt><dd><span className="mono">{ver.dv}</span> v{ver.v} by {ver.created_by}{ver.reason ? ` · “${ver.reason}”` : ''} · contract {ver.contract ? <span className="mono">{String(ver.contract.status ?? (ver.contract.result as { status?: string } | undefined)?.status ?? `v${ver.contract.version ?? '?'}`)}</span> : 'none'} · {ver.certification ? <span className="pt-chip">✓ {ver.certification.level} by {ver.certification.by}</span> : 'not certified'}</dd></div>}
        {r.split && <div><dt>split</dt><dd className="mono">{r.split.strategy ?? '—'} · target {r.split.target ?? '—'} · {r.split.id ?? '—'} · sha {sh(r.split.sha256)}</dd></div>}
        <div><dt>PII features</dt><dd className="mono">{r.pii_features?.length ? r.pii_features.join(', ') : 'none'}</dd></div>
        <div><dt>protected, left out</dt><dd className="mono">{r.excluded_protected?.length ? r.excluded_protected.join(', ') : 'none'}</dd></div>
      </dl>
      <table className="table compact" data-testid="card-input-tables">
        <thead><tr><th>table</th><th>role</th><th>version</th><th className="r">rows</th><th>sha256</th><th>source file</th><th>uploaded by</th></tr></thead>
        <tbody>{(r.tables ?? []).map((t) => (
          <tr key={`${t.role}-${t.name}`}><td className="mono">{t.name}{t.dataset ? <span className="muted"> ({t.dataset.name})</span> : null}</td><td className="muted">{t.role}</td>
            <td className="mono">{t.dv}{t.from_dv ? <span className="muted"> ← {t.from_dv}</span> : null}</td><td className="r mono">{num(t.rows)}</td>
            <td className="mono" title={t.sha256}>{sh(t.sha256, 16)}</td><td className="mono">{t.source_file ?? '—'}</td><td>{t.uploaded_by ?? '—'}</td></tr>))}</tbody>
      </table>
      {jn && <p className="helper" style={{ fontSize: 12 }} data-testid="card-join">Join <b>{jn.name}</b> <span className="mono">{jn.id}</span> · spec v{jn.spec_version ?? '?'} · spec SHA-256 <span className="mono">{sh(jn.spec_sha256, 16)}</span> {jn.spec_frozen ? '(the spec trained with)' : '▲ the saved spec changed since training'} · coverage {jn.coverage ? Object.entries(jn.coverage).map(([k, x]) => `${k} ${cov(x)}`).join(', ') : '—'}</p>}
      {p && p.status !== 'not_applicable' && <ParityDetail p={p} />}
    </div>
  )
}

/** The parity checks one by one: the spec, the training frame's cache, each pinned input, the rebuilt frame's rows and
 *  cells by __row_id, then the version's predictions on the rebuilt holdout. */
export function ParityDetail({ p }: { p: Parity }) {
  const pr = p.predictions
  return (
    <div className={p.status === 'mismatch' ? 'data-warn' : 'helper'} style={{ fontSize: 12 }} data-testid="parity-detail">
      <div><b>Serving parity:</b> {p.summary ?? p.reason ?? p.status}</div>
      {p.cache && <div className="mono">{p.cache.ok ? '✓' : '✕'} training frame cache · recorded {sh(p.cache.recorded)} · now {sh(p.cache.now)}</div>}
      {(p.inputs ?? []).map((x) => <div key={`${x.version_id}-${x.table}`} className="mono">{x.ok ? '✓' : '✕'} input {x.table} ({x.version_id}) · pinned {sh(x.pinned)} · now {sh(x.now)}</div>)}
      {p.rows && <div className="mono">{p.rows.training === p.rows.serving && !p.rows.ids_unmatched ? '✓' : '✕'} rows: training {num(p.rows.training)} · serving {num(p.rows.serving)} · __row_id unmatched {num(p.rows.ids_unmatched)}{p.columns_compared != null ? ` · ${p.columns_compared} columns compared` : ''}{p.mismatched_cells && Object.keys(p.mismatched_cells).length ? ` · cells differ: ${Object.entries(p.mismatched_cells).map(([c, n]) => `${c} ${n}`).join(', ')}` : ''}{p.byte_equal ? ' · byte-equal' : ''}</div>}
      {pr && <div className="mono">{(pr.ok ?? pr.status === 'ok') ? '✓' : '✕'} predictions on the rebuilt holdout: {num(pr.rows as number | undefined)} rows · max |Δp| = {pr.max_abs_diff ?? '—'}{pr.labels_equal != null ? ` · labels ${pr.labels_equal ? 'equal' : 'differ'}` : ''}</div>}
    </div>
  )
}
