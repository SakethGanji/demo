import { useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Page } from "../../api/client";
import {
  AsyncView, Badge, Card, StatTile, Modal, Field, Loading, EmptyState, ErrorBanner,
  useToast, useAsync, statusKind, cx, fmtNum, fmtDate,
} from "../../components/ui";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

// ---------------------------------------------------------------------------
// Shapes (mirror app/features/quality/schemas.py)
// ---------------------------------------------------------------------------
interface Rule {
  id: string;
  name: string;
  description?: string | null;
  scope_type: string;
  sheet_selector?: string | null;
  column_selector?: string | null;
  rule_type: string;
  parameters: Record<string, unknown>;
  severity: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}
interface Run {
  id: string;
  status: string;
  rules_total?: number | null;
  rules_passed?: number | null;
  rules_failed?: number | null;
  error_failures?: number | null;
  warning_failures?: number | null;
  triggered_by?: string | null;
  started_at: string;
  completed_at?: string | null;
  error?: string | null;
}
interface RuleResult {
  id?: string | null;
  rule_id?: string | null;
  rule_name: string;
  rule_type: string;
  scope_type: string;
  sheet_selector?: string | null;
  column_selector?: string | null;
  severity: string;
  status: string;
  failure_count?: number | null;
  message?: string | null;
}
interface RunDetail extends Run { results: RuleResult[]; }

// Param spec per rule type — drives the create/edit form and matches
// engine.py's expectations. `scope` decides whether a column is required.
type ParamKind = "number" | "string" | "list";
interface ParamSpec { key: string; label: string; kind: ParamKind; placeholder?: string; }
interface TypeSpec { type: string; scope: "dataset" | "sheet" | "column" | "cross_sheet"; params: ParamSpec[]; hint: string; }

const TYPES: TypeSpec[] = [
  { type: "sheet_exists", scope: "dataset", params: [], hint: "sheet_selector is the required sheet name" },
  { type: "row_count_min", scope: "sheet", params: [{ key: "min", label: "Minimum rows", kind: "number", placeholder: "1" }], hint: "" },
  { type: "not_null", scope: "column", params: [], hint: "" },
  { type: "unique", scope: "column", params: [], hint: "" },
  { type: "accepted_values", scope: "column", params: [{ key: "values", label: "Accepted values", kind: "list", placeholder: "a, b, c" }], hint: "" },
  { type: "range", scope: "column", params: [{ key: "min", label: "Min", kind: "number" }, { key: "max", label: "Max", kind: "number" }], hint: "at least one of min / max" },
  { type: "regex_match", scope: "column", params: [{ key: "pattern", label: "Regex pattern", kind: "string", placeholder: "^[A-Z]{2}\\d+$" }], hint: "" },
  { type: "foreign_key", scope: "cross_sheet", params: [{ key: "ref_sheet", label: "Reference sheet", kind: "string" }, { key: "ref_column", label: "Reference column", kind: "string" }], hint: "" },
];
const specOf = (t: string) => TYPES.find((s) => s.type === t) ?? TYPES[0];
const needsColumn = (t: string) => ["column", "cross_sheet"].includes(specOf(t).scope);

const emptyPage: Page<Run> = { items: [], total: 0, limit: 1, offset: 0 };

function target(r: { sheet_selector?: string | null; column_selector?: string | null }): string {
  if (r.column_selector) return `${r.sheet_selector ?? "?"}.${r.column_selector}`;
  return r.sheet_selector ?? "—";
}

// ---------------------------------------------------------------------------

