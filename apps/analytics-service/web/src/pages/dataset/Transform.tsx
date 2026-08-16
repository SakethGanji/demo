import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Page } from "../../api/client";
import {
  AsyncView, Badge, Card, Cell, ErrorBanner, Field, Loading, Modal,
  cx, fmtDate, fmtNum, statusKind, useAsync, useToast,
} from "../../components/ui";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

/* ---------------- shapes ---------------- */
interface Column { name: string; normalized_name?: string; dtype?: string; }
interface Sheet { name: string; sheet_key: string; columns?: Column[]; is_default?: boolean; }
interface OutputColumn { name: string; normalized_name: string; dtype: string; position: number; }
interface CompileResult {
  output_schema: OutputColumn[];
  step_schemas: OutputColumn[][];
  columns: string[];
  rows: Record<string, unknown>[];
  sampled: boolean;
  version_number: number;
  sheet_name: string;
}
interface TransformationOut {
  id: string; dataset_id: string; name: string; description?: string | null;
  sheet_key?: string | null; steps: Record<string, unknown>[]; created_at: string; updated_at: string;
}
interface RunOut {
  id: string; definition_id: string; status: string; mode: string; error?: string | null;
  result_summary?: Record<string, unknown> | null; started_at: string; completed_at?: string | null;
  published_version_id?: string | null; published_dataset_id?: string | null; published_version_number?: number | null;
}
interface PublishResponse { dataset_id: string; dataset_name: string; version_id: string; version_number: number; mode: string; }

/* ---------------- step model (UI shape carries a _id for keys) ---------------- */
type OperandUI = { op: "col"; name: string } | { op: "lit"; vt: "num" | "text"; raw: string };
type ExprUI =
  | { k: "col"; name: string }
  | { k: "arith"; fn: string; left: OperandUI; right: OperandUI }
  | { k: "concat"; parts: OperandUI[]; separator: string }
  | { k: "round"; value: OperandUI; digits: number }
  | { k: "cast"; value: OperandUI; to: string };
interface Cond { column: string; op: string; value: string; }
interface SortTerm { column: string; direction: "asc" | "desc"; }
interface UStep { _id: number; type: string; [k: string]: unknown; }

const STEP_TYPES: { type: string; label: string }[] = [
  { type: "case_normalize", label: "Change case" },
  { type: "trim", label: "Trim whitespace" },
  { type: "replace", label: "Find & replace" },
  { type: "drop", label: "Drop columns" },
  { type: "filter", label: "Filter rows" },
  { type: "sort", label: "Sort rows" },
  { type: "deduplicate", label: "Deduplicate" },
  { type: "compute", label: "Computed column" },
];
const FILTER_OPS = ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "icontains", "starts_with", "ends_with", "in", "between", "is_null", "is_not_null"];
const NO_VALUE_OPS = ["is_null", "is_not_null"];
const ARITH_FNS = ["add", "sub", "mul", "div", "mod"];
const CAST_TARGETS = ["varchar", "integer", "bigint", "double", "decimal", "boolean", "date", "timestamp", "time"];
const EXPR_KINDS: { k: ExprUI["k"]; label: string }[] = [
  { k: "col", label: "copy column" }, { k: "arith", label: "arithmetic" },
  { k: "concat", label: "concatenate" }, { k: "round", label: "round" }, { k: "cast", label: "cast type" },
];

let _seq = 1;
function defaultStep(type: string, cols: string[]): UStep {
  const c0 = cols[0] || "";
  const base = { _id: _seq++, type };
  switch (type) {
    case "case_normalize": return { ...base, columns: [] as string[], mode: "lower" };
    case "trim": return { ...base, columns: [] as string[], mode: "both" };
    case "replace": return { ...base, column: c0, mode: "substring", find: "", replace_with: "", nulls_to: "" };
    case "drop": return { ...base, columns: [] as string[] };
    case "filter": return { ...base, logic: "and", conditions: [{ column: c0, op: "eq", value: "" }] as Cond[] };
    case "sort": return { ...base, by: [{ column: c0, direction: "asc" }] as SortTerm[] };
    case "deduplicate": return { ...base, subset: [] as string[], keep: "first", order_by: [] as SortTerm[] };
    case "compute": return { ...base, into: "", expr: { k: "col", name: c0 } as ExprUI };
    default: return base;
  }
}

