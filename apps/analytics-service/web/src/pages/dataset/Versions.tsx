import { useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Page } from "../../api/client";
import {
  AsyncView, Badge, Card, Cell, Field, Loading, Modal, ErrorBanner, EmptyState,
  StatTile, cx, fmtBytes, fmtDate, fmtNum, statusKind, useAsync, useToast,
} from "../../components/ui";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

/* ---- API shapes (see openapi.json: VersionInfo, TagInfo, WorkbookDiffResponse, SheetDiffResponse, RowDiffResponse) ---- */
interface VersionInfo {
  id: string; version_number: number; status: string;
  row_count?: number | null; size_bytes?: number | null; sheet_count?: number | null;
  created_at: string; tags?: string[];
}
interface TagInfo { tag_name: string; version_id: string; version_number: number; created_at: string; updated_at: string; }
interface TagHistoryEntry {
  id: number; tag_name: string; action: string;
  from_version_number?: number | null; to_version_number?: number | null;
  reason?: string | null; actor_email?: string | null; actor_user_id?: string | null; created_at: string;
}
interface SheetSummary { name: string; sheet_key?: string | null; row_count: number; column_count: number; }
interface ModifiedSheet { sheet_key: string; from_sheet: string; to_sheet: string; schema_changed: boolean; row_count_delta?: number | null; visibility_changed?: boolean; }
interface RenamedSheet { logical_sheet_id: string; from_sheet: string; to_sheet: string; from_sheet_key: string; to_sheet_key: string; }
interface RenameCandidate { from_sheet: string; to_sheet: string; confidence: "high" | "medium"; reason: string; }
interface WorkbookDiff {
  dataset_id: string; from_version: number; to_version: number;
  added: SheetSummary[]; removed: SheetSummary[]; modified: ModifiedSheet[];
  unchanged: string[]; renamed: RenamedSheet[]; rename_candidates: RenameCandidate[];
}
interface SheetColumn { name: string; normalized_name: string; dtype: string; }
interface TypeChange { column: string; from_dtype: string; to_dtype: string; }
interface NullabilityChange { column: string; [k: string]: unknown; }
interface SheetDiff {
  sheet_key: string; from_sheet: string; to_sheet: string; from_version: number; to_version: number; identical: boolean;
  added_columns: SheetColumn[]; removed_columns: SheetColumn[]; type_changes: TypeChange[];
  nullability_changes?: NullabilityChange[]; from_row_count?: number | null; to_row_count?: number | null; row_count_delta?: number | null;
}
interface ColumnChange { column: string; changed_rows: number; }
interface RowDiff {
  sheet: string; key: string[]; compared_columns: string[];
  added: number; removed: number; changed: number; unchanged: number;
  column_changes: ColumnChange[];
  added_sample: Record<string, unknown>[]; removed_sample: Record<string, unknown>[];
  changed_sample: Record<string, unknown>[];
  masked_columns?: string[];
}
interface Sheet { name: string; sheet_key: string; columns?: SheetColumn[]; }

type AsyncState<T> = { data: T | null; error: unknown; loading: boolean; reload: () => void };

export function Versions({ dataset, reload }: DatasetTabProps) {
  const { identity } = useIdentity();
  const idem = [dataset.id, identity.userId, identity.teamId];
  const versions = useAsync(() => api.get<Page<VersionInfo>>(`/datasets/${dataset.id}/versions`), idem);
  const tags = useAsync(() => api.get<Page<TagInfo>>(`/datasets/${dataset.id}/tags`), idem);

  const refreshAll = () => { versions.reload(); tags.reload(); reload(); };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <Card title="Version history" pad={false}>
        <AsyncView state={versions} empty={<div className="card-pad muted">No versions yet.</div>}>
          {(page) => <Timeline dataset={dataset} versions={sortDesc(page.items)} />}
        </AsyncView>
      </Card>

      <Tags dataset={dataset} tags={tags} versions={versions.data?.items || []} onChange={refreshAll} />

      <Compare dataset={dataset} versions={sortDesc(versions.data?.items || [])} idem={idem} />
    </div>
  );
}

