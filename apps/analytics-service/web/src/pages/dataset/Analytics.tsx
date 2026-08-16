import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { Page } from "../../api/client";
import { AsyncView, Badge, Card, EmptyState, ErrorBanner, Field, Loading, Modal, StatTile, cx, fmtNum, useAsync, useToast } from "../../components/ui";
import { BarChart, LineChart } from "../../components/charts";
import type { Series } from "../../components/charts";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

/* ---------- shapes (see openapi.json: AggregateResponse, PivotResponse, DefinitionOut, RunResponse, ChartRenderResponse) ---------- */
type Row = Record<string, unknown>;
interface Column { name: string; normalized_name?: string; dtype?: string; }
interface Sheet { name: string; sheet_key: string; columns?: Column[]; is_default?: boolean; }
interface AggResponse { success: boolean; original_count: number; group_count: number; columns: string[]; data?: Row[] | null; totals?: Row | null; totals_omitted?: Row | null; truncated: boolean; result_file?: string | null; }
interface PivotResponse { success: boolean; original_count: number; row_count: number; columns: string[]; pivot_columns?: string[]; data?: Row[] | null; totals?: Row | null; column_totals?: Row | null; truncated: boolean; }
interface DefinitionOut { id: string; name: string; description?: string | null; kind: string; sheet?: string | null; params?: Row | null; created_at: string; updated_at: string; }
interface RunResponse { id: string; definition_id: string; status: string; result_summary?: Row | null; result?: Row | null; artifact_id?: string | null; started_at: string; completed_at?: string | null; error?: string | null; }
interface ChartOut { id: string; name: string; description?: string | null; chart_type: string; config?: Row; }
interface ChartRender { chart_id: string; chart_type: string; categories?: string[]; series?: Series[]; x_field?: string | null; y_fields?: string[]; masked_columns?: string[]; truncated: boolean; row_count: number; }

const AGG_FNS: [string, string][] = [["sum", "sum"], ["mean", "avg (mean)"], ["median", "median"], ["count", "count"], ["min", "min"], ["max", "max"], ["std", "std dev"], ["nunique", "distinct"]];
const DISPLAYS: [string, string][] = [["value", "value"], ["pct_of_row", "% of row"], ["pct_of_column", "% of column"], ["pct_of_grand_total", "% of grand total"]];

interface AggSpec { column: string; function: string; alias: string; }
interface ValSpec { column: string; function: string; display: string; alias: string; }

function numify(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isNaN(n) ? null : n;
}
function aggKey(a: { column: string; function: string; alias?: string }): string {
  return (a.alias && a.alias.trim()) || `${a.column}_${a.function}`;
}