/* ---------------- value coercion ---------------- */
function coerce(s: string): unknown { if (s === "") return s; const n = Number(s); return Number.isNaN(n) ? s : n; }
function parseVal(op: string, raw: string): unknown {
  if (NO_VALUE_OPS.includes(op)) return null;
  if (op === "in") return raw.split(",").map((x) => coerce(x.trim()));
  if (op === "between") return raw.split(",").map((x) => coerce(x.trim()));
  return coerce(raw);
}
function cleanOperand(o: OperandUI): unknown {
  return o.op === "col" ? { op: "col", name: o.name } : { op: "lit", value: o.vt === "num" ? Number(o.raw) : o.raw };
}
function cleanExpr(e: ExprUI): unknown {
  switch (e.k) {
    case "col": return { op: "col", name: e.name };
    case "arith": return { op: "arith", fn: e.fn, left: cleanOperand(e.left), right: cleanOperand(e.right) };
    case "concat": return { op: "concat", parts: e.parts.map(cleanOperand), separator: e.separator };
    case "round": return { op: "round", value: cleanOperand(e.value), digits: e.digits };
    case "cast": return { op: "cast", value: cleanOperand(e.value), to: e.to };
  }
}
function cleanStep(s: UStep): Record<string, unknown> {
  switch (s.type) {
    case "case_normalize": return { type: "case_normalize", columns: s.columns, mode: s.mode };
    case "trim": return { type: "trim", columns: s.columns, mode: s.mode };
    case "replace": {
      const find = (s.find as string) !== "" ? s.find : null;
      const nulls = (s.nulls_to as string) !== "" ? s.nulls_to : null;
      return { type: "replace", column: s.column, mode: s.mode, find, replace_with: s.replace_with ?? "", nulls_to: nulls };
    }
    case "drop": return { type: "drop", columns: s.columns };
    case "filter": return {
      type: "filter",
      where: { logic: s.logic, conditions: (s.conditions as Cond[]).map((c) => ({ column: c.column, op: c.op, value: parseVal(c.op, c.value) })) },
    };
    case "sort": return { type: "sort", by: s.by };
    case "deduplicate": return {
      type: "deduplicate",
      subset: (s.subset as string[]).length ? s.subset : null,
      keep: s.keep, order_by: s.order_by,
    };
    case "compute": return { type: "compute", into: s.into, expression: cleanExpr(s.expr as ExprUI) };
    default: return { type: s.type };
  }
}

/* ---------------- readiness (client-side; keeps compile payloads well-formed) ---------------- */
function operandReady(o: OperandUI): boolean {
  return o.op === "col" ? !!o.name : o.op === "lit" && o.raw !== "" && (o.vt !== "num" || !Number.isNaN(Number(o.raw)));
}
function exprReady(e: ExprUI): boolean {
  switch (e.k) {
    case "col": return !!e.name;
    case "arith": return operandReady(e.left) && operandReady(e.right);
    case "concat": return e.parts.length >= 2 && e.parts.every(operandReady);
    case "round": return operandReady(e.value);
    case "cast": return operandReady(e.value);
  }
}
function stepReady(s: UStep): boolean {
  switch (s.type) {
    case "case_normalize": case "trim": case "drop": return (s.columns as string[]).length >= 1;
    case "replace": return !!s.column && ((s.find as string) !== "" || (s.nulls_to as string) !== "");
    case "filter": {
      const cs = s.conditions as Cond[];
      return cs.length >= 1 && cs.every((c) => c.column && c.op && (NO_VALUE_OPS.includes(c.op) || c.value !== ""));
    }
    case "sort": return (s.by as SortTerm[]).length >= 1 && (s.by as SortTerm[]).every((t) => !!t.column);
    case "deduplicate": return true;
    case "compute": return !!s.into && exprReady(s.expr as ExprUI);
    default: return true;
  }
}

/* best-effort local fold so later steps' pickers see computed/dropped columns */
function colsBeforeStep(base: string[], steps: UStep[], i: number): string[] {
  let cols = [...base];
  for (let j = 0; j < i; j++) {
    const s = steps[j];
    if (s.type === "drop") cols = cols.filter((c) => !(s.columns as string[]).includes(c));
    else if (s.type === "compute" && s.into && !cols.includes(s.into as string)) cols = [...cols, s.into as string];
  }
  return cols;
}