function sortDesc(items: VersionInfo[]): VersionInfo[] {
  return [...items].sort((a, b) => b.version_number - a.version_number);
}

/* ---- Version timeline ---- */
function Timeline({ dataset, versions }: { dataset: DatasetTabProps["dataset"]; versions: VersionInfo[] }) {
  const toast = useToast();
  const [sheetPickFor, setSheetPickFor] = useState<VersionInfo | null>(null);
  // A workbook version needs an explicit sheet — CSV can only hold one. Without
  // this the API (rightly) refused and told the user to set a query parameter
  // they had no way to set from the UI.
  const download = async (v: VersionInfo, sheet?: string) => {
    if ((v.sheet_count ?? 1) > 1 && !sheet) { setSheetPickFor(v); return; }
    const suffix = sheet ? `-${sheet}` : "";
    try {
      await api.download(
        `/datasets/${dataset.id}/versions/${v.version_number}/download`,
        `${dataset.name}-v${v.version_number}${suffix}.csv`,
        { format: "csv", ...(sheet ? { sheet } : {}) },
      );
      setSheetPickFor(null);
    } catch (e) {
      toast({ kind: "error", title: "Download failed", msg: e instanceof ApiError ? e.detail : String(e) });
    }
  };
  return (
    <div className="table-wrap">
      {sheetPickFor && (
        <SheetDownloadModal dataset={dataset} version={sheetPickFor}
          onPick={(sheet) => download(sheetPickFor, sheet)} onClose={() => setSheetPickFor(null)} />
      )}
      <table className="data">
        <thead>
          <tr><th scope="col">Version</th><th scope="col">Status</th><th scope="col">Rows</th><th scope="col">Size</th><th scope="col">Sheets</th><th scope="col">Created</th><th scope="col">Tags</th><th scope="col"></th></tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.id}>
              <td className="mono">
                v{v.version_number}
                {v.version_number === dataset.current_version && <Badge kind="accent">current</Badge>}
              </td>
              <td><Badge kind={statusKind(v.status)}>{v.status}</Badge></td>
              <td>{fmtNum(v.row_count)}</td>
              <td>{fmtBytes(v.size_bytes)}</td>
              <td>{fmtNum(v.sheet_count)}</td>
              <td className="small secondary">{fmtDate(v.created_at)}</td>
              <td>{(v.tags || []).length ? (v.tags || []).map((t) => <Badge key={t} kind="neutral">{t}</Badge>) : <span className="muted">—</span>}</td>
              <td><button className="btn btn-sm btn-ghost" onClick={() => download(v)}>Download CSV</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** CSV holds one sheet, so a workbook version needs the caller to name one. */
function SheetDownloadModal({ dataset, version, onPick, onClose }: {
  dataset: DatasetTabProps["dataset"]; version: VersionInfo;
  onPick: (sheet: string) => void; onClose: () => void;
}) {
  const sheets = useAsync(
    () => api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${version.version_number}/sheets`),
    [dataset.id, version.version_number],
  );
  return (
    <Modal title={`Download v${version.version_number} — pick a sheet`} onClose={onClose}>
      <p className="small secondary" style={{ marginTop: 0 }}>
        This version has {fmtNum(version.sheet_count)} sheets. A CSV holds one, so choose which to export.
      </p>
      <AsyncView state={sheets}>
        {(page) => (
          <div className="col mt-16">
            {page.items.map((s) => (
              <button key={s.sheet_key} className="btn" onClick={() => onPick(s.name)}>{s.name}</button>
            ))}
          </div>
        )}
      </AsyncView>
    </Modal>
  );
}

/* ---- Tags / promotion ---- */
type TagModal = { mode: "set" | "promote" | "rollback"; tag?: TagInfo } | null;

function Tags({ dataset, tags, versions, onChange }: { dataset: DatasetTabProps["dataset"]; tags: AsyncState<Page<TagInfo>>; versions: VersionInfo[]; onChange: () => void }) {
  const toast = useToast();
  const [modal, setModal] = useState<TagModal>(null);
  const [history, setHistory] = useState<TagInfo | null>(null);

  const del = async (t: TagInfo) => {
    if (!window.confirm(`Delete tag "${t.tag_name}"? This does not delete the version it points at.`)) return;
    try {
      await api.del(`/datasets/${dataset.id}/tags/${encodeURIComponent(t.tag_name)}`);
      toast({ kind: "good", title: "Tag deleted", msg: t.tag_name });
      onChange();
    } catch (e) {
      toast({ kind: "error", title: "Delete failed", msg: e instanceof ApiError ? e.detail : String(e) });
    }
  };

  return (
    <Card title="Tags & promotion" actions={<button className="btn btn-sm btn-primary" onClick={() => setModal({ mode: "set" })}>+ Tag</button>} pad={false}>
      <AsyncView state={tags} empty={<div className="card-pad muted">No tags.</div>}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="🏷" title="No tags yet" hint="Point a tag at a version to mark a promotion." action={<button className="btn btn-sm btn-primary" onClick={() => setModal({ mode: "set" })}>+ Tag</button>} />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">Tag</th><th scope="col">Points at</th><th scope="col">Updated</th><th scope="col"></th></tr></thead>
              <tbody>
                {page.items.map((t) => (
                  <tr key={t.tag_name}>
                    <td><Badge kind="accent">{t.tag_name}</Badge></td>
                    <td className="mono">v{t.version_number}</td>
                    <td className="small secondary">{fmtDate(t.updated_at)}</td>
                    <td>
                      <div className="row gap-6">
                        <button className="btn btn-sm" onClick={() => setModal({ mode: "set", tag: t })}>Move</button>
                        <button className="btn btn-sm" onClick={() => setModal({ mode: "promote", tag: t })}>Promote</button>
                        <button className="btn btn-sm" onClick={() => setModal({ mode: "rollback", tag: t })}>Rollback</button>
                        <button className="btn btn-sm btn-ghost" onClick={() => setHistory(t)}>History</button>
                        <button className="btn btn-sm btn-danger" onClick={() => del(t)}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncView>
      {modal && <TagOpModal dataset={dataset} versions={versions} spec={modal} onClose={() => setModal(null)} onDone={() => { setModal(null); onChange(); }} />}
      {history && <TagHistoryModal dataset={dataset} tag={history} onClose={() => setHistory(null)} />}
    </Card>
  );
}

function TagOpModal({ dataset, versions, spec, onClose, onDone }: { dataset: DatasetTabProps["dataset"]; versions: VersionInfo[]; spec: NonNullable<TagModal>; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(spec.tag?.tag_name || "");
  const [version, setVersion] = useState<number>(spec.tag?.version_number ?? versions[0]?.version_number ?? dataset.current_version ?? 0);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const titles = { set: spec.tag ? "Move tag" : "Create tag", promote: "Promote tag", rollback: "Rollback tag" } as const;

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      if (spec.mode === "set") {
        await api.put(`/datasets/${dataset.id}/tags`, { tag_name: name.trim(), version_number: version });
        toast({ kind: "good", title: "Tag saved", msg: `${name.trim()} → v${version}` });
      } else if (spec.mode === "promote") {
        await api.post(`/datasets/${dataset.id}/tags/${encodeURIComponent(name)}/promote`, { version_number: version, reason: reason.trim() || undefined });
        toast({ kind: "good", title: "Promoted", msg: `${name} → v${version}` });
      } else {
        await api.post(`/datasets/${dataset.id}/tags/${encodeURIComponent(name)}/rollback`, { reason: reason.trim() || undefined });
        toast({ kind: "good", title: "Rolled back", msg: name });
      }
      onDone();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const disabled = busy || !name.trim() || (spec.mode !== "rollback" && !version);

  return (
    <Modal
      title={titles[spec.mode]}
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-primary" onClick={submit} disabled={disabled}>{busy ? "Working…" : titles[spec.mode]}</button>
      </>}
    >
      {!!error && <div style={{ marginBottom: 12 }}><ErrorBanner error={error} /></div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <Field label="Tag name">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} disabled={spec.mode !== "set" || !!spec.tag} placeholder="e.g. production" />
        </Field>
        {spec.mode !== "rollback" && (
          <Field label={spec.mode === "promote" ? "Promote to version" : "Version"}>
            <select className="select" value={version} onChange={(e) => setVersion(Number(e.target.value))}>
              {versions.map((v) => <option key={v.id} value={v.version_number}>v{v.version_number} · {v.status} · {fmtNum(v.row_count)} rows</option>)}
            </select>
          </Field>
        )}
        {spec.mode !== "set" && (
          <Field label="Reason (audited)">
            <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={spec.mode === "rollback" ? "Why roll back?" : "Why promote?"} />
          </Field>
        )}
        {spec.mode === "rollback" && <div className="small muted">Rolls the tag back to the version it previously pointed at.</div>}
      </div>
    </Modal>
  );
}

function TagHistoryModal({ dataset, tag, onClose }: { dataset: DatasetTabProps["dataset"]; tag: TagInfo; onClose: () => void }) {
  const state = useAsync(() => api.get<Page<TagHistoryEntry>>(`/datasets/${dataset.id}/tags/${encodeURIComponent(tag.tag_name)}/history`), [dataset.id, tag.tag_name]);
  return (
    <Modal title={<h3>History · <span className="mono">{tag.tag_name}</span></h3>} onClose={onClose} wide>
      <AsyncView state={state} empty={<div className="muted">No history.</div>}>
        {(page) => page.items.length === 0 ? <div className="muted">No recorded transitions.</div> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">When</th><th scope="col">Action</th><th scope="col">Change</th><th scope="col">Reason</th><th scope="col">Actor</th></tr></thead>
              <tbody>
                {page.items.map((h) => (
                  <tr key={h.id}>
                    <td className="small secondary">{fmtDate(h.created_at)}</td>
                    <td><Badge kind="neutral">{h.action}</Badge></td>
                    <td className="mono small">{h.from_version_number != null ? `v${h.from_version_number}` : "—"} → {h.to_version_number != null ? `v${h.to_version_number}` : "—"}</td>
                    <td className="small">{h.reason || <span className="muted">—</span>}</td>
                    <td className="small secondary">{h.actor_email || h.actor_user_id || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncView>
    </Modal>
  );
}

/* ---- Compare two versions ---- */
function Compare({ dataset, versions, idem }: { dataset: DatasetTabProps["dataset"]; versions: VersionInfo[]; idem: unknown[] }) {
  const latest = versions[0]?.version_number ?? null;
  const prev = versions[1]?.version_number ?? latest;
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  const f = from ?? prev;
  const t = to ?? latest;
  const valid = f != null && t != null && f !== t;

  const diff = useAsync(
    () => !valid ? Promise.resolve<WorkbookDiff | null>(null)
      : api.get<WorkbookDiff>(`/datasets/${dataset.id}/versions/${f}/diff/${t}`),
    [dataset.id, f, t, ...idem],
  );

  const [sheet, setSheet] = useState<string | null>(null);

  return (
    <Card title="Compare versions">
      <div className="row row-wrap" style={{ alignItems: "flex-end", marginBottom: 16 }}>
        <Field label="From">
          <select className="select" value={f ?? ""} onChange={(e) => { setFrom(Number(e.target.value)); setSheet(null); }} style={{ minWidth: 160 }}>
            {versions.map((v) => <option key={v.id} value={v.version_number}>v{v.version_number} · {fmtNum(v.row_count)} rows</option>)}
          </select>
        </Field>
        <span className="secondary" style={{ paddingBottom: 8 }}>→</span>
        <Field label="To">
          <select className="select" value={t ?? ""} onChange={(e) => { setTo(Number(e.target.value)); setSheet(null); }} style={{ minWidth: 160 }}>
            {versions.map((v) => <option key={v.id} value={v.version_number}>v{v.version_number} · {fmtNum(v.row_count)} rows</option>)}
          </select>
        </Field>
      </div>

      {!valid ? (
        <div className="banner info">Pick two different versions to compare.</div>
      ) : diff.loading ? <Loading /> : diff.error ? <ErrorBanner error={diff.error} /> : diff.data && (
        <>
          <WorkbookView diff={diff.data} selected={sheet} onSelect={setSheet} />
          {sheet && <SheetCompare dataset={dataset} from={f!} to={t!} sheet={sheet} idem={idem} />}
        </>
      )}
    </Card>
  );
}

function WorkbookView({ diff, selected, onSelect }: { diff: WorkbookDiff; selected: string | null; onSelect: (s: string | null) => void }) {
  const empty = !diff.added.length && !diff.removed.length && !diff.modified.length && !diff.renamed.length && !diff.rename_candidates.length;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="grid grid-4">
        <StatTile label="Added sheets" value={diff.added.length} />
        <StatTile label="Removed sheets" value={diff.removed.length} />
        <StatTile label="Modified sheets" value={diff.modified.length} />
        <StatTile label="Unchanged" value={diff.unchanged.length} />
      </div>

      {empty && <div className="banner info">These two versions have identical sheet structure.</div>}

      {diff.renamed.length > 0 && (
        <div>
          <div className="small secondary" style={{ marginBottom: 6 }}>Renamed sheets</div>
          <div className="pill-row">
            {diff.renamed.map((r) => <Badge key={r.logical_sheet_id} kind="accent">{r.from_sheet} → {r.to_sheet}</Badge>)}
          </div>
        </div>
      )}

      {diff.rename_candidates.length > 0 && (
        <div>
          <div className="small secondary" style={{ marginBottom: 6 }}>Possible renames (advisory)</div>
          {diff.rename_candidates.map((c, i) => (
            <div key={i} className="row row-wrap gap-6 small mt-8">
              <Badge kind={c.confidence === "high" ? "good" : "warning"}>{c.confidence}</Badge>
              <span className="mono">{c.from_sheet} → {c.to_sheet}</span>
              <span className="muted">{c.reason}</span>
            </div>
          ))}
        </div>
      )}

      {(diff.added.length > 0 || diff.removed.length > 0) && (
        <div className="grid grid-2">
          {diff.added.length > 0 && (
            <div>
              <div className="small secondary" style={{ marginBottom: 6 }}>Added</div>
              <div className="pill-row">{diff.added.map((s) => <Badge key={s.name} kind="good">+ {s.name} ({fmtNum(s.row_count)})</Badge>)}</div>
            </div>
          )}
          {diff.removed.length > 0 && (
            <div>
              <div className="small secondary" style={{ marginBottom: 6 }}>Removed</div>
              <div className="pill-row">{diff.removed.map((s) => <Badge key={s.name} kind="critical">− {s.name} ({fmtNum(s.row_count)})</Badge>)}</div>
            </div>
          )}
        </div>
      )}

      {diff.modified.length > 0 && (
        <div>
          <div className="small secondary" style={{ marginBottom: 6 }}>Modified sheets — select one to see column &amp; row changes</div>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">Sheet</th><th scope="col">Schema</th><th scope="col">Row Δ</th><th scope="col">Visibility</th><th scope="col"></th></tr></thead>
              <tbody>
                {diff.modified.map((m) => {
                  const name = m.to_sheet || m.from_sheet;
                  return (
                    <tr key={m.sheet_key} className={cx("clickable", selected === name && "accent")} onClick={() => onSelect(selected === name ? null : name)}>
                      <td className="mono">{m.from_sheet !== m.to_sheet ? `${m.from_sheet} → ${m.to_sheet}` : name}</td>
                      <td>{m.schema_changed ? <Badge kind="warning">changed</Badge> : <span className="muted">—</span>}</td>
                      <td className={cx(m.row_count_delta != null && m.row_count_delta !== 0 && (m.row_count_delta > 0 ? "good" : "critical"))}>{deltaStr(m.row_count_delta)}</td>
                      <td>{m.visibility_changed ? <Badge kind="warning">changed</Badge> : <span className="muted">—</span>}</td>
                      <td className="small accent">{selected === name ? "▼ open" : "▸ inspect"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {diff.unchanged.length > 0 && (
        <div className="small muted">Unchanged: {diff.unchanged.join(", ")}</div>
      )}
    </div>
  );
}

function SheetCompare({ dataset, from, to, sheet, idem }: { dataset: DatasetTabProps["dataset"]; from: number; to: number; sheet: string; idem: unknown[] }) {
  const schema = useAsync(
    () => api.get<SheetDiff>(`/datasets/${dataset.id}/versions/${from}/sheets/${encodeURIComponent(sheet)}/diff/${to}`),
    [dataset.id, from, to, sheet, ...idem],
  );
  // Column list (for the row-diff key picker) comes from the "to" version's sheet schema.
  const cols = useAsync(
    () => api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${to}/sheets`),
    [dataset.id, to, ...idem],
  );
  const columns = useMemo(() => {
    const s = cols.data?.items.find((x) => x.name === sheet);
    return (s?.columns || []).map((c) => c.normalized_name || c.name);
  }, [cols.data, sheet]);

  return (
    <div style={{ marginTop: 20, borderTop: "1px solid var(--border, #333)", paddingTop: 16 }}>
      <h3 style={{ marginBottom: 12 }}>Sheet · <span className="mono">{sheet}</span></h3>
      <AsyncView state={schema}>
        {(d) => (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {d.identical ? (
              <div className="banner info">Schema is identical across these versions.</div>
            ) : (
              <div className="grid grid-3">
                <ColBlock title="Added columns" kind="good" items={d.added_columns.map((c) => `${c.name} : ${c.dtype}`)} />
                <ColBlock title="Removed columns" kind="critical" items={d.removed_columns.map((c) => `${c.name} : ${c.dtype}`)} />
                <ColBlock title="Type changes" kind="warning" items={d.type_changes.map((c) => `${c.column}: ${c.from_dtype} → ${c.to_dtype}`)} />
              </div>
            )}
            <div className="row row-wrap small secondary gap-16">
              <span>Rows: {fmtNum(d.from_row_count)} → {fmtNum(d.to_row_count)}</span>
              <span>Δ {deltaStr(d.row_count_delta)}</span>
              {d.nullability_changes && d.nullability_changes.length > 0 && <Badge kind="warning">{d.nullability_changes.length} nullability change(s)</Badge>}
            </div>

            <RowDiffPanel dataset={dataset} from={from} to={to} sheet={sheet} columns={columns} colsLoading={cols.loading} />
          </div>
        )}
      </AsyncView>
    </div>
  );
}