export function Analytics({ dataset }: DatasetTabProps) {
  const { identity } = useIdentity();
  const idem = [dataset.id, identity.userId, identity.teamId];
  const v = dataset.current_version ?? null;

  const sheets = useAsync(
    () => v == null ? Promise.resolve<Page<Sheet>>({ items: [], total: 0, limit: 1, offset: 0 })
      : api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${v}/sheets`),
    [dataset.id, v, identity.userId, identity.teamId],
  );
  const defs = useAsync(() => api.get<Page<DefinitionOut>>(`/datasets/${dataset.id}/analytics`), idem);
  const charts = useAsync(() => api.get<Page<ChartOut>>(`/datasets/${dataset.id}/charts`), idem);

  const [tab, setTab] = useState<"build" | "saved" | "charts">("build");

  if (v == null) return <div className="banner info">This dataset has no current version to analyze yet.</div>;

  const subtabs: { id: typeof tab; label: string }[] = [
    { id: "build", label: "Builder" },
    { id: "saved", label: `Saved definitions${defs.data ? ` (${defs.data.total})` : ""}` },
    { id: "charts", label: `Charts${charts.data ? ` (${charts.data.total})` : ""}` },
  ];

  return (
    <div>
      <div className="tabs" role="tablist" style={{ marginBottom: 16 }}>
        {subtabs.map((s) => (
          <button key={s.id} role="tab" aria-selected={tab === s.id} className={cx("tab", tab === s.id && "active")} onClick={() => setTab(s.id)}>{s.label}</button>
        ))}
      </div>

      {sheets.loading ? <Loading /> : sheets.error ? <ErrorBanner error={sheets.error} /> : (
        <>
          {tab === "build" && <Builder datasetId={dataset.id} version={v} sheets={sheets.data?.items || []} onSaved={defs.reload} />}
          {tab === "saved" && <SavedDefs datasetId={dataset.id} state={defs} />}
          {tab === "charts" && <Charts datasetId={dataset.id} state={charts} idem={idem} definitions={defs.data?.items || []} />}
        </>
      )}
    </div>
  );
}

/* ======================= BUILDER (aggregate + pivot) ======================= */

function Builder({ datasetId, version, sheets, onSaved }: { datasetId: string; version: number; sheets: Sheet[]; onSaved: () => void }) {
  const [mode, setMode] = useState<"aggregate" | "pivot">("aggregate");
  const [sheet, setSheet] = useState<string>(sheets.find((s) => s.is_default)?.name ?? sheets[0]?.name ?? "");
  const active = sheets.find((s) => s.name === sheet) || sheets[0];
  const cols = (active?.columns || []).map((c) => c.normalized_name || c.name);
  const first = cols[0] || "";

  // aggregate config
  const [groupBy, setGroupBy] = useState<string[]>([first]);
  const [aggs, setAggs] = useState<AggSpec[]>([{ column: first, function: "count", alias: "" }]);
  const [aSort, setASort] = useState<string>("");
  const [aOrder, setAOrder] = useState<"asc" | "desc">("desc");
  const [aLimit, setALimit] = useState<string>("50");

  // pivot config
  const [pRows, setPRows] = useState<string[]>([first]);
  const [pCol, setPCol] = useState<string>(cols[1] || first);
  const [vals, setVals] = useState<ValSpec[]>([{ column: first, function: "sum", display: "value", alias: "" }]);
  const [rowTot, setRowTot] = useState(true);
  const [colTot, setColTot] = useState(true);

  const [running, setRunning] = useState(false);
  const [agg, setAgg] = useState<AggResponse | null>(null);
  const [pivot, setPivot] = useState<PivotResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const toast = useToast();

  const aggBody = useMemo(() => ({
    group_by: groupBy.filter(Boolean),
    aggregations: aggs.filter((a) => a.column).map((a) => ({ column: a.column, function: a.function, ...(a.alias.trim() ? { alias: a.alias.trim() } : {}) })),
    ...(aSort ? { sort_by: aSort, sort_order: aOrder } : {}),
    ...(aLimit ? { limit: Number(aLimit) } : {}),
  }), [groupBy, aggs, aSort, aOrder, aLimit]);

  const pivotBody = useMemo(() => ({
    rows: pRows.filter(Boolean),
    columns: pCol || null,
    values: vals.filter((v) => v.column).map((v) => ({ column: v.column, function: v.function, display: v.display, ...(v.alias.trim() ? { alias: v.alias.trim() } : {}) })),
    include_row_totals: rowTot,
    include_column_totals: colTot,
  }), [pRows, pCol, vals, rowTot, colTot]);

  async function run() {
    setRunning(true); setError(null); setAgg(null); setPivot(null);
    try {
      if (mode === "aggregate") {
        setAgg(await api.post<AggResponse>(`/aggregate`, { dataset_id: datasetId, version_number: version, sheet, ...aggBody }));
      } else {
        setPivot(await api.post<PivotResponse>(`/pivot`, { dataset_id: datasetId, version_number: version, sheet, ...pivotBody }));
      }
      toast({ kind: "good", title: "Query ran" });
    } catch (e) {
      setError(e);
      toast({ kind: "error", title: "Query failed", msg: e instanceof ApiError ? e.detail : String(e) });
    } finally { setRunning(false); }
  }

  if (!cols.length) return <EmptyState title="No columns" hint="This sheet has no readable columns." />;

  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(320px, 380px) 1fr", gap: 20, alignItems: "start" }}>
      <Card title="Query builder" actions={<button className="btn btn-sm" onClick={() => setSaveOpen(true)}>Save…</button>}>
        <div className="row" style={{ marginBottom: 12 }}>
          <button className={cx("btn btn-sm", mode === "aggregate" && "btn-primary")} onClick={() => setMode("aggregate")}>Aggregate</button>
          <button className={cx("btn btn-sm", mode === "pivot" && "btn-primary")} onClick={() => setMode("pivot")}>Pivot</button>
        </div>

        {sheets.length > 1 && (
          <Field label="Sheet">
            <select className="select" value={sheet} onChange={(e) => setSheet(e.target.value)}>{sheets.map((s) => <option key={s.sheet_key} value={s.name}>{s.name}</option>)}</select>
          </Field>
        )}

        {mode === "aggregate" ? (
          <>
            <ColList label="Group by" values={groupBy} cols={cols} onChange={setGroupBy} />
            <div className="field"><label>Aggregations</label>
              {aggs.map((a, i) => (
                <div className="row row-wrap mt-8" key={i}>
                  <select className="select" style={{ maxWidth: 140 }} value={a.function} onChange={(e) => setAggs(patch(aggs, i, { function: e.target.value }))}>{AGG_FNS.map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}</select>
                  <select className="select" style={{ maxWidth: 150 }} value={a.column} onChange={(e) => setAggs(patch(aggs, i, { column: e.target.value }))}>{cols.map((c) => <option key={c} value={c}>{c}</option>)}</select>
                  <input className="input" style={{ maxWidth: 110 }} placeholder="alias" value={a.alias} onChange={(e) => setAggs(patch(aggs, i, { alias: e.target.value }))} />
                  {aggs.length > 1 && <button className="icon-btn" title="Remove" aria-label="Remove" onClick={() => setAggs(aggs.filter((_, j) => j !== i))}>✕</button>}
                </div>
              ))}
              <div className="mt-8"><button className="btn btn-sm" onClick={() => setAggs([...aggs, { column: first, function: "sum", alias: "" }])}>+ Aggregation</button></div>
            </div>
            <div className="row row-wrap mt-8">
              <Field label="Sort by">
                <select className="select" style={{ maxWidth: 160 }} value={aSort} onChange={(e) => setASort(e.target.value)}>
                  <option value="">(none)</option>
                  {groupBy.filter(Boolean).map((c, gi) => <option key={`g${gi}-${c}`} value={c}>{c}</option>)}
                  {aggs.filter((a) => a.column).map((a, ai) => { const k = aggKey(a); return <option key={`a${ai}-${k}`} value={k}>{k}</option>; })}
                </select>
              </Field>
              <Field label="Order"><select className="select" style={{ maxWidth: 90 }} value={aOrder} onChange={(e) => setAOrder(e.target.value as "asc" | "desc")}><option value="desc">desc</option><option value="asc">asc</option></select></Field>
              <Field label="Limit"><input className="input" style={{ maxWidth: 80 }} type="number" value={aLimit} onChange={(e) => setALimit(e.target.value)} /></Field>
            </div>
          </>
        ) : (
          <>
            <ColList label="Rows" values={pRows} cols={cols} onChange={setPRows} />
            <Field label="Column dimension"><select className="select" value={pCol} onChange={(e) => setPCol(e.target.value)}>{cols.map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
            <div className="field"><label>Values</label>
              {vals.map((val, i) => (
                <div className="row row-wrap mt-8" key={i}>
                  <select className="select" style={{ maxWidth: 120 }} value={val.function} onChange={(e) => setVals(patch(vals, i, { function: e.target.value }))}>{AGG_FNS.map(([v, lbl]) => <option key={v} value={v}>{lbl}</option>)}</select>
                  <select className="select" style={{ maxWidth: 130 }} value={val.column} onChange={(e) => setVals(patch(vals, i, { column: e.target.value }))}>{cols.map((c) => <option key={c} value={c}>{c}</option>)}</select>
                  <select className="select" style={{ maxWidth: 150 }} value={val.display} onChange={(e) => setVals(patch(vals, i, { display: e.target.value }))}>{DISPLAYS.map(([v, lbl]) => <option key={v} value={v}>{lbl}</option>)}</select>
                  {vals.length > 1 && <button className="icon-btn" title="Remove" aria-label="Remove" onClick={() => setVals(vals.filter((_, j) => j !== i))}>✕</button>}
                </div>
              ))}
              <div className="mt-8"><button className="btn btn-sm" onClick={() => setVals([...vals, { column: first, function: "sum", display: "value", alias: "" }])}>+ Value</button></div>
            </div>
            <div className="row row-wrap mt-8">
              <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={rowTot} onChange={(e) => setRowTot(e.target.checked)} /> Row totals</label>
              <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={colTot} onChange={(e) => setColTot(e.target.checked)} /> Column totals</label>
            </div>
          </>
        )}

        <div className="mt-16"><button className="btn btn-primary" disabled={running} onClick={run}>{running ? "Running…" : "Run query"}</button></div>
      </Card>

      <div>
        {running ? <Loading /> : error ? <ErrorBanner error={error} /> :
          mode === "aggregate" && agg ? <AggregateResult res={agg} groupBy={groupBy.filter(Boolean)} aggs={aggs.filter((a) => a.column)} /> :
          mode === "pivot" && pivot ? <PivotResult res={pivot} rows={pRows.filter(Boolean)} /> :
          <EmptyState icon="∑" title="Build a query" hint="Configure the query on the left, then Run to see a chart and results." />}
      </div>

      {saveOpen && (
        <SaveModal datasetId={datasetId} sheet={sheet} kind={mode}
          params={mode === "aggregate" ? aggBody : pivotBody}
          onClose={() => setSaveOpen(false)} onSaved={() => { setSaveOpen(false); onSaved(); }} />
      )}
    </div>
  );
}

function ColList({ label, values, cols, onChange }: { label: string; values: string[]; cols: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="field"><label>{label}</label>
      {values.map((v, i) => (
        <div className="row mt-8" key={i}>
          <select className="select" value={v} onChange={(e) => onChange(patch(values, i, e.target.value))}>{cols.map((c) => <option key={c} value={c}>{c}</option>)}</select>
          {values.length > 1 && <button className="icon-btn" title="Remove" aria-label="Remove" onClick={() => onChange(values.filter((_, j) => j !== i))}>✕</button>}
        </div>
      ))}
      <div className="mt-8"><button className="btn btn-sm" onClick={() => onChange([...values, cols[0] || ""])}>+ {label}</button></div>
    </div>
  );
}
function patch<T>(arr: T[], i: number, v: Partial<T> | T): T[] {
  return arr.map((x, j) => j === i ? (typeof v === "object" && v !== null && !Array.isArray(v) ? { ...x, ...(v as object) } : (v as T)) : x);
}

/* ---------- aggregate result: BarChart + stats + totals ---------- */
function AggregateResult({ res, groupBy, aggs }: { res: AggResponse; groupBy: string[]; aggs: AggSpec[] }) {
  const data = res.data || [];
  // Series come from the columns the SERVER returned, minus the group keys —
  // deriving them client-side from `alias || column_function` mis-plotted any
  // result the server renamed (a duplicate alias charted one measure twice).
  const measureCols = (res.columns || []).filter((c) => !groupBy.includes(c));
  const keys = measureCols.length ? measureCols : aggs.map((a) => aggKey(a));
  const categories = data.map((r) => groupBy.map((g) => String(r[g] ?? "—")).join(" / ") || "—");
  const series: Series[] = keys.map((k) => ({ name: k, data: data.map((r) => numify(r[k])) }));

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="grid grid-3">
        <StatTile label="Groups" value={fmtNum(res.group_count)} />
        <StatTile label="Rows scanned" value={fmtNum(res.original_count)} />
        <StatTile label="Result" value={res.truncated ? <Badge kind="warning">truncated</Badge> : <Badge kind="good">complete</Badge>} />
      </div>

      <Card title="Chart">
        {data.length ? <BarChart categories={categories} series={series} /> : <EmptyState title="No rows returned" />}
      </Card>

      {/* Rendered when there are totals OR omissions: when EVERY measure is
          non-additive the API returns totals:null, and nesting the notice
          inside the totals check meant the user got no total and no reason. */}
      {((res.totals && Object.keys(res.totals).length > 0)
        || (res.totals_omitted && Object.keys(res.totals_omitted).length > 0)) && (
        <Card title="Grand totals">
          {res.totals && Object.keys(res.totals).length > 0 ? (
            <dl className="kv">
              {Object.entries(res.totals).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}><dt className="mono">{k}</dt><dd>{fmtNum(v)}</dd></div>
              ))}
            </dl>
          ) : (
            <div className="small secondary">No grand total — every measure here is non-additive.</div>
          )}
          {res.totals_omitted && Object.keys(res.totals_omitted).length > 0 && (
            <div className="small muted mt-8">Omitted (non-additive): {Object.keys(res.totals_omitted).join(", ")}</div>
          )}
        </Card>
      )}

      {data.length > 0 && <DataTable columns={res.columns} data={data} title="Result rows" />}
    </div>
  );
}

/* ---------- pivot result: matrix table + totals + optional grouped chart ---------- */
function PivotResult({ res, rows }: { res: PivotResponse; rows: string[] }) {
  const data = res.data || [];
  const cols = res.columns || [];
  const cellCols = cols.filter((c) => !rows.includes(c));
  // The row-total column is an aggregate OF the other cells; charting it as a
  // peer series double-counts and dominates the axis.
  const isRowTotal = (c: string) => c.startsWith("total_");
  const chartCols = cellCols.filter((c) => !isRowTotal(c));
  const [chart, setChart] = useState(false);

  const categories = data.map((r) => rows.map((rc) => String(r[rc] ?? "—")).join(" / ") || "—");
  const series: Series[] = chartCols.map((c) => ({ name: c, data: data.map((r) => numify(r[c])) }));

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="grid grid-3">
        <StatTile label="Rows" value={fmtNum(res.row_count)} />
        <StatTile label="Columns" value={fmtNum(cellCols.length)} />
        <StatTile label="Result" value={res.truncated ? <Badge kind="warning">truncated</Badge> : <Badge kind="good">complete</Badge>} />
      </div>

      <Card title="Pivot matrix" actions={<button className="btn btn-ghost btn-sm" onClick={() => setChart((c) => !c)}>{chart ? "Show table" : "Show chart"}</button>}>
        {!data.length ? <EmptyState title="No rows returned" /> : chart ? (
          <BarChart categories={categories} series={series} />
        ) : (
          <div className="table-wrap" style={{ maxHeight: 520 }}>
            <table className="data">
              <thead><tr>{cols.map((c) => <th scope="col" key={c} className={rows.includes(c) ? undefined : "num"}>{c}</th>)}</tr></thead>
              <tbody>
                {data.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className={rows.includes(c) ? undefined : "num"}>{rows.includes(c) ? String(r[c] ?? "—") : fmtNum(r[c])}</td>)}</tr>)}
                {res.column_totals && Object.keys(res.column_totals).length > 0 && (
                  <tr style={{ fontWeight: 600, borderTop: "2px solid var(--baseline)" }}>
                    {cols.map((c, i) => {
                      if (i === 0) return <td key={c}>Total</td>;
                      if (rows.includes(c)) return <td key={c} />;
                      // `column_totals` has no key for the row-total column; the
                      // grand total (which belongs in that corner cell) lives in
                      // `totals`, keyed by the measure alias.
                      const val = isRowTotal(c)
                        ? (res.totals as Row | undefined)?.[c.replace(/^total_/, "")]
                        : (res.column_totals as Row)[c];
                      return <td key={c} className="num">{fmtNum(val)}</td>;
                    })}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {res.totals && Object.keys(res.totals).length > 0 && (
        <Card title="Grand totals">
          <dl className="kv">{Object.entries(res.totals).map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt className="mono">{k}</dt><dd>{fmtNum(v)}</dd></div>)}</dl>
        </Card>
      )}
    </div>
  );
}

function DataTable({ columns, data, title }: { columns: string[]; data: Row[]; title: string }) {
  const cols = columns.length ? columns : (data[0] ? Object.keys(data[0]) : []);
  return (
    <Card title={title}>
      <div className="table-wrap" style={{ maxHeight: 460 }}>
        <table className="data">
          <thead><tr>{cols.map((c) => <th scope="col" key={c}>{c}</th>)}</tr></thead>
          <tbody>{data.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{typeof r[c] === "number" ? fmtNum(r[c]) : String(r[c] ?? "—")}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </Card>
  );
}

/* ======================= SAVE DEFINITION MODAL ======================= */
function SaveModal({ datasetId, sheet, kind, params, onClose, onSaved }: { datasetId: string; sheet: string; kind: "aggregate" | "pivot"; params: Row; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const toast = useToast();

  async function submit() {
    if (!name.trim()) { setErr(new Error("Name is required")); return; }
    setBusy(true); setErr(null);
    try {
      await api.post(`/datasets/${datasetId}/analytics`, {
        name: name.trim(), description: desc.trim() || null, kind,
        version_selector: { mode: "current" }, sheet, params,
      });
      toast({ kind: "good", title: "Definition saved", msg: name.trim() });
      onSaved();
    } catch (e) { setErr(e); setBusy(false); }
  }

  return (
    <Modal title={`Save ${kind} definition`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy} onClick={submit}>{busy ? "Saving…" : "Save"}</button></>}>
      {err ? <ErrorBanner error={err} /> : null}
      <Field label="Name"><input className="input" value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="e.g. Revenue by region" /></Field>
      <Field label="Description"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="optional" /></Field>
      <div className="small muted mt-8">Bound to the current version; parameters captured from the builder.</div>
    </Modal>
  );
}

/* ======================= SAVED DEFINITIONS ======================= */
function SavedDefs({ datasetId, state }: { datasetId: string; state: ReturnType<typeof useAsync<Page<DefinitionOut>>> }) {
  const [published, setPublished] = useState<{ id: string; name: string } | null>(null);
  return (
    <div>
      {published && (
        <div className="banner info" style={{ marginBottom: 16 }}>
          <span>✓ Published as <strong>{published.name}</strong>.</span>
          <Link className="btn btn-sm" style={{ marginLeft: 12 }} to={`/datasets/${published.id}`}>Open dataset →</Link>
        </div>
      )}
      <AsyncView state={state}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="🖉" title="No saved definitions" hint="Build a query and use Save… to store it here." />
        ) : (
          <div className="grid grid-2">
            {page.items.map((d) => <DefinitionCard key={d.id} datasetId={datasetId} def={d} onDeleted={state.reload} onPublished={setPublished} />)}
          </div>
        )}
      </AsyncView>
    </div>
  );
}

function DefinitionCard({ datasetId, def, onDeleted, onPublished }: { datasetId: string; def: DefinitionOut; onDeleted: () => void; onPublished: (p: { id: string; name: string }) => void }) {
  const [run, setRun] = useState<RunResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [publishFor, setPublishFor] = useState<string | null>(null);
  const toast = useToast();

  async function doRun() {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<RunResponse>(`/datasets/${datasetId}/analytics/${def.id}/run`);
      setRun(r);
      toast({ kind: r.status === "failed" ? "error" : "good", title: `Run ${r.status}`, msg: r.error || undefined });
    } catch (e) { setErr(e); toast({ kind: "error", title: "Run failed", msg: e instanceof ApiError ? e.detail : String(e) }); }
    finally { setBusy(false); }
  }
  async function doDelete() {
    if (!confirm(`Delete definition "${def.name}"?`)) return;
    try { await api.del(`/datasets/${datasetId}/analytics/${def.id}`); toast({ kind: "good", title: "Deleted" }); onDeleted(); }
    catch (e) { toast({ kind: "error", title: "Delete failed", msg: e instanceof ApiError ? e.detail : String(e) }); }
  }

  const summary = run?.result_summary || run?.result || null;
  return (
    <Card title={<div><h3>{def.name}</h3><div className="row row-wrap mt-8"><Badge kind="accent">{def.kind}</Badge>{def.sheet && <span className="small muted mono">{def.sheet}</span>}</div></div>}
      actions={<><button className="btn btn-sm btn-primary" disabled={busy} onClick={doRun}>{busy ? "Running…" : "Run"}</button><button className="btn btn-sm btn-danger" onClick={doDelete}>Delete</button></>}>
      {def.description && <div className="secondary small">{def.description}</div>}
      {err ? <div className="mt-8"><ErrorBanner error={err} /></div> : null}
      {run && (
        <div className="mt-16">
          <div className="row"><Badge kind={run.status === "failed" ? "critical" : run.status === "completed" ? "good" : "warning"}>{run.status}</Badge>{run.artifact_id && <span className="small muted">artifact ready</span>}</div>
          {summary && (
            <dl className="kv mt-8">
              {Object.entries(summary).slice(0, 8).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}><dt>{k.replace(/_/g, " ")}</dt><dd>{typeof v === "number" ? fmtNum(v) : typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>
              ))}
            </dl>
          )}
          {run.status !== "failed" && <div className="mt-8"><button className="btn btn-sm" onClick={() => setPublishFor(run.id)}>Publish result…</button></div>}
        </div>
      )}
      {publishFor && <PublishModal datasetId={datasetId} runId={publishFor} defaultName={`${def.name} result`} onClose={() => setPublishFor(null)} onDone={(p) => { setPublishFor(null); onPublished(p); }} />}
    </Card>
  );
}

function PublishModal({ datasetId, runId, defaultName, onClose, onDone }: { datasetId: string; runId: string; defaultName: string; onClose: () => void; onDone: (p: { id: string; name: string }) => void }) {
  const [mode, setMode] = useState<"new_dataset" | "new_version">("new_dataset");
  const [name, setName] = useState(defaultName);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const toast = useToast();

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<{ dataset_id: string; dataset_name: string; version_number: number }>(
        `/datasets/${datasetId}/analytics/runs/${runId}/publish`, { mode, name: name.trim() || null });
      toast({ kind: "good", title: "Published", msg: `${r.dataset_name} · v${r.version_number}` });
      onDone({ id: r.dataset_id, name: r.dataset_name });
    } catch (e) { setErr(e); setBusy(false); }
  }
  return (
    <Modal title="Publish run" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy} onClick={submit}>{busy ? "Publishing…" : "Publish"}</button></>}>
      {err ? <ErrorBanner error={err} /> : null}
      <Field label="Mode">
        <select className="select" value={mode} onChange={(e) => setMode(e.target.value as "new_dataset" | "new_version")}>
          <option value="new_dataset">New dataset</option>
          <option value="new_version">New version of this dataset</option>
        </select>
      </Field>
      <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={mode === "new_dataset" ? "new dataset name" : "optional label"} /></Field>
    </Modal>
  );
}

/* ======================= CHARTS ======================= */
function Charts({ datasetId, state, idem, definitions }: {
  datasetId: string; state: ReturnType<typeof useAsync<Page<ChartOut>>>; idem: unknown[]; definitions: DefinitionOut[];
}) {
  const [selected, setSelected] = useState<ChartOut | null>(null);
  const [creating, setCreating] = useState(false);
  const toast = useToast();

  const remove = async (c: ChartOut) => {
    if (!window.confirm(`Delete chart "${c.name}"?`)) return;
    try {
      await api.del(`/datasets/${datasetId}/charts/${c.id}`);
      toast({ kind: "good", title: "Chart deleted", msg: c.name });
      if (selected?.id === c.id) setSelected(null);
      state.reload();
    } catch (e) {
      toast({ kind: "error", title: "Delete failed", msg: e instanceof ApiError ? e.detail : String(e) });
    }
  };

  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(240px, 300px) 1fr", gap: 20, alignItems: "start" }}>
      <Card title="Saved charts" actions={
        <button className="btn btn-primary btn-sm" disabled={!definitions.length} onClick={() => setCreating(true)}>+ New chart</button>
      }>
        <AsyncView state={state}>
          {(page) => page.items.length === 0 ? (
            <EmptyState icon="📊" title="No charts"
              hint={definitions.length ? "Create one from a saved definition." : "Save an analytics definition first, then chart it."} />
          ) : (
            <div className="grid" style={{ gap: 8 }}>
              {page.items.map((c) => (
                <div key={c.id} className="row gap-6">
                  <button className={cx("btn", selected?.id === c.id ? "btn-primary" : "btn-ghost")} style={{ flex: 1, justifyContent: "flex-start", textAlign: "left" }} onClick={() => setSelected(c)}>
                    <span style={{ flex: 1 }}>{c.name}</span><Badge kind="neutral">{c.chart_type}</Badge>
                  </button>
                  <button className="icon-btn" title="Delete chart" aria-label="Delete chart" onClick={() => remove(c)}>✕</button>
                </div>
              ))}
            </div>
          )}
        </AsyncView>
      </Card>
      {creating && (
        <NewChartModal datasetId={datasetId} definitions={definitions}
          onClose={() => setCreating(false)}
          onCreated={(c) => { setCreating(false); state.reload(); setSelected(c); }} />
      )}
      <div>{selected ? <ChartView datasetId={datasetId} chart={selected} idem={idem} /> : <EmptyState icon="📈" title="Select a chart" hint="Pick a saved chart to render it." />}</div>
    </div>
  );
}

const CHART_TYPES = ["bar", "line", "area", "pie"];

/** Create a chart over a saved definition. The definition supplies the data;
 *  the chart stores only its type plus the field encoding. */
function NewChartModal({ datasetId, definitions, onClose, onCreated }: {
  datasetId: string; definitions: DefinitionOut[];
  onClose: () => void; onCreated: (c: ChartOut) => void;
}) {
  const [name, setName] = useState("");
  const [chartType, setChartType] = useState("bar");
  const [definitionId, setDefinitionId] = useState(definitions[0]?.id || "");
  const [xField, setXField] = useState("");
  const [yFields, setYFields] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const create = async () => {
    setBusy(true); setError(null);
    try {
      const config: Row = {};
      if (xField.trim()) config.x_field = xField.trim();
      if (yFields.trim()) config.y_fields = yFields.split(",").map((s) => s.trim()).filter(Boolean);
      const c = await api.post<ChartOut>(`/datasets/${datasetId}/charts`, {
        name: name.trim(), chart_type: chartType, definition_id: definitionId, config,
      });
      onCreated(c);
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  return (
    <Modal title="New chart" onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !name.trim() || !definitionId} onClick={create}>Create chart</button>
      </>}>
      <div className="col">
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Revenue by region" /></Field>
        <Field label="Source definition">
          <select className="select" value={definitionId} onChange={(e) => setDefinitionId(e.target.value)}>
            {definitions.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.kind})</option>)}
          </select>
        </Field>
        <Field label="Chart type">
          <select className="select" value={chartType} onChange={(e) => setChartType(e.target.value)}>
            {CHART_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="X field (optional — inferred when blank)">
          <input className="input" value={xField} onChange={(e) => setXField(e.target.value)} placeholder="region" />
        </Field>
        <Field label="Y fields (optional, comma-separated)">
          <input className="input" value={yFields} onChange={(e) => setYFields(e.target.value)} placeholder="amount_sum" />
        </Field>
        {error != null && <ErrorBanner error={error} />}
      </div>
    </Modal>
  );
}

function ChartView({ datasetId, chart, idem }: { datasetId: string; chart: ChartOut; idem: unknown[] }) {
  const state = useAsync(() => api.post<ChartRender>(`/datasets/${datasetId}/charts/${chart.id}/render`, {}), [datasetId, chart.id, ...idem]);
  return (
    <Card title={<div><h3>{chart.name}</h3>{chart.description && <div className="secondary small mt-8">{chart.description}</div>}</div>}>
      <AsyncView state={state}>
        {(r) => {
          const categories = r.categories || [];
          const series = (r.series || []).map((s) => ({ name: s.name, data: (s.data || []).map(numify) }));
          const isLine = r.chart_type === "line" || r.chart_type === "area";
          return (
            <div className="grid" style={{ gap: 12 }}>
              <div className="row row-wrap">
                <Badge kind="neutral">{r.chart_type}</Badge>
                {r.x_field && <span className="small muted">x: <span className="mono">{r.x_field}</span></span>}
                {r.truncated && <Badge kind="warning">truncated</Badge>}
                {r.masked_columns && r.masked_columns.length > 0 && <Badge kind="warning">masked: {r.masked_columns.join(", ")}</Badge>}
              </div>
              {categories.length && series.length ? (
                isLine ? <LineChart categories={categories} series={series} /> : <BarChart categories={categories} series={series} />
              ) : <EmptyState title="No data to chart" />}
            </div>
          );
        }}
      </AsyncView>
    </Card>
  );
}
