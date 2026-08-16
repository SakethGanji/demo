import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { Page } from "../../api/client";
import { AsyncView, Badge, Cell, Field, Loading, Modal, ErrorBanner, fmtNum, useAsync, useToast } from "../../components/ui";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

interface VersionInfo { version_number: number; status: string; row_count?: number; }
interface Column { name: string; normalized_name?: string; dtype?: string; }
interface Sheet { name: string; sheet_key: string; columns?: Column[]; row_count?: number; }
interface QueryPage { items: Record<string, unknown>[]; next_cursor: string | null; total: number; masked_columns: string[]; }

const OPS = ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "icontains", "starts_with", "between", "in", "is_null", "not_null"];

export function Explore({ dataset }: DatasetTabProps) {
  const { identity } = useIdentity();
  const idem = [dataset.id, identity.userId, identity.teamId];
  const versions = useAsync(() => api.get<Page<VersionInfo>>(`/datasets/${dataset.id}/versions`), idem);
  const [version, setVersion] = useState<number | null>(null);
  const v = version ?? dataset.current_version ?? versions.data?.items[0]?.version_number ?? null;

  const sheets = useAsync(
    () => v == null ? Promise.resolve<Page<Sheet>>({ items: [], total: 0, limit: 1, offset: 0 })
      : api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${v}/sheets`),
    [dataset.id, v, identity.userId, identity.teamId],
  );
  const [sheet, setSheet] = useState<string | null>(null);
  const sheetName = sheet ?? sheets.data?.items.find((s) => (s as unknown as { is_default?: boolean }).is_default)?.name ?? sheets.data?.items[0]?.name ?? null;
  const activeSheet = sheets.data?.items.find((s) => s.name === sheetName) || null;

  return (
    <div>
      <div className="row row-wrap" style={{ marginBottom: 16 }}>
        <Field label="Version">
          <select className="select" value={v ?? ""} onChange={(e) => setVersion(Number(e.target.value))} style={{ minWidth: 120 }}>
            {(versions.data?.items || []).map((ver) => <option key={ver.version_number} value={ver.version_number}>v{ver.version_number} ({fmtNum(ver.row_count)} rows)</option>)}
          </select>
        </Field>
        {(sheets.data?.items.length || 0) > 1 && (
          <Field label="Sheet">
            <select className="select" value={sheetName ?? ""} onChange={(e) => setSheet(e.target.value)} style={{ minWidth: 140 }}>
              {sheets.data!.items.map((s) => <option key={s.sheet_key} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
        )}
      </div>

      {/* A failed request (deleted dataset, no access) must not be reported as
          "not ready" — every other tab says "not found", and this one sent the
          user chasing an ingestion problem that didn't exist. */}
      {versions.error || sheets.error ? (
        <ErrorBanner error={versions.error || sheets.error} />
      ) : versions.loading || sheets.loading ? <Loading /> : v == null || !sheetName ? (
        <div className="banner info">This version isn't ready to explore yet.</div>
      ) : (
        <Grid datasetId={dataset.id} version={v} sheet={sheetName} sheetKey={activeSheet?.sheet_key || sheetName} columns={activeSheet?.columns || []} idem={idem} />
      )}
    </div>
  );
}

interface Cond { column: string; op: string; value: string; }

function Grid({ datasetId, version, sheet, sheetKey, columns, idem }: { datasetId: string; version: number; sheet: string; sheetKey: string; columns: Column[]; idem: unknown[] }) {
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" } | null>(null);
  const [conds, setConds] = useState<Cond[]>([]);
  const [applied, setApplied] = useState<Cond[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [stack, setStack] = useState<(string | null)[]>([]);
  const [profileCol, setProfileCol] = useState<string | null>(null);

  useEffect(() => { setCursor(null); setStack([]); }, [sort, applied, sheet, version]);

  const filters = useMemo(() => {
    // Skip incomplete conditions — a value-less operator (other than the null
    // checks) would send an empty value the API rightly rejects as a 400.
    const needsValue = (op: string) => !["is_null", "not_null"].includes(op);
    const valid = applied.filter((c) => c.column && c.op && (!needsValue(c.op) || c.value.trim() !== ""));
    if (!valid.length) return undefined;
    return { logic: "and", conditions: valid.map((c) => ({ column: c.column, op: c.op, value: parseVal(c.op, c.value) })) };
  }, [applied]);

  const state = useAsync(
    () => api.post<QueryPage>(`/datasets/${datasetId}/versions/${version}/sheets/${encodeURIComponent(sheet)}/query`, {
      sort: sort ? [sort] : undefined, filters, cursor: cursor || undefined, limit: 25,
    }),
    [datasetId, version, sheet, sort, applied, cursor, ...idem],
  );

  const colNames = columns.map((c) => c.normalized_name || c.name);

  return (
    <div>
      <FilterBar columns={colNames} conds={conds} setConds={setConds} applied={applied}
        onApply={() => setApplied(conds)} onClear={() => setApplied([])} />
      <AsyncView state={state}>
        {(page) => {
          const cols = page.items.length ? Object.keys(page.items[0]) : colNames;
          return (
            <>
              <div className="row small secondary" style={{ margin: "4px 2px 10px" }}>
                <span><strong>{fmtNum(page.total)}</strong> rows</span>
                {page.masked_columns?.length > 0 && <Badge kind="warning">masked: {page.masked_columns.join(", ")}</Badge>}
              </div>
              <div className="card">
                <div className="table-wrap" style={{ maxHeight: 520 }}>
                  <table className="data">
                    <thead>
                      <tr>{cols.map((c) => (
                        <th scope="col" key={c} aria-sort={sort?.column === c ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
                          {/* Real buttons: the sort used to live on the <th scope="col"> and the
                              stats affordance was a <span>, so neither was reachable
                              by keyboard and the header announced as "idⓘ". */}
                          <button
                            className="btn btn-ghost btn-sm"
                            style={{ padding: "2px 4px", font: "inherit", fontWeight: 600 }}
                            onClick={() => setSort((s) => s?.column === c ? { column: c, direction: s.direction === "asc" ? "desc" : "asc" } : { column: c, direction: "asc" })}
                            aria-label={`Sort by ${c}`}
                          >
                            {c}{sort?.column === c ? (sort.direction === "asc" ? " ▲" : " ▼") : ""}
                          </button>
                          <button
                            className="icon-btn"
                            style={{ padding: 2, marginLeft: 2 }}
                            onClick={() => setProfileCol(c)}
                            title={`Column stats for ${c}`}
                            aria-label={`Column stats for ${c}`}
                          >ⓘ</button>
                        </th>
                      ))}</tr>
                    </thead>
                    <tbody>
                      {page.items.map((row, i) => <tr key={i}>{cols.map((c) => <td key={c}><Cell value={row[c]} /></td>)}</tr>)}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="row mt-16">
                <button className="btn btn-sm" disabled={stack.length === 0} onClick={() => { const s = [...stack]; const prev = s.pop() ?? null; setStack(s); setCursor(prev); }}>← Prev</button>
                <button className="btn btn-sm" disabled={!page.next_cursor} onClick={() => { setStack((s) => [...s, cursor]); setCursor(page.next_cursor); }}>Next →</button>
                <span className="spacer" />
                <span className="small muted">Page {stack.length + 1}</span>
              </div>
            </>
          );
        }}
      </AsyncView>
      {profileCol && <ColumnProfile datasetId={datasetId} version={version} sheet={sheet} sheetKey={sheetKey} column={profileCol} onClose={() => setProfileCol(null)} />}
    </div>
  );
}

function FilterBar({ columns, conds, setConds, applied, onApply, onClear }: {
  columns: string[]; conds: Cond[]; setConds: (c: Cond[]) => void; applied: Cond[]; onApply: () => void; onClear: () => void;
}) {
  const add = () => setConds([...conds, { column: columns[0] || "", op: "eq", value: "" }]);
  const upd = (i: number, patch: Partial<Cond>) => setConds(conds.map((c, j) => j === i ? { ...c, ...patch } : c));
  // Removing the last condition must also drop the applied filter — otherwise
  // the bar reads "no filters" while the grid is still showing filtered rows,
  // with no Apply button left to commit the change.
  const rm = (i: number) => {
    const next = conds.filter((_, j) => j !== i);
    setConds(next);
    if (next.length === 0) onClear();
  };
  return (
    <div className="card card-pad" style={{ marginBottom: 16 }}>
      <div className="row">
        <strong className="small">Filters</strong>
        {applied.length > 0 && <span className="badge accent">{applied.length} active</span>}
        <span className="spacer" />
        {applied.length > 0 && <button className="btn btn-sm btn-ghost" onClick={() => { setConds([]); onClear(); }}>Clear all</button>}
        <button className="btn btn-sm" onClick={add}>+ Condition</button>
      </div>
      {conds.map((c, i) => (
        <div className="row row-wrap mt-8" key={i}>
          <select className="select" style={{ maxWidth: 200 }} value={c.column} onChange={(e) => upd(i, { column: e.target.value })}>{columns.map((col) => <option key={col} value={col}>{col}</option>)}</select>
          <select className="select" style={{ maxWidth: 130 }} value={c.op} onChange={(e) => upd(i, { op: e.target.value })}>{OPS.map((o) => <option key={o} value={o}>{o}</option>)}</select>
          {!["is_null", "not_null"].includes(c.op) && <input className="input" style={{ maxWidth: 200 }} placeholder={c.op === "between" || c.op === "in" ? "comma,separated" : "value"} value={c.value} onChange={(e) => upd(i, { value: e.target.value })} />}
          <button className="icon-btn" onClick={() => rm(i)} title="Remove" aria-label="Remove">✕</button>
        </div>
      ))}
      {conds.length > 0 && <div className="row mt-16"><button className="btn btn-primary btn-sm" onClick={onApply}>Apply filters</button></div>}
    </div>
  );
}

const SENSITIVITY = ["", "confidential", "pii", "restricted"];

/** Render one profile statistic. `top_values`/`rare_values` are arrays of
 *  {value,count,percent} objects, which String() turned into a row of
 *  "[object Object]" — the panel's most useful fields were unreadable. */
function statValue(val: unknown): string {
  if (val === null || val === undefined) return "—";
  if (typeof val === "number") return fmtNum(val);
  if (Array.isArray(val)) {
    if (val.length === 0) return "—";
    return val
      .map((v) => {
        if (v && typeof v === "object") {
          const o = v as { value?: unknown; count?: unknown };
          return o.count === undefined ? String(o.value) : `${String(o.value)} (${fmtNum(o.count)})`;
        }
        return String(v);
      })
      .join(", ");
  }
  if (typeof val === "object") return JSON.stringify(val);
  return String(val);
}

function ColumnProfile({ datasetId, version, sheet, sheetKey, column, onClose }: { datasetId: string; version: number; sheet: string; sheetKey: string; column: string; onClose: () => void }) {
  const toast = useToast();
  const stats = useAsync(() => api.get<Record<string, unknown>>(`/datasets/${datasetId}/versions/${version}/sheets/${encodeURIComponent(sheet)}/columns/${encodeURIComponent(column)}`), [datasetId, version, sheet, column]);
  // Read the whole column collection (always 200) rather than the per-column
  // entry, which 404s for any column with no dictionary row yet — a handled but
  // noisy failure the browser logs to the console on every open.
  const dict = useAsync(async () => {
    const page = await api.get<{ items: Record<string, unknown>[] }>(`/datasets/${datasetId}/sheet-metadata/${encodeURIComponent(sheetKey)}/columns`);
    return (page.items || []).find((c) => c.column_name === column) || {};
  }, [datasetId, sheetKey, column]);

  return (
    <Modal title={<h3>Column · <span className="mono">{column}</span></h3>} onClose={onClose} wide>
      <div className="grid grid-2">
        <div>
          <h4 className="secondary" style={{ marginBottom: 8 }}>Statistics</h4>
          {stats.loading ? <Loading /> : stats.error ? <ErrorBanner error={stats.error} /> : stats.data && (
            <dl className="kv">
              {Object.entries(stats.data).filter(([k]) => !["column", "name", "dtype"].includes(k)).map(([k, val]) => (
                <div key={k} style={{ display: "contents" }}><dt>{k.replace(/_/g, " ")}</dt><dd>{statValue(val)}</dd></div>
              ))}
            </dl>
          )}
        </div>
        <div>
          <h4 className="secondary" style={{ marginBottom: 8 }}>Data dictionary</h4>
          {dict.loading ? <Loading /> : (
            <DictEditor datasetId={datasetId} sheetKey={sheetKey} column={column} current={dict.data || {}}
              onSaved={() => { toast({ kind: "good", title: "Dictionary updated" }); dict.reload(); }} />
          )}
        </div>
      </div>
    </Modal>
  );
}

function DictEditor({ datasetId, sheetKey, column, current, onSaved }: { datasetId: string; sheetKey: string; column: string; current: Record<string, unknown>; onSaved: () => void }) {
  const [businessName, setBusinessName] = useState((current.business_name as string) || "");
  const [description, setDescription] = useState((current.description as string) || "");
  const [unit, setUnit] = useState((current.unit as string) || "");
  const [sensitivity, setSensitivity] = useState((current.sensitivity as string) || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const save = async () => {
    setBusy(true); setError(null);
    try {
      await api.put(`/datasets/${datasetId}/sheet-metadata/${encodeURIComponent(sheetKey)}/columns/${encodeURIComponent(column)}`, {
        business_name: businessName.trim() || null,
        description: description.trim() || null,
        unit: unit.trim() || null,
        sensitivity: sensitivity || null,
      });
      onSaved();
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <div className="col">
      <Field label="Business name"><input className="input" value={businessName} onChange={(e) => setBusinessName(e.target.value)} /></Field>
      <Field label="Description"><textarea className="input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      <Field label="Unit"><input className="input" value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="e.g. USD, %, count" /></Field>
      <Field label="Sensitivity (marking a column sensitive masks it for non-elevated users)">
        <select className="select" value={sensitivity} onChange={(e) => setSensitivity(e.target.value)}>
          {SENSITIVITY.map((s) => <option key={s} value={s}>{s || "none"}</option>)}
        </select>
      </Field>
      {error != null && <ErrorBanner error={error} />}
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn btn-primary btn-sm" disabled={busy} onClick={save}>Save dictionary</button></div>
    </div>
  );
}

function parseVal(op: string, raw: string): unknown {
  if (op === "is_null" || op === "not_null") return null;
  if (op === "between" || op === "in") return raw.split(",").map((s) => coerce(s.trim()));
  return coerce(raw);
}
function coerce(s: string): unknown {
  if (s === "") return s;
  const n = Number(s);
  return Number.isNaN(n) ? s : n;
}
