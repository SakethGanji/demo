import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { Page } from "../../api/client";
import {
  AsyncView, Badge, Card, Cell, EmptyState, ErrorBanner, Field, Loading, Modal,
  Tabs, cx, fmtDate, fmtNum, useAsync, useToast,
} from "../../components/ui";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

// ---- Shapes (see app/features/explorer + app/features/library) ----
interface Column { name: string; normalized_name?: string; dtype?: string; }
interface Sheet { name: string; sheet_key: string; is_default?: boolean; columns?: Column[]; }
interface Cond { column: string; op: string; value: string; }
interface QuerySpecShape {
  columns?: string[] | null;
  filters?: { logic: string; conditions: { column: string; op: string; value?: unknown }[] } | null;
  sort?: { column: string; direction: string }[];
  search?: string | null;
}
interface ViewOut {
  id: string; dataset_id: string; logical_sheet_id: string;
  sheet_key?: string | null; sheet_name?: string | null;
  name: string; description?: string | null;
  version_selector: Record<string, unknown>;
  query: QuerySpecShape;
  created_by?: string | null; created_at: string; updated_at: string;
}
interface QueryPage { items: Record<string, unknown>[]; next_cursor: string | null; total: number | null; masked_columns: string[]; }
interface ViewRunResponse { view_id: string; version_number: number; sheet_name: string; result: QueryPage; }
interface SqlResponse { columns: string[]; items: Record<string, unknown>[]; row_count: number; truncated: boolean; tables: string[]; result_file: string; }
interface ExportResponse { export_file: string; format: string; size_bytes: number; media_type: string; source_file: string; }
interface LineageNode { id: string; name: string; domain?: string | null; deprecated: boolean; created_at: string; is_root: boolean; }
interface LineageEdge { child_id: string; parent_id: string; relation: string; depth: number; }
interface LineageGraph { dataset_id: string; nodes: LineageNode[]; edges: LineageEdge[]; max_depth: number; truncated: boolean; hidden_nodes: number; }

// Filter operators — vocabulary from app/shared/query/schemas.py::FilterOp.
const OPS = ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "icontains", "starts_with", "ends_with", "in", "not_in", "between", "is_null", "is_not_null"];
const NO_VALUE_OPS = ["is_null", "is_not_null", "is_empty", "is_not_empty", "is_duplicate", "is_unique"];
const LIST_OR_PAIR_OPS = ["in", "not_in", "between", "not_between", "len_between", "date_between"];

const RELATION_LABELS: Record<string, string> = {
  aggregated_from: "aggregated from",
  transformed_from: "transformed from",
  joined_from: "joined from",
  published_from: "published from",
  uploaded: "uploaded",
};

const SUBTABS = [
  { id: "views", label: "Saved views" },
  { id: "sql", label: "SQL console" },
  { id: "lineage", label: "Lineage" },
];