/* ======================================================================= */
export function Transform({ dataset, reload }: DatasetTabProps) {
  const { identity } = useIdentity();
  const idem = [dataset.id, identity.userId, identity.teamId];
  const v = dataset.current_version ?? null;

  const sheets = useAsync(
    () => v == null
      ? Promise.resolve<Page<Sheet>>({ items: [], total: 0, limit: 1, offset: 0 })
      : api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${v}/sheets`),
    [dataset.id, v, identity.userId, identity.teamId],
  );
  const [sheet, setSheet] = useState<string | null>(null);
  const sheetName = sheet ?? sheets.data?.items.find((s) => s.is_default)?.name ?? sheets.data?.items[0]?.name ?? null;
  const activeSheet = sheets.data?.items.find((s) => s.name === sheetName) || null;
  const baseCols = (activeSheet?.columns || []).map((c) => c.normalized_name || c.name);

  if (v == null) return <div className="banner info">This dataset has no ready version to transform yet.</div>;
  if (sheets.loading) return <Loading />;
  if (sheets.error) return <ErrorBanner error={sheets.error} />;
  if (!sheetName) return <div className="banner info">This version has no sheets to transform.</div>;

  return (
    <div>
      <div className="row row-wrap" style={{ marginBottom: 16 }}>
        <span className="small muted">Building over <strong>v{v}</strong></span>
        {(sheets.data?.items.length || 0) > 1 && (
          <Field label="Sheet">
            <select className="select" value={sheetName} onChange={(e) => setSheet(e.target.value)} style={{ minWidth: 160 }}>
              {sheets.data!.items.map((s) => <option key={s.sheet_key} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
        )}
      </div>

      <Builder datasetId={dataset.id} sheetName={sheetName} baseCols={baseCols} idem={idem} />

      <h2 style={{ margin: "28px 0 12px" }}>Saved pipelines</h2>
      <SavedPipelines datasetId={dataset.id} idem={idem} onReloadDataset={reload} />
    </div>
  );
}

/* ---------------- pipeline builder ---------------- */
function Builder({ datasetId, sheetName, baseCols, idem }: { datasetId: string; sheetName: string; baseCols: string[]; idem: unknown[] }) {
  const toast = useToast();
  const [steps, setSteps] = useState<UStep[]>([]);
  const [addType, setAddType] = useState(STEP_TYPES[0].type);
  const [preview, setPreview] = useState<CompileResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);

  const allReady = steps.every(stepReady);
  const cleaned = useMemo(() => steps.map(cleanStep), [steps]);

  const compile = useAsync<CompileResult | null>(
    () => (!allReady)
      ? Promise.resolve(null)
      : api.post<CompileResult>(`/datasets/${datasetId}/transformations/compile`, { sheet: sheetName, steps: cleaned }),
    [datasetId, sheetName, JSON.stringify(cleaned), allReady, ...idem],
  );

  const update = (id: number, patch: Record<string, unknown>) =>
    setSteps((cur) => cur.map((s) => s._id === id ? { ...s, ...patch } : s));
  const remove = (id: number) => setSteps((cur) => cur.filter((s) => s._id !== id));
  const move = (i: number, dir: -1 | 1) => setSteps((cur) => {
    const next = [...cur]; const j = i + dir;
    if (j < 0 || j >= next.length) return cur;
    [next[i], next[j]] = [next[j], next[i]]; return next;
  });
  const add = () => setSteps((cur) => [...cur, defaultStep(addType, baseCols)]);

  const runPreview = async () => {
    setPreviewing(true);
    try {
      const res = await api.post<CompileResult>(`/datasets/${datasetId}/transformations/compile`, { sheet: sheetName, steps: cleaned, rows: 20 });
      setPreview(res);
    } catch (e) {
      toast({ kind: "error", title: "Preview failed", msg: errMsg(e) });
    } finally { setPreviewing(false); }
  };

  const canWork = allReady && !compile.error;

  return (
    <Card title="Pipeline" actions={
      <>
        <button className="btn btn-sm" disabled={!canWork || previewing} onClick={runPreview}>{previewing ? "Previewing…" : "Preview sample"}</button>
        <button className="btn btn-primary btn-sm" disabled={!canWork || !steps.length} onClick={() => setSaveOpen(true)}>Save pipeline</button>
      </>
    }>
      {steps.length === 0 && <div className="muted small" style={{ marginBottom: 12 }}>An empty pipeline just copies the sheet. Add cleanup steps below; they run in order.</div>}

      {steps.map((s, i) => {
        const inCols = colsBeforeStep(baseCols, steps, i);
        const ready = stepReady(s);
        return (
          <div key={s._id} className={cx("card card-pad")} style={{ marginBottom: 10, borderColor: ready ? undefined : "var(--warning, #b9770e)" }}>
            <div className="row">
              <span className="badge accent">{i + 1}</span>
              <strong style={{ marginLeft: 8 }}>{STEP_TYPES.find((t) => t.type === s.type)?.label || s.type}</strong>
              {!ready && <Badge kind="warning">incomplete</Badge>}
              <span className="spacer" />
              <button className="icon-btn" title="Move up" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
              <button className="icon-btn" title="Move down" aria-label="Move down" disabled={i === steps.length - 1} onClick={() => move(i, 1)}>↓</button>
              <button className="icon-btn" title="Remove" aria-label="Remove" onClick={() => remove(s._id)}>✕</button>
            </div>
            <div className="mt-8"><StepEditor step={s} cols={inCols} onChange={(p) => update(s._id, p)} /></div>
          </div>
        );
      })}

      <div className="row mt-8">
        <select className="select" style={{ maxWidth: 200 }} value={addType} onChange={(e) => setAddType(e.target.value)}>
          {STEP_TYPES.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
        </select>
        <button className="btn btn-sm" onClick={add}>+ Add step</button>
      </div>

      {/* validity / output schema */}
      <div className="mt-16">
        {!allReady ? (
          <div className="banner info">Finish the highlighted step(s) to compile — invalid pipelines are caught here (and at save), never at run.</div>
        ) : compile.loading ? <Loading label="Compiling…" />
          : compile.error ? <ErrorBanner error={compile.error} />
          : compile.data ? (
            <div className="banner good" role="status">
              <span>✓</span>
              <span>
                Valid · <strong>{compile.data.output_schema.length}</strong> output column(s)
                <div className="row row-wrap mt-8" style={{ gap: 6 }}>
                  {compile.data.output_schema.map((c) => (
                    <span key={c.position} className="badge" title={c.dtype}>{c.normalized_name || c.name}</span>
                  ))}
                </div>
              </span>
            </div>
          ) : null}
      </div>

      {preview && <PreviewModal result={preview} onClose={() => setPreview(null)} />}
      {saveOpen && (
        <SaveModal datasetId={datasetId} sheetName={sheetName} steps={cleaned}
          onClose={() => setSaveOpen(false)}
          onSaved={() => { setSaveOpen(false); toast({ kind: "good", title: "Pipeline saved" }); window.dispatchEvent(new Event("transform:saved")); }} />
      )}
    </Card>
  );
}

/* ---------------- per-type step editor ---------------- */
function StepEditor({ step, cols, onChange }: { step: UStep; cols: string[]; onChange: (p: Record<string, unknown>) => void }) {
  switch (step.type) {
    case "case_normalize":
      return (
        <div className="row row-wrap" style={{ gap: 10, alignItems: "flex-end" }}>
          <Field label="Columns"><MultiColumn value={step.columns as string[]} options={cols} onChange={(columns) => onChange({ columns })} /></Field>
          <Field label="Mode"><Sel value={step.mode as string} options={["lower", "upper", "title"]} onChange={(mode) => onChange({ mode })} /></Field>
        </div>
      );
    case "trim":
      return (
        <div className="row row-wrap" style={{ gap: 10, alignItems: "flex-end" }}>
          <Field label="Columns"><MultiColumn value={step.columns as string[]} options={cols} onChange={(columns) => onChange({ columns })} /></Field>
          <Field label="Side"><Sel value={step.mode as string} options={["both", "left", "right"]} onChange={(mode) => onChange({ mode })} /></Field>
        </div>
      );
    case "drop":
      return <Field label="Columns to remove"><MultiColumn value={step.columns as string[]} options={cols} onChange={(columns) => onChange({ columns })} /></Field>;
    case "replace":
      return (
        <div className="row row-wrap" style={{ gap: 10, alignItems: "flex-end" }}>
          <Field label="Column"><Sel value={step.column as string} options={cols} onChange={(column) => onChange({ column })} /></Field>
          <Field label="Match"><Sel value={step.mode as string} options={["exact", "substring", "regex"]} onChange={(mode) => onChange({ mode })} /></Field>
          <Field label="Find"><input className="input" style={{ maxWidth: 150 }} value={step.find as string} onChange={(e) => onChange({ find: e.target.value })} /></Field>
          <Field label="Replace with"><input className="input" style={{ maxWidth: 150 }} value={step.replace_with as string} onChange={(e) => onChange({ replace_with: e.target.value })} /></Field>
          <Field label="Fill NULLs with"><input className="input" style={{ maxWidth: 150 }} placeholder="(leave blank)" value={step.nulls_to as string} onChange={(e) => onChange({ nulls_to: e.target.value })} /></Field>
        </div>
      );
    case "filter":
      return <FilterEditor step={step} cols={cols} onChange={onChange} />;
    case "sort":
      return <SortEditor label="Order by" value={step.by as SortTerm[]} cols={cols} min={1} onChange={(by) => onChange({ by })} />;
    case "deduplicate":
      return (
        <div>
          <div className="row row-wrap" style={{ gap: 10, alignItems: "flex-end" }}>
            <Field label="Duplicate by (blank = all columns)"><MultiColumn value={step.subset as string[]} options={cols} onChange={(subset) => onChange({ subset })} /></Field>
            <Field label="Keep"><Sel value={step.keep as string} options={["first", "last", "none"]} onChange={(keep) => onChange({ keep })} /></Field>
          </div>
          <div className="mt-8"><SortEditor label="Tie-break order (makes first/last deterministic)" value={step.order_by as SortTerm[]} cols={cols} min={0} onChange={(order_by) => onChange({ order_by })} /></div>
        </div>
      );
    case "compute":
      return (
        <div className="row row-wrap" style={{ gap: 10, alignItems: "flex-start" }}>
          <Field label="New column name"><input className="input" style={{ maxWidth: 180 }} value={step.into as string} onChange={(e) => onChange({ into: e.target.value })} /></Field>
          <Field label="Expression"><ExprEditor value={step.expr as ExprUI} cols={cols} onChange={(expr) => onChange({ expr })} /></Field>
        </div>
      );
    default:
      return null;
  }
}

function FilterEditor({ step, cols, onChange }: { step: UStep; cols: string[]; onChange: (p: Record<string, unknown>) => void }) {
  const conds = step.conditions as Cond[];
  const upd = (i: number, patch: Partial<Cond>) => onChange({ conditions: conds.map((c, j) => j === i ? { ...c, ...patch } : c) });
  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <span className="small secondary">Keep rows where</span>
        <Sel value={step.logic as string} options={["and", "or"]} onChange={(logic) => onChange({ logic })} />
        <span className="small secondary">conditions hold</span>
      </div>
      {conds.map((c, i) => (
        <div className="row row-wrap mt-8" key={i}>
          <Sel value={c.column} options={cols} onChange={(column) => upd(i, { column })} width={170} />
          <Sel value={c.op} options={FILTER_OPS} onChange={(op) => upd(i, { op })} width={130} />
          {!NO_VALUE_OPS.includes(c.op) && (
            <input className="input" style={{ maxWidth: 180 }} placeholder={c.op === "in" || c.op === "between" ? "comma,separated" : "value"} value={c.value} onChange={(e) => upd(i, { value: e.target.value })} />
          )}
          <button className="icon-btn" title="Remove" aria-label="Remove" onClick={() => onChange({ conditions: conds.filter((_, j) => j !== i) })}>✕</button>
        </div>
      ))}
      <div className="mt-8"><button className="btn btn-sm" onClick={() => onChange({ conditions: [...conds, { column: cols[0] || "", op: "eq", value: "" }] })}>+ Condition</button></div>
    </div>
  );
}

function SortEditor({ label, value, cols, min, onChange }: { label: string; value: SortTerm[]; cols: string[]; min: number; onChange: (v: SortTerm[]) => void }) {
  const upd = (i: number, patch: Partial<SortTerm>) => onChange(value.map((t, j) => j === i ? { ...t, ...patch } : t));
  return (
    <div>
      <div className="small secondary" style={{ marginBottom: 4 }}>{label}</div>
      {value.map((t, i) => (
        <div className="row mt-8" key={i}>
          <Sel value={t.column} options={cols} onChange={(column) => upd(i, { column })} width={170} />
          <Sel value={t.direction} options={["asc", "desc"]} onChange={(direction) => upd(i, { direction: direction as "asc" | "desc" })} width={100} />
          {value.length > min && <button className="icon-btn" title="Remove" aria-label="Remove" onClick={() => onChange(value.filter((_, j) => j !== i))}>✕</button>}
        </div>
      ))}
      <div className="mt-8"><button className="btn btn-sm" onClick={() => onChange([...value, { column: cols[0] || "", direction: "asc" }])}>+ Column</button></div>
    </div>
  );
}

function ExprEditor({ value, cols, onChange }: { value: ExprUI; cols: string[]; onChange: (e: ExprUI) => void }) {
  const setKind = (k: ExprUI["k"]) => {
    const col0 = cols[0] || "";
    const op0: OperandUI = { op: "col", name: col0 };
    if (k === "col") onChange({ k: "col", name: col0 });
    else if (k === "arith") onChange({ k: "arith", fn: "add", left: op0, right: { op: "lit", vt: "num", raw: "" } });
    else if (k === "concat") onChange({ k: "concat", parts: [op0, { op: "col", name: cols[1] || col0 }], separator: " " });
    else if (k === "round") onChange({ k: "round", value: op0, digits: 2 });
    else onChange({ k: "cast", value: op0, to: "double" });
  };
  return (
    <div className="row row-wrap" style={{ gap: 6, alignItems: "center" }}>
      <Sel value={value.k} options={EXPR_KINDS.map((e) => e.k)} labels={EXPR_KINDS.map((e) => e.label)} onChange={(k) => setKind(k as ExprUI["k"])} width={140} />
      {value.k === "col" && <Sel value={value.name} options={cols} onChange={(name) => onChange({ k: "col", name })} width={170} />}
      {value.k === "arith" && <>
        <OperandEditor value={value.left} cols={cols} onChange={(left) => onChange({ ...value, left })} />
        <Sel value={value.fn} options={ARITH_FNS} onChange={(fn) => onChange({ ...value, fn })} width={90} />
        <OperandEditor value={value.right} cols={cols} onChange={(right) => onChange({ ...value, right })} />
      </>}
      {value.k === "concat" && <>
        {value.parts.map((p, i) => (
          <span key={i} className="row" style={{ gap: 4 }}>
            <OperandEditor value={p} cols={cols} onChange={(np) => onChange({ ...value, parts: value.parts.map((x, j) => j === i ? np : x) })} />
            {value.parts.length > 2 && <button className="icon-btn" aria-label="Remove part" title="Remove part" onClick={() => onChange({ ...value, parts: value.parts.filter((_, j) => j !== i) })}>✕</button>}
          </span>
        ))}
        <button className="btn btn-sm" onClick={() => onChange({ ...value, parts: [...value.parts, { op: "col", name: cols[0] || "" }] })}>+</button>
        <input className="input" style={{ maxWidth: 90 }} placeholder="separator" value={value.separator} onChange={(e) => onChange({ ...value, separator: e.target.value })} />
      </>}
      {value.k === "round" && <>
        <OperandEditor value={value.value} cols={cols} onChange={(v) => onChange({ ...value, value: v })} />
        <input className="input" style={{ maxWidth: 70 }} type="number" value={value.digits} onChange={(e) => onChange({ ...value, digits: Number(e.target.value) })} />
        <span className="small secondary">digits</span>
      </>}
      {value.k === "cast" && <>
        <OperandEditor value={value.value} cols={cols} onChange={(v) => onChange({ ...value, value: v })} />
        <span className="small secondary">to</span>
        <Sel value={value.to} options={CAST_TARGETS} onChange={(to) => onChange({ ...value, to })} width={120} />
      </>}
    </div>
  );
}

function OperandEditor({ value, cols, onChange }: { value: OperandUI; cols: string[]; onChange: (o: OperandUI) => void }) {
  const kind = value.op === "col" ? "col" : value.vt;
  return (
    <span className="row" style={{ gap: 4 }}>
      <Sel value={kind} options={["col", "num", "text"]} labels={["column", "number", "text"]} width={100}
        onChange={(k) => k === "col" ? onChange({ op: "col", name: cols[0] || "" }) : onChange({ op: "lit", vt: k as "num" | "text", raw: "" })} />
      {value.op === "col"
        ? <Sel value={value.name} options={cols} onChange={(name) => onChange({ op: "col", name })} width={150} />
        : <input className="input" style={{ maxWidth: 120 }} placeholder={value.vt === "num" ? "0" : "text"} value={value.raw} onChange={(e) => onChange({ op: "lit", vt: value.vt, raw: e.target.value })} />}
    </span>
  );
}

/* ---------------- small controls ---------------- */
function Sel({ value, options, labels, onChange, width }: { value: string; options: string[]; labels?: string[]; onChange: (v: string) => void; width?: number }) {
  return (
    <select className="select" style={{ maxWidth: width ?? 130 }} value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o, i) => <option key={o} value={o}>{labels?.[i] ?? o}</option>)}
    </select>
  );
}
function MultiColumn({ value, options, onChange }: { value: string[]; options: string[]; onChange: (v: string[]) => void }) {
  const remaining = options.filter((o) => !value.includes(o));
  return (
    <div className="row row-wrap" style={{ gap: 6 }}>
      {value.map((c) => (
        <span key={c} className="badge accent">{c}<span className="icon-btn" style={{ padding: 0, marginLeft: 4 }} title="Remove" aria-label="Remove" onClick={() => onChange(value.filter((x) => x !== c))}>✕</span></span>
      ))}
      <select className="select" style={{ maxWidth: 170 }} value="" onChange={(e) => { if (e.target.value) onChange([...value, e.target.value]); }}>
        <option value="">+ column…</option>
        {remaining.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}

/* ---------------- preview modal ---------------- */
function PreviewModal({ result, onClose }: { result: CompileResult; onClose: () => void }) {
  const cols = result.columns.length ? result.columns : result.output_schema.map((c) => c.normalized_name || c.name);
  return (
    <Modal wide title={<h3>Preview · {result.sheet_name} (v{result.version_number})</h3>} onClose={onClose}>
      <div className="banner info" style={{ marginBottom: 12 }}>Sampled dry run of {result.rows.length} row(s). Nothing is written — the source dataset is untouched.</div>
      <div className="table-wrap" style={{ maxHeight: 420 }}>
        <table className="data">
          <thead><tr>{cols.map((c) => <th scope="col" key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {result.rows.map((row, i) => <tr key={i}>{cols.map((c) => <td key={c}><Cell value={row[c]} /></td>)}</tr>)}
          </tbody>
        </table>
      </div>
      {result.rows.length === 0 && <div className="muted small mt-8">The sample produced no rows.</div>}
    </Modal>
  );
}

/* ---------------- save modal ---------------- */
function SaveModal({ datasetId, sheetName, steps, onClose, onSaved }: { datasetId: string; sheetName: string; steps: Record<string, unknown>[]; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await api.post(`/datasets/${datasetId}/transformations`, { name, description: description || undefined, sheet: sheetName, steps });
      onSaved();
    } catch (e) {
      toast({ kind: "error", title: "Save failed", msg: errMsg(e) });
    } finally { setBusy(false); }
  };
  return (
    <Modal title="Save pipeline" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !name} onClick={submit}>{busy ? "Saving…" : "Save"}</button></>}>
      <Field label="Name"><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Clean customer names" /></Field>
      <div className="mt-16"><Field label="Description (optional)"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} /></Field></div>
      <div className="small muted mt-16">Saving re-validates the pipeline against the sheet — an unrunnable pipeline is rejected here, before any run.</div>
    </Modal>
  );
}

/* ---------------- saved pipelines list ---------------- */
function SavedPipelines({ datasetId, idem, onReloadDataset }: { datasetId: string; idem: unknown[]; onReloadDataset: () => void }) {
  const toast = useToast();
  const list = useAsync(() => api.get<Page<TransformationOut>>(`/datasets/${datasetId}/transformations`), [datasetId, ...idem]);
  const [open, setOpen] = useState<string | null>(null);

  // reload when a save happens in the builder
  useEffect(() => {
    const h = () => list.reload();
    window.addEventListener("transform:saved", h);
    return () => window.removeEventListener("transform:saved", h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const del = async (id: string) => {
    try { await api.del(`/datasets/${datasetId}/transformations/${id}`); toast({ kind: "good", title: "Pipeline deleted" }); list.reload(); }
    catch (e) { toast({ kind: "error", title: "Delete failed", msg: errMsg(e) }); }
  };

  return (
    <AsyncView state={list} empty={<Card><div className="muted">No saved pipelines yet. Build one above and save it.</div></Card>}>
      {(page) => page.items.length === 0
        ? <Card><div className="muted">No saved pipelines yet. Build one above and save it.</div></Card>
        : (
          <div className="grid" style={{ gap: 12 }}>
            {page.items.map((t) => (
              <Card key={t.id}>
                <div className="row row-wrap">
                  <div>
                    <strong>{t.name}</strong>
                    <div className="small muted">{(t.steps || []).length} step(s){t.sheet_key ? ` · ${t.sheet_key}` : ""} · updated {fmtDate(t.updated_at)}</div>
                    {t.description && <div className="small secondary mt-8">{t.description}</div>}
                  </div>
                  <span className="spacer" />
                  <button className="btn btn-sm" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? "Hide runs" : "Runs"}</button>
                  <button className="btn btn-sm btn-danger" onClick={() => del(t.id)}>Delete</button>
                </div>
                {open === t.id && <div className="mt-16"><RunsPanel datasetId={datasetId} definitionId={t.id} onReloadDataset={onReloadDataset} /></div>}
              </Card>
            ))}
            <div className="small muted">Loading a saved pipeline back into the builder isn't wired in this reference; edit via re-create.</div>
          </div>
        )}
    </AsyncView>
  );
}

/* ---------------- runs + publish ---------------- */
function RunsPanel({ datasetId, definitionId, onReloadDataset }: { datasetId: string; definitionId: string; onReloadDataset: () => void }) {
  const toast = useToast();
  const runs = useAsync(() => api.get<Page<RunOut>>(`/datasets/${datasetId}/transformations/${definitionId}/runs`), [datasetId, definitionId]);
  const [running, setRunning] = useState(false);
  const [publishFor, setPublishFor] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    try {
      const r = await api.post<RunOut>(`/datasets/${datasetId}/transformations/${definitionId}/run`);
      toast({ kind: r.status === "failed" ? "error" : "good", title: r.status === "failed" ? "Run failed" : "Run complete", msg: r.error || undefined });
      runs.reload();
    } catch (e) {
      toast({ kind: "error", title: "Run failed", msg: errMsg(e) });
    } finally { setRunning(false); }
  };

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="btn btn-primary btn-sm" disabled={running} onClick={run}>{running ? "Running…" : "Run now"}</button>
        <span className="small muted">Run writes an output artifact; publish turns a run into a dataset or version.</span>
      </div>
      <AsyncView state={runs} empty={<div className="muted small">No runs yet.</div>}>
        {(page) => page.items.length === 0 ? <div className="muted small">No runs yet — run the pipeline above.</div> : (
          <table className="data">
            <thead><tr><th scope="col">Status</th><th scope="col">Started</th><th scope="col">Result</th><th scope="col"></th></tr></thead>
            <tbody>
              {page.items.map((r) => (
                <tr key={r.id}>
                  <td><Badge kind={statusKind(r.status)}>{r.status}</Badge></td>
                  <td className="small">{fmtDate(r.started_at)}</td>
                  <td className="small">{r.error ? <span className="cell-masked">{r.error}</span> : summarize(r.result_summary)}</td>
                  <td>
                    {r.published_version_id
                      ? <Badge kind="good">published{r.published_version_number != null ? ` v${r.published_version_number}` : ""}</Badge>
                      : r.status === "completed"
                        ? <button className="btn btn-sm" onClick={() => setPublishFor(r.id)}>Publish</button>
                        : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncView>
      {publishFor && (
        <PublishModal datasetId={datasetId} runId={publishFor}
          onClose={() => setPublishFor(null)}
          onDone={(mode) => { runs.reload(); if (mode === "new_version") onReloadDataset(); }} />
      )}
    </div>
  );
}

function PublishModal({ datasetId, runId, onClose, onDone }: { datasetId: string; runId: string; onClose: () => void; onDone: (mode: string) => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<"new_dataset" | "new_version">("new_dataset");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const r = await api.post<PublishResponse>(`/datasets/${datasetId}/transformations/runs/${runId}/publish`,
        { mode, name: mode === "new_dataset" ? (name || undefined) : undefined });
      toast({ kind: "good", title: "Published", msg: `${r.dataset_name} · v${r.version_number}` });
      onDone(mode); onClose();
    } catch (e) {
      toast({ kind: "error", title: "Publish failed", msg: errMsg(e) });
    } finally { setBusy(false); }
  };
  return (
    <Modal title="Publish run" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || (mode === "new_dataset" && !name)} onClick={submit}>{busy ? "Publishing…" : "Publish"}</button></>}>
      <Field label="Publish as">
        <Sel value={mode} options={["new_dataset", "new_version"]} labels={["a new dataset", "a new version of this dataset"]} width={260} onChange={(m) => setMode(m as "new_dataset" | "new_version")} />
      </Field>
      {mode === "new_dataset" && <div className="mt-16"><Field label="New dataset name"><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></Field></div>}
      <div className="small muted mt-16">Publishing is non-destructive — it never rewrites the source. A run can be published once.</div>
    </Modal>
  );
}

/* ---------------- helpers ---------------- */
function errMsg(e: unknown): string { return e instanceof ApiError ? `${e.detail}${e.code ? ` (${e.code})` : ""}` : String((e as Error)?.message || e); }
function summarize(s?: Record<string, unknown> | null): string {
  if (!s) return "—";
  const rows = s.output_rows ?? s.rows ?? s.row_count ?? s.rows_out;
  return rows != null ? `${fmtNum(rows)} rows` : "done";
}