export function Quality({ dataset, reload }: DatasetTabProps) {
  const { identity } = useIdentity();
  const toast = useToast();
  const idem = [dataset.id, identity.userId, identity.teamId];
  const version = dataset.current_version ?? null;

  const rules = useAsync(() => api.get<Page<Rule>>(`/datasets/${dataset.id}/rules`), idem);
  const runs = useAsync(
    () => version == null ? Promise.resolve(emptyPage)
      : api.get<Page<Run>>(`/datasets/${dataset.id}/versions/${version}/validations`, { limit: 20 }),
    [dataset.id, version, identity.userId, identity.teamId],
  );

  const [editing, setEditing] = useState<Rule | null | undefined>(undefined); // undefined = closed, null = create
  const [confirmDel, setConfirmDel] = useState<Rule | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const afterMutation = () => { rules.reload(); };

  const toggleEnabled = async (r: Rule) => {
    try {
      await api.patch(`/datasets/${dataset.id}/rules/${r.id}`, { enabled: !r.enabled });
      toast({ kind: "good", title: r.enabled ? "Rule disabled" : "Rule enabled", msg: r.name });
      rules.reload();
    } catch (e) {
      toast({ kind: "error", title: "Could not update rule", msg: errMsg(e) });
    }
  };

  const doDelete = async (r: Rule) => {
    try {
      await api.del(`/datasets/${dataset.id}/rules/${r.id}`);
      toast({ kind: "good", title: "Rule deleted", msg: r.name });
      setConfirmDel(null);
      rules.reload();
    } catch (e) {
      toast({ kind: "error", title: "Could not delete rule", msg: errMsg(e) });
    }
  };

  const runValidation = async () => {
    if (version == null) return;
    setRunning(true);
    try {
      const run = await api.post<RunDetail>(`/datasets/${dataset.id}/versions/${version}/validate`);
      toast({
        kind: run.error_failures ? "error" : "good",
        title: run.error_failures ? "Validation found failures" : "Validation passed",
        msg: `${fmtNum(run.rules_passed)} / ${fmtNum(run.rules_total)} rules passed`,
      });
      runs.reload();
      reload(); // refresh the dataset header's validation badge
      setOpenRun(run.id);
    } catch (e) {
      toast({ kind: "error", title: "Validation could not run", msg: errMsg(e) });
    } finally {
      setRunning(false);
    }
  };

  const latest = runs.data?.items[0];

  return (
    <div>
      <div className="row row-wrap" style={{ marginBottom: 16 }}>
        <h2 style={{ flex: 1 }}>Quality rules</h2>
        <button className="btn" onClick={() => setEditing(null)}>+ New rule</button>
        <button className="btn btn-primary" disabled={running || version == null} onClick={runValidation}>
          {running ? "Running…" : version == null ? "No version" : `Run validation · v${version}`}
        </button>
      </div>

      <AsyncView
        state={rules}
        empty={<EmptyState icon="✓" title="No quality rules yet" hint="Add rules to validate this dataset." action={<button className="btn btn-primary" onClick={() => setEditing(null)}>+ New rule</button>} />}
      >
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="✓" title="No quality rules yet" hint="Add rules to validate this dataset." action={<button className="btn btn-primary" onClick={() => setEditing(null)}>+ New rule</button>} />
        ) : (
          <Card pad={false}>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Target</th><th scope="col">Severity</th><th scope="col">Enabled</th><th scope="col" /></tr>
                </thead>
                <tbody>
                  {page.items.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{r.name}</div>
                        {r.description && <div className="small muted">{r.description}</div>}
                      </td>
                      <td><span className="mono small">{r.rule_type}</span></td>
                      <td className="mono small">{target(r)}</td>
                      <td><Badge kind={r.severity === "error" ? "critical" : "warning"}>{r.severity}</Badge></td>
                      <td>
                        <button className={cx("btn", "btn-sm")} onClick={() => toggleEnabled(r)}>
                          {r.enabled ? "● on" : "○ off"}
                        </button>
                      </td>
                      <td>
                        <div className="row gap-6">
                          <button className="btn btn-sm" onClick={() => setEditing(r)}>Edit</button>
                          <button className="btn btn-sm" onClick={() => setConfirmDel(r)}>Delete</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </AsyncView>

      <h2 style={{ margin: "28px 0 12px" }}>Validation runs</h2>
      {version == null ? (
        <div className="banner info">This dataset has no current version to validate.</div>
      ) : (
        <>
          {latest && (
            <div className="grid grid-4" style={{ marginBottom: 16 }}>
              <StatTile label="Latest status" value={<Badge kind={statusKind(latest.error_failures ? "failed" : latest.status)}>{latest.error_failures ? "failed" : latest.status}</Badge>} sub={fmtDate(latest.started_at)} />
              <StatTile label="Rules passed" value={fmtNum(latest.rules_passed)} sub={`of ${fmtNum(latest.rules_total)}`} />
              <StatTile label="Error failures" value={fmtNum(latest.error_failures)} />
              <StatTile label="Warning failures" value={fmtNum(latest.warning_failures)} />
            </div>
          )}
          <AsyncView
            state={runs}
            empty={<EmptyState icon="⧗" title="No validation runs yet" hint="Run validation to check rules against this version." />}
          >
            {(page) => page.items.length === 0 ? (
              <EmptyState icon="⧗" title="No validation runs yet" hint="Run validation to check rules against this version." />
            ) : (
              <Card pad={false}>
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr><th scope="col">Started</th><th scope="col">Status</th><th scope="col">Passed</th><th scope="col">Failed</th><th scope="col">Errors</th><th scope="col">Warnings</th><th scope="col" /></tr>
                    </thead>
                    <tbody>
                      {page.items.map((run) => (
                        <tr key={run.id} className="clickable" onClick={() => setOpenRun(run.id)}>
                          <td>{fmtDate(run.started_at)}</td>
                          <td><Badge kind={statusKind(run.error_failures ? "failed" : run.status)}>{run.error_failures ? "failed" : run.status}</Badge></td>
                          <td>{fmtNum(run.rules_passed)}</td>
                          <td>{fmtNum(run.rules_failed)}</td>
                          <td>{fmtNum(run.error_failures)}</td>
                          <td>{fmtNum(run.warning_failures)}</td>
                          <td><button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); setOpenRun(run.id); }}>Details</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </AsyncView>
        </>
      )}

      {editing !== undefined && (
        <RuleForm
          datasetId={dataset.id}
          rule={editing}
          onClose={() => setEditing(undefined)}
          onSaved={() => { setEditing(undefined); afterMutation(); }}
        />
      )}
      {confirmDel && (
        <Modal
          title="Delete rule"
          onClose={() => setConfirmDel(null)}
          footer={<>
            <button className="btn" onClick={() => setConfirmDel(null)}>Cancel</button>
            <button className="btn btn-primary" onClick={() => doDelete(confirmDel)}>Delete</button>
          </>}
        >
          <p>Delete rule <strong>{confirmDel.name}</strong>? Past validation results keep their snapshot of it.</p>
        </Modal>
      )}
      {openRun && <RunDetailModal datasetId={dataset.id} runId={openRun} onClose={() => setOpenRun(null)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create / edit rule
// ---------------------------------------------------------------------------
function RuleForm({ datasetId, rule, onClose, onSaved }: { datasetId: string; rule: Rule | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const editing = rule != null;
  const [name, setName] = useState(rule?.name ?? "");
  const [description, setDescription] = useState(rule?.description ?? "");
  const [ruleType, setRuleType] = useState(rule?.rule_type ?? "not_null");
  const [sheet, setSheet] = useState(rule?.sheet_selector ?? "");
  const [column, setColumn] = useState(rule?.column_selector ?? "");
  const [severity, setSeverity] = useState(rule?.severity ?? "error");
  const [params, setParams] = useState<Record<string, string>>(() => prefillParams(rule?.parameters));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const spec = specOf(ruleType);

  const setParam = (k: string, v: string) => setParams((p) => ({ ...p, [k]: v }));

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const parameters = buildParams(spec, params);
      if (editing) {
        const body: Record<string, unknown> = {
          name, description: description || null, sheet_selector: sheet || null,
          column_selector: needsColumn(ruleType) ? (column || null) : null,
          parameters, severity,
        };
        await api.patch(`/datasets/${datasetId}/rules/${rule!.id}`, body);
        toast({ kind: "good", title: "Rule updated", msg: name });
      } else {
        const body: Record<string, unknown> = {
          name, description: description || undefined, rule_type: ruleType,
          sheet_selector: sheet || undefined,
          column_selector: needsColumn(ruleType) ? (column || undefined) : undefined,
          parameters, severity,
        };
        await api.post(`/datasets/${datasetId}/rules`, body);
        toast({ kind: "good", title: "Rule created", msg: name });
      }
      onSaved();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={editing ? "Edit rule" : "New quality rule"}
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={saving || !name.trim()} onClick={submit}>{saving ? "Saving…" : editing ? "Save changes" : "Create rule"}</button>
      </>}
    >
      {error != null && <ErrorBanner error={error} />}
      <Field label="Name">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Unique rule name" />
      </Field>
      <Field label="Rule type">
        <select className="select" value={ruleType} disabled={editing} onChange={(e) => { setRuleType(e.target.value); setParams({}); }}>
          {TYPES.map((t) => <option key={t.type} value={t.type}>{t.type}</option>)}
        </select>
      </Field>
      {spec.hint && <div className="small muted" style={{ margin: "-6px 0 12px" }}>{spec.hint}</div>}
      <Field label={ruleType === "sheet_exists" ? "Sheet name (required)" : "Sheet selector (sheet_key)"}>
        <input className="input" value={sheet} onChange={(e) => setSheet(e.target.value)} placeholder="e.g. main" />
      </Field>
      {needsColumn(ruleType) && (
        <Field label="Column selector (normalized name)">
          <input className="input" value={column} onChange={(e) => setColumn(e.target.value)} placeholder="e.g. customer_id" />
        </Field>
      )}
      {spec.params.map((p) => (
        <Field key={p.key} label={p.label}>
          <input className="input" value={params[p.key] ?? ""} placeholder={p.placeholder} onChange={(e) => setParam(p.key, e.target.value)} />
        </Field>
      ))}
      <Field label="Severity">
        <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="error">error</option>
          <option value="warning">warning</option>
        </select>
      </Field>
      <Field label="Description (optional)">
        <input className="input" value={description ?? ""} onChange={(e) => setDescription(e.target.value)} />
      </Field>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Run detail
// ---------------------------------------------------------------------------
function RunDetailModal({ datasetId, runId, onClose }: { datasetId: string; runId: string; onClose: () => void }) {
  const state = useAsync(() => api.get<RunDetail>(`/datasets/${datasetId}/validations/${runId}`), [datasetId, runId]);
  return (
    <Modal title="Validation run" onClose={onClose} wide>
      {state.loading ? <Loading /> : state.error ? <ErrorBanner error={state.error} /> : state.data && (
        <div>
          <div className="grid grid-4" style={{ marginBottom: 16 }}>
            <StatTile label="Status" value={<Badge kind={statusKind(state.data.error_failures ? "failed" : state.data.status)}>{state.data.error_failures ? "failed" : state.data.status}</Badge>} sub={fmtDate(state.data.started_at)} />
            <StatTile label="Passed" value={`${fmtNum(state.data.rules_passed)} / ${fmtNum(state.data.rules_total)}`} />
            <StatTile label="Error failures" value={fmtNum(state.data.error_failures)} />
            <StatTile label="Warning failures" value={fmtNum(state.data.warning_failures)} />
          </div>
          {state.data.error && <div className="banner error" style={{ marginBottom: 12 }}>{state.data.error}</div>}
          {state.data.results.length === 0 ? (
            <EmptyState title="No per-rule results" />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th scope="col">Rule</th><th scope="col">Type</th><th scope="col">Target</th><th scope="col">Severity</th><th scope="col">Status</th><th scope="col">Failures</th><th scope="col">Message</th></tr>
                </thead>
                <tbody>
                  {state.data.results.map((r, i) => (
                    <tr key={r.id ?? i}>
                      <td style={{ fontWeight: 600 }}>{r.rule_name}</td>
                      <td className="mono small">{r.rule_type}</td>
                      <td className="mono small">{target(r)}</td>
                      <td>{r.severity}</td>
                      <td><Badge kind={statusKind(r.status)}>{r.status}</Badge></td>
                      <td>{fmtNum(r.failure_count)}</td>
                      <td className="small">{r.message ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
function errMsg(e: unknown): string {
  if (e instanceof ApiError) return `${e.detail}${e.code && e.code !== "error" ? ` (${e.code})` : ""}`;
  return String((e as Error)?.message || e);
}

function buildParams(spec: TypeSpec, raw: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of spec.params) {
    const v = (raw[p.key] ?? "").trim();
    if (v === "") continue;
    if (p.kind === "number") out[p.key] = Number(v);
    else if (p.kind === "list") out[p.key] = v.split(",").map((s) => s.trim()).filter(Boolean);
    else out[p.key] = v;
  }
  return out;
}

function prefillParams(parameters?: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  if (!parameters) return out;
  for (const [k, v] of Object.entries(parameters)) {
    out[k] = Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v);
  }
  return out;
}
