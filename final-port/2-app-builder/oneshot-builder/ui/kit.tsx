// The UI half of the platform. Written once; generated pages import only from here and react.
// Hooks talk to server.py; after any successful action or edit, every hook refetches.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

// ---------- types ----------
export type Row = Record<string, any> & { id: number };
export type Column = { name: string; type: string; required?: boolean; locked?: boolean; choices?: string[]; table?: string; default?: any };
export type TableInfo = { name: string; columns: Column[]; edit_roles: string[] };
export type ActionInput = { name: string; required: boolean; type: string };
export type ActionInfo = { name: string; roles: string[] | null; description: string; inputs: ActionInput[] };
export type AppInfo = { name: string; roles: string[]; tables: TableInfo[]; queries: { name: string; columns: string[] }[]; actions: ActionInfo[] };
export type User = { email: string; role: string };
export type Format = "text" | "money" | "number" | "date" | "datetime" | "status" | "boolean" | "email";
export type ColumnSpec = { key: string; label?: string; format?: Format; render?: (row: Row) => React.ReactNode; align?: "left" | "right" };

// ---------- core ----------
type Ctx = { app: AppInfo; user: User; setUser: (u: User) => void; version: number; bump: () => void; toast: (msg: string, kind?: "ok" | "error") => void };
const KitCtx = createContext<Ctx | null>(null);
const useKit = () => {
  const c = useContext(KitCtx);
  if (!c) throw new Error("kit hooks must be used inside the app");
  return c;
};

async function api(user: User, method: string, path: string, body?: unknown) {
  const res = await fetch(path, {
    method,
    headers: { "content-type": "application/json", "x-user": user.email, "x-role": user.role },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${res.status})`);
  return data;
}

function loadUser(): User | null {
  try { return JSON.parse(localStorage.getItem("kit.user") || "null"); } catch { return null; }
}

/** Wraps the generated <App/>. Loads the app description and holds the signed-in user. */
export function KitRoot({ children }: { children: React.ReactNode }) {
  const [app, setApp] = useState<AppInfo | null>(null);
  const [err, setErr] = useState("");
  const [user, setUserState] = useState<User>(() => loadUser() || { email: "member.user@bank.example", role: "member" });
  const [version, setVersion] = useState(0);
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: string }[]>([]);
  useEffect(() => {
    api(user, "GET", "/api/app").then((a: AppInfo) => {
      setApp(a);
      if (a.roles.length && !a.roles.includes(user.role)) setUserState({ email: `${a.roles[0]}.user@bank.example`, role: a.roles[0] });
    }).catch((e) => setErr(String(e.message || e)));
  }, []);
  const setUser = (u: User) => {
    try { localStorage.setItem("kit.user", JSON.stringify(u)); } catch { /* ignore */ }
    setUserState(u);
    setVersion((v) => v + 1);
  };
  const toast = useCallback((msg: string, kind: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 2500);
  }, []);
  if (err) return <div className="kit-page"><div className="kit-error">Couldn't load the app: {err}</div></div>;
  if (!app) return <div className="kit-page kit-muted">Loading…</div>;
  return (
    <KitCtx.Provider value={{ app, user, setUser, version, bump: () => setVersion((v) => v + 1), toast }}>
      <style>{CSS}</style>
      {children}
      <div className="kit-toasts" role="status">
        {toasts.map((t) => <div key={t.id} className={`kit-toast kit-toast-${t.kind}`}>{t.msg}</div>)}
      </div>
    </KitCtx.Provider>
  );
}

// ---------- data hooks ----------
export function useUser(): User & { is: (...roles: string[]) => boolean } {
  const { user } = useKit();
  return { ...user, is: (...roles: string[]) => roles.includes(user.role) };
}

export function useApp(): AppInfo {
  return useKit().app;
}

function useFetch<T>(path: string | null, initial: T) {
  const { user, version } = useKit();
  const [state, setState] = useState<{ data: T; loading: boolean; error: string }>({ data: initial, loading: true, error: "" });
  useEffect(() => {
    if (!path) return;
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    api(user, "GET", path)
      .then((d) => live && setState({ data: d, loading: false, error: "" }))
      .catch((e) => live && setState((s) => ({ ...s, loading: false, error: String(e.message || e) })));
    return () => { live = false; };
  }, [path, version, user.email, user.role]);
  return state;
}

/** Rows of a table. where = exact-match filters, q = text search, sort = "col" or "-col". */
export function useTable(table: string, opts: { where?: Record<string, any>; q?: string; sort?: string; limit?: number } = {}) {
  const p = new URLSearchParams();
  Object.entries(opts.where || {}).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && p.set(k, String(v)));
  if (opts.q) p.set("q", opts.q);
  if (opts.sort) p.set("sort", opts.sort);
  p.set("limit", String(opts.limit ?? 500));
  const s = useFetch<{ rows: Row[]; total: number }>(`/api/tables/${table}?${p}`, { rows: [], total: 0 });
  return { rows: s.data.rows || [], total: s.data.total || 0, loading: s.loading, error: s.error };
}

/** One row plus its change history (`row._history`). Pass null for no row. */
export function useRow(table: string, id: number | null | undefined) {
  const s = useFetch<Row | null>(id ? `/api/tables/${table}/${id}` : null, null);
  return { row: id ? s.data : null, loading: !!id && s.loading, error: s.error };
}

/** A saved query from schema.json, by name. */
export function useQuery(name: string) {
  const s = useFetch<Row[]>(`/api/queries/${name}`, []);
  return { rows: Array.isArray(s.data) ? s.data : [], loading: s.loading, error: s.error };
}

/** Run a backend action. run() resolves to the result, or throws with the refusal message (also shown as a toast). */
export function useAction(name: string) {
  const { user, bump, toast, app } = useKit();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const info = app.actions.find((a) => a.name === name);
  const run = async (inputs: Record<string, any> = {}) => {
    setRunning(true); setError("");
    try {
      const r = await api(user, "POST", `/api/actions/${name}`, inputs);
      bump();
      return r.result;
    } catch (e: any) {
      setError(e.message); toast(e.message, "error");
      throw e;
    } finally { setRunning(false); }
  };
  const allowed = !info || !info.roles || info.roles.includes(user.role);
  return { run, running, error, allowed, info };
}

/** id -> readable label for rows of a table (uses its first text column). */
export function useLookup(table: string): (id: any) => string {
  const { rows } = useTable(table, { limit: 1000 });
  const { app } = useKit();
  return useMemo(() => {
    const t = app.tables.find((x) => x.name === table);
    const col = t?.columns.find((c) => c.type === "text")?.name;
    const m = new Map(rows.map((r) => [r.id, col ? String(r[col]) : `#${r.id}`]));
    return (id: any) => (id == null ? "" : m.get(Number(id)) ?? `#${id}`);
  }, [rows, app, table]);
}