function ColBlock({ title, kind, items }: { title: string; kind: "good" | "critical" | "warning"; items: string[] }) {
  return (
    <div>
      <div className="small secondary" style={{ marginBottom: 6 }}>{title} ({items.length})</div>
      {items.length === 0 ? <div className="muted small">—</div> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {items.map((it, i) => <Badge key={i} kind={kind}>{it}</Badge>)}
        </div>
      )}
    </div>
  );
}

function RowDiffPanel({ dataset, from, to, sheet, columns, colsLoading }: { dataset: DatasetTabProps["dataset"]; from: number; to: number; sheet: string; columns: string[]; colsLoading: boolean }) {
  const [key, setKey] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RowDiff | null>(null);
  const [error, setError] = useState<unknown>(null);
  const activeKey = key || columns[0] || "";

  const run = async () => {
    if (!activeKey) return;
    setRunning(true); setError(null); setResult(null);
    try {
      const r = await api.post<RowDiff>(`/datasets/${dataset.id}/versions/${from}/sheets/${encodeURIComponent(sheet)}/row-diff/${to}`, { key: [activeKey] });
      setResult(r);
    } catch (e) {
      setError(e); // 409 when the key is not unique — surfaced below.
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="card card-pad">
      <div className="row row-wrap" style={{ alignItems: "flex-end" }}>
        <Field label="Key column (must be unique)">
          <select className="select" value={activeKey} onChange={(e) => { setKey(e.target.value); setResult(null); setError(null); }} style={{ minWidth: 200 }} disabled={colsLoading || columns.length === 0}>
            {columns.length === 0 && <option value="">{colsLoading ? "Loading columns…" : "No columns"}</option>}
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <button className="btn btn-primary btn-sm" onClick={run} disabled={running || !activeKey}>{running ? "Diffing…" : "Run row diff"}</button>
      </div>

      {!!error && <div className="mt-16">{error instanceof ApiError && error.status === 409
        ? <div className="banner warning"><span>⚠</span><span className="wrap-anywhere">{error.detail}</span></div>
        : <ErrorBanner error={error} />}</div>}

      {result && (
        <div className="mt-16" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="grid grid-4">
            <StatTile label="Added" value={fmtNum(result.added)} />
            <StatTile label="Removed" value={fmtNum(result.removed)} />
            <StatTile label="Changed" value={fmtNum(result.changed)} />
            <StatTile label="Unchanged" value={fmtNum(result.unchanged)} />
          </div>
          {result.masked_columns && result.masked_columns.length > 0 && (
            <Badge kind="warning">masked: {result.masked_columns.join(", ")}</Badge>
          )}

          {result.column_changes.length > 0 && (
            <div>
              <div className="small secondary" style={{ marginBottom: 6 }}>Changed cells by column</div>
              <div className="pill-row">{result.column_changes.map((c) => <Badge key={c.column} kind="warning">{c.column}: {fmtNum(c.changed_rows)}</Badge>)}</div>
            </div>
          )}

          {result.changed_sample.length > 0 && (
            <div>
              <div className="small secondary" style={{ marginBottom: 6 }}>Sample cell changes</div>
              <div className="table-wrap" style={{ maxHeight: 320 }}>
                <table className="data">
                  <thead><tr><th scope="col">Row key</th><th scope="col">Column</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
                  <tbody>
                    {result.changed_sample.map((c, i) => (
                      <tr key={i}>
                        <td className="mono"><Cell value={c.row_key} /></td>
                        <td>{String(c.column_name ?? "")}</td>
                        <td><Cell value={c.before_value} /></td>
                        <td><Cell value={c.after_value} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(result.added_sample.length > 0 || result.removed_sample.length > 0) && (
            <div className="grid grid-2">
              <RowSample title="Added rows" rows={result.added_sample} />
              <RowSample title="Removed rows" rows={result.removed_sample} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RowSample({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]);
  return (
    <div>
      <div className="small secondary" style={{ marginBottom: 6 }}>{title} ({rows.length})</div>
      <div className="table-wrap" style={{ maxHeight: 260 }}>
        <table className="data">
          <thead><tr>{cols.map((c) => <th scope="col" key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}><Cell value={r[c]} /></td>)}</tr>)}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function deltaStr(n?: number | null): string {
  if (n == null) return "—";
  if (n === 0) return "0";
  return n > 0 ? `+${fmtNum(n)}` : fmtNum(n);
}