export function Library({ dataset }: DatasetTabProps) {
  const [sub, setSub] = useState("views");
  return (
    <div>
      <Tabs tabs={SUBTABS} active={sub} onChange={setSub} />
      {sub === "views" && <SavedViews dataset={dataset} />}
      {sub === "sql" && <SqlConsole dataset={dataset} />}
      {sub === "lineage" && <Lineage dataset={dataset} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Saved views
// ---------------------------------------------------------------------------
function SavedViews({ dataset }: { dataset: DatasetTabProps["dataset"] }) {
  const { identity } = useIdentity();
  const toast = useToast();
  const idem = [dataset.id, identity.userId, identity.teamId];
  const views = useAsync(() => api.get<Page<ViewOut>>(`/datasets/${dataset.id}/views`), idem);

  const v = dataset.current_version ?? null;
  const sheets = useAsync(
    () => v == null ? Promise.resolve<Page<Sheet>>({ items: [], total: 0, limit: 1, offset: 0 })
      : api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${v}/sheets`),
    [dataset.id, v, identity.userId, identity.teamId],
  );

  const [editing, setEditing] = useState<ViewOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [running, setRunning] = useState<ViewOut | null>(null);

  const del = async (view: ViewOut) => {
    if (!confirm(`Delete view "${view.name}"?`)) return;
    try {
      await api.del(`/datasets/${dataset.id}/views/${view.id}`);
      toast({ kind: "good", title: "View deleted", msg: view.name });
      views.reload();
    } catch (e) {
      toast({ kind: "error", title: "Delete failed", msg: e instanceof ApiError ? `${e.detail} (${e.code})` : String(e) });
    }
  };

  return (
    <div>
      <div className="row" style={{ marginBottom: 16 }}>
        <div className="small secondary">Reusable queries pinned to a version selector — rename-proof and masking-aware.</div>
        <span className="spacer" />
        <button className="btn btn-primary btn-sm" disabled={v == null} onClick={() => setCreating(true)}>+ New view</button>
      </div>

      <AsyncView state={views} empty={<EmptyState icon="◇" title="No saved views" hint="Create a view to save a projection, filter and sort." />}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="◇" title="No saved views" hint="Create a view to save a projection, filter and sort." />
        ) : (
          <div className="grid grid-2">
            {page.items.map((view) => (
              <Card key={view.id}>
                <div className="row">
                  <h3 style={{ flex: 1 }}>{view.name}</h3>
                  <Badge kind="neutral">{view.sheet_name || view.sheet_key || "sheet"}</Badge>
                </div>
                {view.description && <div className="secondary small mt-8">{view.description}</div>}
                <dl className="kv mt-16">
                  <div style={{ display: "contents" }}><dt>columns</dt><dd>{view.query.columns?.length ? view.query.columns.join(", ") : "all"}</dd></div>
                  <div style={{ display: "contents" }}><dt>filters</dt><dd>
                    {fmtNum(view.query.filters?.conditions?.length || 0)}
                    {(view.query.filters?.logic || "and") !== "and" && ` (${view.query.filters?.logic})`}
                  </dd></div>
                  {view.query.search && (
                    <div style={{ display: "contents" }}><dt>search</dt><dd>“{view.query.search}”</dd></div>
                  )}
                  <div style={{ display: "contents" }}><dt>sort</dt><dd>{view.query.sort?.length ? view.query.sort.map((s) => `${s.column} ${s.direction}`).join(", ") : "—"}</dd></div>
                  <div style={{ display: "contents" }}><dt>version</dt><dd>{selectorLabel(view.version_selector)}</dd></div>
                  <div style={{ display: "contents" }}><dt>updated</dt><dd>{fmtDate(view.updated_at)}</dd></div>
                </dl>
                <div className="row mt-16 gap-6">
                  <button className="btn btn-primary btn-sm" onClick={() => setRunning(view)}>Run</button>
                  <button className="btn btn-sm" onClick={() => setEditing(view)}>Edit</button>
                  <span className="spacer" />
                  <button className="btn btn-danger btn-sm" onClick={() => del(view)}>Delete</button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </AsyncView>

      {(creating || editing) && (
        <ViewEditor
          datasetId={dataset.id}
          sheets={sheets.data?.items || []}
          sheetsLoading={sheets.loading}
          existing={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={(name, isNew) => {
            setCreating(false); setEditing(null);
            toast({ kind: "good", title: isNew ? "View created" : "View updated", msg: name });
            views.reload();
          }}
        />
      )}
      {running && <RunViewModal datasetId={dataset.id} view={running} onClose={() => setRunning(null)} />}
    </div>
  );
}

function ViewEditor({ datasetId, sheets, sheetsLoading, existing, onClose, onSaved }: {
  datasetId: string; sheets: Sheet[]; sheetsLoading: boolean; existing: ViewOut | null;
  onClose: () => void; onSaved: (name: string, isNew: boolean) => void;
}) {
  const isNew = !existing;
  const [name, setName] = useState(existing?.name || "");
  const [description, setDescription] = useState(existing?.description || "");
  const [sheet, setSheet] = useState(existing?.sheet_name || existing?.sheet_key || "");
  const [selectedCols, setSelectedCols] = useState<string[]>(existing?.query.columns || []);
  // A condition that has its own `conditions` is a nested group. This flat
  // editor cannot represent one: it used to render as a blank row and then get
  // dropped on save, silently changing which rows the view returns. Detect it,
  // leave the filters untouched, and say so.
  const originalConditions = existing?.query.filters?.conditions || [];
  const hasNestedFilters = originalConditions.some(
    (c) => !!c && typeof c === "object" && Array.isArray((c as { conditions?: unknown[] }).conditions),
  );
  const [conds, setConds] = useState<Cond[]>(
    (hasNestedFilters ? [] : originalConditions).map((c) => ({
      column: c.column, op: c.op,
      value: c.value == null ? "" : Array.isArray(c.value) ? (c.value as unknown[]).join(",") : String(c.value),
    })),
  );
  const [sortCol, setSortCol] = useState(existing?.query.sort?.[0]?.column || "");
  const [sortDir, setSortDir] = useState(existing?.query.sort?.[0]?.direction || "asc");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Default the sheet to the first one when creating.
  useEffect(() => {
    if (isNew && !sheet && sheets.length) setSheet(sheets[0].name);
  }, [isNew, sheet, sheets]);

  const activeSheet = sheets.find((s) => s.name === sheet || s.sheet_key === sheet);
  const colNames = (activeSheet?.columns || []).map((c) => c.normalized_name || c.name);

  const toggleCol = (c: string) => setSelectedCols((cur) => cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]);
  const addCond = () => setConds((cur) => [...cur, { column: colNames[0] || "", op: "eq", value: "" }]);
  const updCond = (i: number, patch: Partial<Cond>) => setConds((cur) => cur.map((c, j) => j === i ? { ...c, ...patch } : c));
  const rmCond = (i: number) => setConds((cur) => cur.filter((_, j) => j !== i));

  // Start from the stored spec and override only what this editor owns, so
  // parts it can't express — `search`, a non-"and" `logic`, extra sort keys,
  // nested filter groups — survive an edit instead of being silently dropped
  // (which changed the view's answer with no warning).
  const buildQuery = (): QuerySpecShape => {
    const base: QuerySpecShape = { ...(existing?.query || {}) } as QuerySpecShape;
    base.columns = selectedCols.length ? selectedCols : null;

    if (!hasNestedFilters) {
      const conditions = conds.filter((c) => c.column && c.op).map((c) => {
        const cond: { column: string; op: string; value?: unknown } = { column: c.column, op: c.op };
        if (!NO_VALUE_OPS.includes(c.op)) cond.value = parseVal(c.op, c.value);
        return cond;
      });
      base.filters = conditions.length
        ? { logic: existing?.query.filters?.logic || "and", conditions }
        : null;
    }

    // Keep any sort keys past the first — the editor only exposes one.
    const extraSort = (existing?.query.sort || []).slice(1);
    base.sort = sortCol ? [{ column: sortCol, direction: sortDir }, ...extraSort] : extraSort;
    return base;
  };

  const submit = async () => {
    setSaving(true); setError(null);
    try {
      if (isNew) {
        await api.post(`/datasets/${datasetId}/views`, {
          name, description: description || null, sheet,
          version_selector: { mode: "current" }, query: buildQuery(),
        });
      } else {
        await api.patch(`/datasets/${datasetId}/views/${existing!.id}`, {
          name, description: description || null, sheet, query: buildQuery(),
        });
      }
      onSaved(name, isNew);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  const canSave = name.trim().length > 0 && sheet.length > 0 && !saving;

  return (
    <Modal
      wide
      title={<h3>{isNew ? "New view" : `Edit · ${existing!.name}`}</h3>}
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose} disabled={saving}>Cancel</button>
        <button className="btn btn-primary" onClick={submit} disabled={!canSave}>{saving ? "Saving…" : isNew ? "Create view" : "Save"}</button>
      </>}
    >
      {error ? <div style={{ marginBottom: 12 }}><ErrorBanner error={error} /></div> : null}
      <div className="row row-wrap">
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="View name" style={{ minWidth: 220 }} /></Field>
        <Field label="Sheet">
          <select className="select" value={sheet} onChange={(e) => { setSheet(e.target.value); setSelectedCols([]); setConds([]); setSortCol(""); }} style={{ minWidth: 160 }}>
            {sheetsLoading && <option value="">Loading…</option>}
            {sheets.map((s) => <option key={s.sheet_key} value={s.name}>{s.name}</option>)}
          </select>
        </Field>
      </div>
      <div className="mt-8"><Field label="Description"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional" style={{ width: "100%" }} /></Field></div>

      <div className="mt-16">
        <strong className="small">Columns <span className="muted">(none = all)</span></strong>
        <div className="card card-pad mt-8" style={{ maxHeight: 160, overflow: "auto" }}>
          {colNames.length === 0 ? <span className="muted small">No columns for this sheet.</span> : (
            <div className="row row-wrap gap-6">
              {colNames.map((c) => (
                <label key={c} className={cx("pill-row", "clickable")} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "2px 8px" }}>
                  <input type="checkbox" checked={selectedCols.includes(c)} onChange={() => toggleCol(c)} />
                  <span className="mono small">{c}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="mt-16">
        <div className="row"><strong className="small">Filters</strong><span className="spacer" /><button className="btn btn-sm" onClick={addCond} disabled={!colNames.length || hasNestedFilters}>+ Condition</button></div>
        {hasNestedFilters && (
          <div className="banner info mt-8">
            This view uses a grouped filter this editor can't display. It is preserved
            exactly as-is — edit the name, columns, or sort here without affecting it.
          </div>
        )}
        {conds.map((c, i) => (
          <div className="row row-wrap mt-8" key={i}>
            <select className="select" style={{ maxWidth: 200 }} value={c.column} onChange={(e) => updCond(i, { column: e.target.value })}>{colNames.map((col) => <option key={col} value={col}>{col}</option>)}</select>
            <select className="select" style={{ maxWidth: 140 }} value={c.op} onChange={(e) => updCond(i, { op: e.target.value })}>{OPS.map((o) => <option key={o} value={o}>{o}</option>)}</select>
            {!NO_VALUE_OPS.includes(c.op) && <input className="input" style={{ maxWidth: 200 }} placeholder={LIST_OR_PAIR_OPS.includes(c.op) ? "comma,separated" : "value"} value={c.value} onChange={(e) => updCond(i, { value: e.target.value })} />}
            <button className="icon-btn" onClick={() => rmCond(i)} title="Remove" aria-label="Remove">✕</button>
          </div>
        ))}
      </div>

      <div className="row row-wrap mt-16">
        <Field label="Sort by">
          <select className="select" value={sortCol} onChange={(e) => setSortCol(e.target.value)} style={{ minWidth: 160 }}>
            <option value="">— none —</option>
            {colNames.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        {sortCol && (
          <Field label="Direction">
            <select className="select" value={sortDir} onChange={(e) => setSortDir(e.target.value)}><option value="asc">asc</option><option value="desc">desc</option></select>
          </Field>
        )}
      </div>
    </Modal>
  );
}

function RunViewModal({ datasetId, view, onClose }: { datasetId: string; view: ViewOut; onClose: () => void }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ version_number: number; sheet_name: string; total: number | null; masked: string[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async (cur: string | null) => {
    setLoading(true); setError(null);
    try {
      const r = await api.post<ViewRunResponse>(`/datasets/${datasetId}/views/${view.id}/run`, { cursor: cur || undefined, limit: 100 });
      setRows((prev) => cur ? [...prev, ...r.result.items] : r.result.items);
      setCursor(r.result.next_cursor);
      setMeta({ version_number: r.version_number, sheet_name: r.sheet_name, total: r.result.total, masked: r.result.masked_columns });
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [datasetId, view.id]);

  useEffect(() => { load(null); }, [load]);

  const cols = rows.length ? Object.keys(rows[0]) : (view.query.columns || []);

  return (
    <Modal wide title={<h3>Run · {view.name}</h3>} onClose={onClose}>
      {error ? <ErrorBanner error={error} /> : !meta ? <Loading /> : (
        <>
          <div className="row small secondary" style={{ margin: "0 2px 10px", gap: 10 }}>
            <span>v{meta.version_number} · {meta.sheet_name}</span>
            <span><strong>{fmtNum(meta.total)}</strong> rows</span>
            {meta.masked.length > 0 && <Badge kind="warning">masked: {meta.masked.join(", ")}</Badge>}
          </div>
          {rows.length === 0 ? <EmptyState title="No rows matched this view." /> : (
            <div className="card" style={{ padding: 0 }}>
              <div className="table-wrap" style={{ maxHeight: 420 }}>
                <table className="data">
                  <thead><tr>{cols.map((c) => <th scope="col" key={c}>{c}</th>)}</tr></thead>
                  <tbody>{rows.map((row, i) => <tr key={i}>{cols.map((c) => <td key={c}><Cell value={row[c]} /></td>)}</tr>)}</tbody>
                </table>
              </div>
            </div>
          )}
          <div className="row mt-16">
            <span className="small muted">Showing {fmtNum(rows.length)}</span>
            <span className="spacer" />
            {cursor && <button className="btn btn-sm" disabled={loading} onClick={() => load(cursor)}>{loading ? "Loading…" : "Load more"}</button>}
          </div>
        </>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// SQL console
// ---------------------------------------------------------------------------
function SqlConsole({ dataset }: { dataset: DatasetTabProps["dataset"] }) {
  const { identity } = useIdentity();
  const toast = useToast();
  const v = dataset.current_version ?? null;
  const sheets = useAsync(
    () => v == null ? Promise.resolve<Page<Sheet>>({ items: [], total: 0, limit: 1, offset: 0 })
      : api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${v}/sheets`),
    [dataset.id, v, identity.userId, identity.teamId],
  );

  const [sql, setSql] = useState("");
  const [result, setResult] = useState<SqlResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);

  const tables = (sheets.data?.items || []).map((s) => s.sheet_key);
  const firstTable = tables[0] || "sheet_key";
  const examples = [
    `SELECT * FROM ${firstTable} LIMIT 100`,
    `SELECT COUNT(*) AS n FROM ${firstTable}`,
  ];

  const run = async () => {
    if (!sql.trim() || v == null) return;
    setRunning(true); setError(null); setResult(null);
    try {
      const r = await api.post<SqlResponse>(`/datasets/${dataset.id}/versions/${v}/sql`, { sql });
      setResult(r);
    } catch (e) {
      setError(e);
    } finally {
      setRunning(false);
    }
  };

  const downloadFile = async (filename: string) => {
    try {
      await api.download(`/samples/${encodeURIComponent(filename)}`, filename);
    } catch (e) {
      toast({ kind: "error", title: "Download failed", msg: e instanceof ApiError ? `${e.detail} (${e.code})` : String(e) });
    }
  };

  const exportCsv = async (filename: string) => {
    try {
      const r = await api.post<ExportResponse>(`/samples/${encodeURIComponent(filename)}/export`, undefined, { format: "csv" });
      await api.download(`/samples/${encodeURIComponent(r.export_file)}`, r.export_file);
      toast({ kind: "good", title: "Exported CSV", msg: r.export_file });
    } catch (e) {
      toast({ kind: "error", title: "Export failed", msg: e instanceof ApiError ? `${e.detail} (${e.code})` : String(e) });
    }
  };

  if (v == null) return <div className="banner info" style={{ marginTop: 16 }}>This dataset has no ready version to query.</div>;

  return (
    <div>
      <Card>
        <div className="row"><strong className="small">Read-only SQL over v{v}</strong><span className="spacer" />
          {sheets.loading ? <span className="muted small">loading tables…</span> : tables.length > 0 && <span className="small muted">tables: {tables.map((t) => <code key={t} style={{ marginLeft: 6 }}>{t}</code>)}</span>}
        </div>
        <textarea
          className="input mono mt-8"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          placeholder={`A single SELECT, e.g.\n${examples[0]}`}
          rows={5}
          style={{ width: "100%", resize: "vertical" }}
        />
        <div className="row row-wrap mt-8 gap-6">
          <button className="btn btn-primary btn-sm" onClick={run} disabled={running || !sql.trim()}>{running ? "Running…" : "Run query"}</button>
          <span className="spacer" />
          <span className="small muted">Examples:</span>
          {examples.map((ex) => <button key={ex} className="btn btn-ghost btn-sm" onClick={() => setSql(ex)}><code>{ex}</code></button>)}
        </div>
      </Card>

      <div className="mt-16">
        {error ? <ErrorBanner error={error} /> : running ? <Loading /> : result && (
          <>
            <div className="row small secondary" style={{ margin: "0 2px 10px", gap: 10 }}>
              <span><strong>{fmtNum(result.row_count)}</strong> rows</span>
              {result.truncated && <Badge kind="warning">row cap hit — truncated</Badge>}
              <span className="spacer" />
              {result.result_file && <>
                <button className="btn btn-sm" onClick={() => downloadFile(result.result_file)}>Download parquet</button>
                <button className="btn btn-sm" onClick={() => exportCsv(result.result_file)}>Export CSV</button>
              </>}
            </div>
            {result.items.length === 0 ? <EmptyState title="Query returned no rows." /> : (
              <div className="card" style={{ padding: 0 }}>
                <div className="table-wrap" style={{ maxHeight: 480 }}>
                  <table className="data">
                    <thead><tr>{result.columns.map((c) => <th scope="col" key={c}>{c}</th>)}</tr></thead>
                    <tbody>{result.items.map((row, i) => <tr key={i}>{result.columns.map((c) => <td key={c}><Cell value={row[c]} /></td>)}</tr>)}</tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lineage
// ---------------------------------------------------------------------------
function Lineage({ dataset }: { dataset: DatasetTabProps["dataset"] }) {
  const { identity } = useIdentity();
  const [maxDepth, setMaxDepth] = useState(5);
  const state = useAsync(
    () => api.get<LineageGraph>(`/datasets/${dataset.id}/lineage/graph`, { max_depth: maxDepth }),
    [dataset.id, maxDepth, identity.userId, identity.teamId],
  );

  return (
    <div>
      <div className="row row-wrap" style={{ marginBottom: 16 }}>
        <Field label="Max depth">
          <select className="select" value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))} style={{ minWidth: 90 }}>
            {[1, 2, 3, 5, 10, 25].map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </Field>
      </div>
      <AsyncView state={state}>
        {(g) => <LineageGraphView graph={g} rootId={dataset.id} />}
      </AsyncView>
    </div>
  );
}

function LineageGraphView({ graph, rootId }: { graph: LineageGraph; rootId: string }) {
  const nodeById = useMemo(() => {
    const m = new Map<string, LineageNode>();
    for (const n of graph.nodes) m.set(n.id, n);
    return m;
  }, [graph.nodes]);

  const root = nodeById.get(rootId) || graph.nodes.find((n) => n.is_root) || null;

  // edge: child_id was derived FROM parent_id (parent = upstream source).
  const upstreamOf = useCallback((id: string) => graph.edges.filter((e) => e.child_id === id), [graph.edges]);
  const downstreamOf = useCallback((id: string) => graph.edges.filter((e) => e.parent_id === id), [graph.edges]);

  const hasUpstream = graph.edges.some((e) => e.child_id === rootId);
  const hasDownstream = graph.edges.some((e) => e.parent_id === rootId);

  if (graph.edges.length === 0 && graph.hidden_nodes === 0) {
    return <EmptyState icon="⋔" title="No lineage recorded" hint="This dataset was uploaded directly — nothing was derived from it yet." />;
  }

  return (
    <div>
      {graph.truncated && <div className="banner info" style={{ marginBottom: 12 }}>The lineage continues past depth {graph.max_depth}. Raise the depth to see further.</div>}
      {graph.hidden_nodes > 0 && <div className="banner info" style={{ marginBottom: 12 }}><strong>{graph.hidden_nodes}</strong> dataset(s) in this lineage belong to teams you cannot read and are hidden.</div>}

      <div className="grid grid-2">
        <Card title="Upstream — where this came from">
          {!hasUpstream ? <span className="muted small">No parents — this is an original upload.</span> : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              <LineageTree id={rootId} nodeById={nodeById} rootId={rootId} step={upstreamOf} pick={(e) => e.parent_id} seen={new Set([rootId])} />
            </ul>
          )}
        </Card>
        <Card title="Downstream — what was derived from this">
          {!hasDownstream ? <span className="muted small">Nothing has been derived from this dataset.</span> : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              <LineageTree id={rootId} nodeById={nodeById} rootId={rootId} step={downstreamOf} pick={(e) => e.child_id} seen={new Set([rootId])} />
            </ul>
          )}
        </Card>
      </div>

      {root && (
        <div className="small muted mt-16">
          Root: <NodeLabel node={root} rootId={rootId} /> · {graph.nodes.length} visible node(s), {graph.edges.length} edge(s)
        </div>
      )}
    </div>
  );
}

// Recursive derivation tree from one node, following `step` edges and `pick`ing
// the far endpoint. `seen` guards against cycles in the DAG.
function LineageTree({ id, nodeById, rootId, step, pick, seen, edge }: {
  id: string; nodeById: Map<string, LineageNode>; rootId: string;
  step: (id: string) => LineageEdge[]; pick: (e: LineageEdge) => string;
  seen: Set<string>; edge?: LineageEdge;
}) {
  const node = nodeById.get(id);
  const children = step(id).filter((e) => !seen.has(pick(e)));

  return (
    <li style={{ marginTop: 6 }}>
      <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
        {edge && <Badge kind="accent">{RELATION_LABELS[edge.relation] || edge.relation}</Badge>}
        {node ? <NodeLabel node={node} rootId={rootId} /> : <span className="cell-masked">hidden dataset</span>}
        {edge && <span className="small muted">depth {edge.depth}</span>}
      </div>
      {children.length > 0 && (
        <ul style={{ listStyle: "none", margin: 0, paddingLeft: 18, borderLeft: "1px solid var(--border)" }}>
          {children.map((e) => {
            const next = pick(e);
            return <LineageTree key={`${e.parent_id}->${e.child_id}:${e.relation}`} id={next} nodeById={nodeById} rootId={rootId} step={step} pick={pick} seen={new Set([...seen, next])} edge={e} />;
          })}
        </ul>
      )}
    </li>
  );
}

function NodeLabel({ node, rootId }: { node: LineageNode; rootId: string }) {
  const label = (
    <>
      <span className="mono">{node.name}</span>
      {node.domain && <Badge kind="neutral">{node.domain}</Badge>}
      {node.deprecated && <Badge kind="warning">deprecated</Badge>}
      {node.id === rootId && <Badge kind="good">this dataset</Badge>}
    </>
  );
  if (node.id === rootId) return <span className="row" style={{ gap: 6, alignItems: "baseline", display: "inline-flex" }}>{label}</span>;
  return <Link to={`/datasets/${node.id}`} className="row" style={{ gap: 6, alignItems: "baseline", display: "inline-flex" }}>{label}</Link>;
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
function selectorLabel(sel: Record<string, unknown>): string {
  const mode = String(sel?.mode ?? "current");
  if (mode === "tag") return `tag:${sel.tag}`;
  if (mode === "version") return `v${sel.version_number}`;
  return "current";
}

function parseVal(op: string, raw: string): unknown {
  if (LIST_OR_PAIR_OPS.includes(op)) return raw.split(",").map((s) => coerce(s.trim()));
  return coerce(raw);
}
function coerce(s: string): unknown {
  if (s === "") return s;
  const n = Number(s);
  return Number.isNaN(n) ? s : n;
}