// ---------- formatting ----------
export function formatMoney(v: any) {
  if (v === null || v === undefined || v === "") return "";
  return Number(v).toLocaleString("en-US", { style: "currency", currency: "USD" });
}
export function formatDate(v: any) {
  if (!v) return "";
  const d = new Date(String(v).length <= 10 ? `${v}T00:00:00` : v);
  return isNaN(+d) ? String(v) : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
export function humanize(s: string) {
  return String(s ?? "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
function fmt(v: any, f?: Format) {
  if (v === null || v === undefined) return "";
  switch (f) {
    case "money": return formatMoney(v);
    case "number": return Number(v).toLocaleString("en-US");
    case "date": return formatDate(v);
    case "datetime": return formatDate(v) + " " + String(v).slice(11, 16);
    case "status": return <StatusBadge value={v} />;
    case "boolean": return v ? "Yes" : "No";
    default: return String(v);
  }
}

// ---------- layout ----------
/** Page frame: title, the signed-in user (with a role switcher standing in for login), content. */
export function AppShell({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  const { app, user, setUser } = useKit();
  return (
    <div className="kit-page">
      <header className="kit-header">
        <div>
          <h1>{title}</h1>
          {subtitle && <p className="kit-muted">{subtitle}</p>}
        </div>
        <label className="kit-user">
          <span className="kit-muted">Signed in as</span>
          <input aria-label="User email" value={user.email} onChange={(e) => setUser({ ...user, email: e.target.value })} />
          <select aria-label="Role" value={user.role} onChange={(e) => setUser({ email: `${e.target.value}.user@bank.example`, role: e.target.value })}>
            {app.roles.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
          </select>
        </label>
      </header>
      <main className="kit-main">{children}</main>
    </div>
  );
}

export function Section({ title, actions, children }: { title?: string; actions?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="kit-card">
      {(title || actions) && <div className="kit-card-head">{title && <h2>{title}</h2>}<div className="kit-row">{actions}</div></div>}
      {children}
    </section>
  );
}

export function Stats({ children }: { children: React.ReactNode }) {
  return <div className="kit-stats">{children}</div>;
}
export function Stat({ label, value, format, tone }: { label: string; value: any; format?: Format; tone?: "warn" | "bad" | "ok" }) {
  return <div className={`kit-stat ${tone ? "kit-tone-" + tone : ""}`}><span className="kit-muted">{label}</span><b>{fmt(value, format)}</b></div>;
}

export function Tabs({ tabs }: { tabs: { label: string; content: React.ReactNode }[] }) {
  const [i, setI] = useState(0);
  return (
    <div>
      <div className="kit-tabs" role="tablist">
        {tabs.map((t, j) => <button key={t.label} role="tab" aria-selected={i === j} onClick={() => setI(j)}>{t.label}</button>)}
      </div>
      <div className="kit-tabpanel">{tabs[i]?.content}</div>
    </div>
  );
}

export function StatusBadge({ value }: { value: string }) {
  const v = String(value ?? "").toLowerCase();
  const tone = /(reject|block|breach|fail|overdue|denied|escalat|written_off)/.test(v) ? "bad"
    : /(need|pending|wait|partial|open|assigned|review|request|flag)/.test(v) ? "warn"
    : /(paid|approved|done|closed|complete|active|ok|resolved|ready|signed|compliant|waived)/.test(v) ? "ok" : "neutral";
  return <span className={`kit-badge kit-tone-${tone}`}>{humanize(value)}</span>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="kit-empty kit-muted">{children}</div>;
}

// ---------- tables ----------
export function DataTable({ rows, columns, onRowClick, empty, loading }: {
  rows: Row[]; columns?: ColumnSpec[]; onRowClick?: (row: Row) => void; empty?: string; loading?: boolean;
}) {
  const cols: ColumnSpec[] = columns || Object.keys(rows[0] || {})
    .filter((k) => !["created_at", "created_by", "updated_at", "_history"].includes(k)).map((k) => ({ key: k }));
  const missing = rows.length ? cols.filter((c) => !c.render && !rows.some((r) => c.key in r)).map((c) => c.key) : [];
  useEffect(() => {
    if (missing.length) console.error(`DataTable: column(s) ${missing.join(", ")} not in the rows (rows have: ${Object.keys(rows[0]).join(", ")})`);
  }, [missing.join(",")]);
  if (loading && !rows.length) return <Empty>Loading…</Empty>;
  if (!rows.length) return <Empty>{empty || "Nothing here yet."}</Empty>;
  return (
    <div className="kit-tablewrap">
      <table className="kit-table">
        <thead><tr>{cols.map((c) => <th key={c.key} className={right(c) ? "r" : ""}>{c.label ?? humanize(c.key)}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? i} onClick={onRowClick ? () => onRowClick(r) : undefined} className={onRowClick ? "kit-click" : ""}>
              {cols.map((c) => <td key={c.key} className={right(c) ? "r" : ""}>{c.render ? c.render(r) : fmt(r[c.key], c.format)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const right = (c: ColumnSpec) => c.align === "right" || (!c.align && (c.format === "money" || c.format === "number"));

/** A complete list/search/add/edit/delete screen for a table, using its schema and permissions. */
export function RecordTable({ table, columns, title }: { table: string; columns?: ColumnSpec[]; title?: string }) {
  const { app, user, bump, toast } = useKit();
  const info = app.tables.find((t) => t.name === table);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const { rows, loading } = useTable(table, { q });
  if (!info) return <div className="kit-error">No table "{table}"</div>;
  const canEdit = info.edit_roles.includes(user.role);
  const fields = info.columns.filter((c) => !c.locked);
  const cols = columns || info.columns.slice(0, 6).map((c) => ({ key: c.name, format: formatFor(c) }));
  const save = async (values: Record<string, any>) => {
    try {
      if (editing === "new") await api(user, "POST", `/api/tables/${table}`, values);
      else if (editing) await api(user, "PATCH", `/api/tables/${table}/${editing.id}`, values);
      bump(); setEditing(null); toast("Saved");
    } catch (e: any) { toast(e.message, "error"); throw e; }
  };
  const remove = async (r: Row) => {
    if (!confirm("Delete this row?")) return;
    try { await api(user, "DELETE", `/api/tables/${table}/${r.id}`); bump(); toast("Deleted"); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const allCols: ColumnSpec[] = canEdit ? [...cols, { key: "_edit", label: "", render: (r) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      <Button onClick={() => setEditing(r)}>Edit</Button>
      <Button variant="danger" onClick={() => remove(r)}>Delete</Button>
    </span>) }] : cols;
  return (
    <Section title={title ?? humanize(table)} actions={<>
      <input className="kit-input" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label={`Search ${table}`} />
      {canEdit && <Button variant="primary" onClick={() => setEditing("new")}>Add</Button>}
    </>}>
      <DataTable rows={rows} columns={allCols} loading={loading} />
      {editing && (
        <Modal title={editing === "new" ? `Add to ${humanize(table)}` : `Edit ${humanize(table)}`} onClose={() => setEditing(null)}>
          <AutoForm fields={fields.map((c) => fieldFor(c, app))} initial={editing === "new" ? {} : editing} submitLabel="Save" onSubmit={save} />
        </Modal>
      )}
    </Section>
  );
}
function formatFor(c: Column): Format {
  return c.type === "money" ? "money" : c.type === "date" ? "date" : c.type === "datetime" ? "datetime"
    : c.type === "boolean" ? "boolean" : c.name === "status" ? "status" : c.type === "number" || c.type === "integer" ? "number" : "text";
}

// ---------- actions and forms ----------
type Field = { name: string; label: string; kind: "text" | "number" | "date" | "email" | "textarea" | "select" | "checkbox"; required: boolean; options?: { value: any; label: string }[]; refTable?: string };

function fieldFor(c: Column, app: AppInfo): Field {
  const base = { name: c.name, label: humanize(c.name), required: !!c.required };
  if (c.type === "ref") return { ...base, kind: "select", refTable: c.table };
  if (c.type === "choice") return { ...base, kind: "select", options: (c.choices || []).map((v) => ({ value: v, label: humanize(v) })) };
  if (["money", "number", "integer"].includes(c.type)) return { ...base, kind: "number" };
  if (c.type === "date") return { ...base, kind: "date" };
  if (c.type === "boolean") return { ...base, kind: "checkbox" };
  if (["email", "person"].includes(c.type)) return { ...base, kind: "email" };
  return { ...base, kind: /note|reason|description|comment|evidence|justification/.test(c.name) ? "textarea" : "text" };
}

function inputField(i: ActionInput, app: AppInfo): Field {
  // Use the matching table column when there is one (gives choices, refs, types).
  for (const t of app.tables) {
    const c = t.columns.find((c) => c.name === i.name);
    if (c) return { ...fieldFor(c, app), required: i.required };
  }
  // x_id, or a qualified one like new_branch_id / from_account_id, names a table (branches, accounts, ...)
  const stem = i.name.slice(0, -3);
  const singular = (n: string) => n.replace(/ies$/, "y").replace(/(ch|sh|ss|x)es$/, "$1").replace(/s$/, "");
  const ref = i.name.endsWith("_id") && app.tables.find((t) =>
    [t.name, singular(t.name)].some((n) => stem === n || stem.endsWith("_" + n)));
  const base = { name: i.name, label: humanize(i.name.replace(/_id$/, "")), required: i.required };
  if (ref) return { ...base, kind: "select", refTable: ref.name };
  if (["float", "int"].includes(i.type) || /amount|count|total|threshold|value/.test(i.name)) return { ...base, kind: "number" };
  if (i.type === "bool") return { ...base, kind: "checkbox" };
  if (/date|_on$|due/.test(i.name)) return { ...base, kind: "date" };
  if (/email/.test(i.name)) return { ...base, kind: "email" };
  return { ...base, kind: /note|reason|description|comment|evidence|justification|resolution/.test(i.name) ? "textarea" : "text" };
}

function RefSelect({ table, value, onChange, required, id }: { table: string; value: any; onChange: (v: any) => void; required: boolean; id: string }) {
  const { rows } = useTable(table, { limit: 1000 });
  const label = useLookup(table);
  return (
    <select id={id} className="kit-input" value={value ?? ""} required={required} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">Choose…</option>
      {rows.map((r) => <option key={r.id} value={r.id}>{label(r.id)}</option>)}
    </select>
  );
}

function AutoForm({ fields, initial, submitLabel, onSubmit }: { fields: Field[]; initial: Record<string, any>; submitLabel: string; onSubmit: (v: Record<string, any>) => Promise<any> }) {
  const [values, setValues] = useState<Record<string, any>>(() => Object.fromEntries(fields.map((f) => [f.name, initial[f.name] ?? (f.kind === "checkbox" ? false : "")])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k: string, v: any) => setValues((s) => ({ ...s, [k]: v }));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    const out: Record<string, any> = {};
    for (const f of fields) {
      const v = values[f.name];
      if (v === "" || v === null || v === undefined) continue;
      out[f.name] = f.kind === "number" ? Number(v) : v;
    }
    try { await onSubmit(out); } catch (err: any) { setError(err.message); } finally { setBusy(false); }
  };
  return (
    <form className="kit-form" onSubmit={submit}>
      {fields.map((f) => {
        const id = `f-${f.name}`;
        return (
          <label key={f.name} htmlFor={id} className="kit-field">
            <span>{f.label}{f.required && " *"}</span>
            {f.kind === "select" && f.refTable ? <RefSelect id={id} table={f.refTable} value={values[f.name]} required={f.required} onChange={(v) => set(f.name, v)} />
              : f.kind === "select" ? (
                <select id={id} className="kit-input" value={values[f.name]} required={f.required} onChange={(e) => set(f.name, e.target.value)}>
                  <option value="">Choose…</option>
                  {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>)
              : f.kind === "textarea" ? <textarea id={id} className="kit-input" rows={3} value={values[f.name]} required={f.required} onChange={(e) => set(f.name, e.target.value)} />
              : f.kind === "checkbox" ? <input id={id} type="checkbox" checked={!!values[f.name]} onChange={(e) => set(f.name, e.target.checked)} />
              : <input id={id} className="kit-input" type={f.kind} step={f.kind === "number" ? "any" : undefined} value={values[f.name]} required={f.required} onChange={(e) => set(f.name, e.target.value)} />}
          </label>
        );
      })}
      {error && <div className="kit-error" role="alert">{error}</div>}
      <div className="kit-row kit-end"><Button variant="primary" type="submit" disabled={busy}>{busy ? "Working…" : submitLabel}</Button></div>
    </form>
  );
}

/**
 * A button that runs a backend action. Inputs you pass are fixed (e.g. the row id);
 * if the action needs more, it opens a form for them, built from the action's inputs.
 * Hidden when the signed-in role can't run the action.
 */
export function ActionButton({ action, inputs = {}, label, variant = "default", confirm: confirmText, form, onDone }: {
  action: string; inputs?: Record<string, any>; label?: string; variant?: "primary" | "danger" | "default";
  confirm?: string; form?: boolean; onDone?: (result: any) => void;
}) {
  const { app } = useKit();
  const { run, running, allowed, info } = useAction(action);
  const { toast } = useKit();
  const [open, setOpen] = useState(false);
  if (!info) return <span className="kit-error">Unknown action "{action}"</span>;
  if (!allowed) return null;
  const missing = info.inputs.filter((i) => !(i.name in inputs));
  const needsForm = form || missing.some((i) => i.required);
  const text = label ?? humanize(action);
  const go = async (extra: Record<string, any> = {}) => {
    const r = await run({ ...inputs, ...extra });
    toast(`${text}: done`);
    setOpen(false);
    onDone?.(r);
  };
  return (
    <>
      <Button variant={variant} disabled={running} onClick={(e) => {
        e.stopPropagation();
        if (needsForm) return setOpen(true);
        if (confirmText && !window.confirm(confirmText)) return;
        go().catch(() => { /* toast already shown */ });
      }}>{running ? "Working…" : text}</Button>
      {open && (
        <Modal title={text} onClose={() => setOpen(false)}>
          {info.description && <p className="kit-muted">{info.description}</p>}
          <AutoForm fields={missing.map((i) => inputField(i, app))} initial={{}} submitLabel={text} onSubmit={go} />
        </Modal>
      )}
    </>
  );
}

export function Button({ variant = "default", ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "danger" | "default" }) {
  return <button type="button" {...p} className={`kit-btn kit-btn-${variant} ${p.className || ""}`} />;
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="kit-overlay" onClick={onClose}>
      <div className="kit-modal" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="kit-card-head"><h2>{title}</h2><Button aria-label="Close" onClick={onClose}>✕</Button></div>
        {children}
      </div>
    </div>
  );
}

/** Shows a row's change history (from useRow(...).row._history). */
export function History({ items }: { items?: { at: string; who: string; change: string }[] }) {
  if (!items?.length) return <Empty>No changes yet.</Empty>;
  return (
    <ul className="kit-history">
      {items.map((h, i) => {
        let c: any = {};
        try { c = JSON.parse(h.change); } catch { /* raw */ }
        const what = c.insert ? "Created" : c.delete ? "Deleted" : Object.entries(c).map(([k, v]: any) => `${humanize(k)}: ${v?.[0] ?? "—"} → ${v?.[1] ?? "—"}`).join(", ");
        return <li key={i}><span className="kit-muted">{formatDate(h.at)} {h.at.slice(11, 16)} · {h.who}</span><span>{what}</span></li>;
      })}
    </ul>
  );
}

const CSS = `
:root{--bg:#eef3f8;--panel:#fff;--panel2:#f4f7fb;--line:#d7e0ea;--fg:#0f1b2d;--muted:#56657c;--accent:#1f4fc4;--ok:#1d6b3f;--okbg:#dcefe3;--warn:#7a4b00;--warnbg:#fbefd6;--bad:#a12a2a;--badbg:#f8e1e1}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.kit-page{max-width:1200px;margin:0 auto;padding:20px 16px 60px}
.kit-header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.kit-header h1{margin:0;font-size:24px}.kit-header p{margin:2px 0 0}
.kit-user{display:flex;gap:6px;align-items:center;font-size:13px;flex-wrap:wrap}
.kit-user input,.kit-user select,.kit-input{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);font:inherit;color:inherit}
.kit-main{display:flex;flex-direction:column;gap:16px}
.kit-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:12px;min-width:0}
.kit-card-head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.kit-card-head h2{margin:0;font-size:16px}
.kit-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.kit-end{justify-content:flex-end}
.kit-muted{color:var(--muted)}.kit-empty{padding:24px;text-align:center;background:var(--panel2);border-radius:10px}
.kit-error{color:var(--bad);background:var(--badbg);padding:8px 12px;border-radius:8px}
.kit-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kit-stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column}.kit-stat b{font-size:22px;font-variant-numeric:tabular-nums}
.kit-stat.kit-tone-warn{border-color:#e9c98a}.kit-stat.kit-tone-bad{border-color:#e7a5a5}.kit-stat.kit-tone-ok{border-color:#9fd1b2}
.kit-tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
.kit-table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.kit-table th,.kit-table td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
.kit-table th{background:var(--panel2);color:var(--muted);font-weight:500}.kit-table tr:last-child td{border-bottom:none}.kit-table .r{text-align:right}
.kit-click{cursor:pointer}.kit-click:hover{background:var(--panel2)}
.kit-badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:500;background:var(--panel2);color:var(--muted)}
.kit-badge.kit-tone-ok{background:var(--okbg);color:var(--ok)}.kit-badge.kit-tone-warn{background:var(--warnbg);color:var(--warn)}.kit-badge.kit-tone-bad{background:var(--badbg);color:var(--bad)}
.kit-btn{padding:7px 13px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--fg);font:inherit;font-size:13px;cursor:pointer}
.kit-btn:disabled{opacity:.6;cursor:default}.kit-btn-primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}.kit-btn-danger{color:var(--bad);border-color:#e7a5a5}
.kit-tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin-bottom:14px;overflow-x:auto}
.kit-tabs button{border:none;background:none;padding:9px 14px;font:inherit;color:var(--muted);border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap}
.kit-tabs button[aria-selected=true]{color:var(--fg);border-bottom-color:var(--accent);font-weight:600}
.kit-tabpanel{display:flex;flex-direction:column;gap:16px}
.kit-overlay{position:fixed;inset:0;background:rgba(15,27,45,.35);display:flex;align-items:center;justify-content:center;padding:16px;z-index:10}
.kit-modal{background:var(--panel);border-radius:14px;padding:18px;width:min(480px,100%);max-height:90vh;overflow:auto;display:flex;flex-direction:column;gap:12px}
.kit-form{display:flex;flex-direction:column;gap:12px}.kit-field{display:flex;flex-direction:column;gap:4px;font-size:13px}
.kit-toasts{position:fixed;bottom:16px;right:16px;display:flex;flex-direction:column;gap:8px;z-index:20}
.kit-toast{padding:10px 14px;border-radius:10px;background:var(--fg);color:#fff;box-shadow:0 4px 14px rgba(0,0,0,.15);max-width:360px}.kit-toast-error{background:var(--bad)}
.kit-history{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}.kit-history li{display:flex;flex-direction:column;font-size:13px;border-left:2px solid var(--line);padding-left:10px}
`;
