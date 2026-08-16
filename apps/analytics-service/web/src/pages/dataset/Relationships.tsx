import { useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Page } from "../../api/client";
import { AsyncView, Badge, Card, EmptyState, Field, Loading, Modal, StatTile, cx, fmtNum, useAsync, useToast } from "../../components/ui";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

/* ---- Shapes (from app/features/relationships/schemas.py) ---- */
interface Relationship {
  id: string;
  dataset_id: string;
  from_sheet: string | null;
  from_column: string;
  to_dataset_id: string;
  to_sheet: string | null;
  to_column: string;
  status: "suggested" | "confirmed" | "rejected";
  method: "fk_rule" | "statistical" | "manual";
  evidence: Record<string, unknown>;
  confidence: number | null;
  created_by?: string | null;
  reviewed_by?: string | null;
}
interface Column { name: string; normalized_name?: string; dtype?: string; }
interface Sheet { name: string; sheet_key: string; columns?: Column[]; }
interface SeedResponse { created: number; relationships: Relationship[]; }
interface SuggestResponse { suggested: number; pairs_examined: number; skipped: number; relationships: Relationship[]; }
interface JoinWarnings {
  left_rows: number; right_rows: number;
  left_duplicate_keys: number; right_duplicate_keys: number;
  many_to_many: boolean; estimated_output_rows: number; row_expansion_factor: number;
  unmatched_left_pct: number; unmatched_right_pct: number; column_collisions: string[];
}
interface JoinPreview { warnings: JoinWarnings; output_columns: string[]; preview: Record<string, unknown>[]; relationship: Relationship; }
interface JoinExecuteResponse { run_id: string; row_count: number; warnings: JoinWarnings; output_columns: string[]; }
interface PublishResponse { dataset_id: string; dataset_name: string; version_number: number; mode: string; }

const STATUS_KIND = { confirmed: "good", suggested: "warning", rejected: "critical" } as const;
const STATUSES = ["all", "suggested", "confirmed", "rejected"] as const;

function apiMsg(e: unknown): string {
  return e instanceof ApiError ? `${e.detail}${e.code ? ` (${e.code})` : ""}` : String((e as Error)?.message || e);
}
/** A 0–1 fraction (e.g. `confidence`) rendered as a percentage. */
function pctFromFraction(n: number | null | undefined): string {
  return n == null ? "—" : `${(n * 100).toFixed(0)}%`;
}

/** A value the API already expressed in percent (e.g. `unmatched_left_pct`).
 *  Must NOT be scaled again — sniffing "n <= 1 means fraction" multiplied every
 *  sub-1% rate by 100, so a 0.5%-unmatched join displayed as 50%. Keeps two
 *  decimals so sub-1% resolution the API computed isn't thrown away. */
function pctFromPercent(n: number | null | undefined): string {
  if (n == null) return "—";
  const s = n >= 10 ? n.toFixed(0) : n.toFixed(2).replace(/\.?0+$/, "");
  return `${s}%`;
}

export function Relationships({ dataset }: DatasetTabProps) {
  const { identity } = useIdentity();
  const toast = useToast();
  const [filter, setFilter] = useState<(typeof STATUSES)[number]>("all");
  const [declaring, setDeclaring] = useState(false);
  const [evidenceOf, setEvidenceOf] = useState<Relationship | null>(null);
  const [joinOf, setJoinOf] = useState<Relationship | null>(null);
  const [busy, setBusy] = useState(false);

  const rels = useAsync(
    () => api.get<Page<Relationship>>(`/datasets/${dataset.id}/relationships`, filter === "all" ? undefined : { status: filter }),
    [dataset.id, filter, identity.userId, identity.teamId],
  );
  const v = dataset.current_version ?? null;
  const sheets = useAsync(
    () => v == null ? Promise.resolve<Page<Sheet>>({ items: [], total: 0, limit: 0, offset: 0 })
      : api.get<Page<Sheet>>(`/datasets/${dataset.id}/versions/${v}/sheets`),
    [dataset.id, v, identity.userId, identity.teamId],
  );

  async function mutate(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  }
  const discover = (label: string, fn: () => Promise<{ title: string; msg: string }>) =>
    mutate(async () => {
      try {
        const { title, msg } = await fn();
        toast({ kind: "good", title, msg });
        rels.reload();
      } catch (e) { toast({ kind: "error", title: `${label} failed`, msg: apiMsg(e) }); }
    });

  const review = (r: Relationship, action: "confirm" | "reject") =>
    mutate(async () => {
      try {
        await api.post(`/datasets/${dataset.id}/relationships/${r.id}/${action}`);
        toast({ kind: "good", title: `Relationship ${action === "confirm" ? "confirmed" : "rejected"}` });
        rels.reload();
      } catch (e) { toast({ kind: "error", title: "Review failed", msg: apiMsg(e) }); }
    });

  const remove = (r: Relationship) =>
    mutate(async () => {
      try {
        await api.del(`/datasets/${dataset.id}/relationships/${r.id}`);
        toast({ kind: "good", title: "Relationship deleted" });
        rels.reload();
      } catch (e) {
        // The 409 carries `attached.join_definitions: [{id,name}]` precisely so the
        // user can be told WHICH saved joins to remove first.
        const attached = e instanceof ApiError
          ? ((e.body.attached as { join_definitions?: { name?: string }[] } | undefined)?.join_definitions || [])
          : [];
        const names = attached.map((d) => d.name).filter(Boolean).join(", ");
        toast({ kind: "error", title: "Delete failed", msg: names ? `${apiMsg(e)} — ${names}` : apiMsg(e) });
      }
    });

  const counts = useMemo(() => {
    const c = { suggested: 0, confirmed: 0, rejected: 0 };
    for (const r of rels.data?.items || []) if (r.status in c) c[r.status]++;
    return c;
  }, [rels.data]);

  return (
    <div>
      <div className="grid grid-4" style={{ marginBottom: 20 }}>
        <StatTile label="Relationships" value={fmtNum(rels.data?.total)} />
        <StatTile label="Suggested" value={fmtNum(counts.suggested)} sub="awaiting review" />
        <StatTile label="Confirmed" value={fmtNum(counts.confirmed)} sub="joinable" />
        <StatTile label="Rejected" value={fmtNum(counts.rejected)} />
      </div>

      <div className="row row-wrap" style={{ marginBottom: 16 }}>
        <Field label="Status">
          <select className="select" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} style={{ minWidth: 130 }}>
            {STATUSES.map((s) => <option key={s} value={s}>{s === "all" ? "All" : s}</option>)}
          </select>
        </Field>
        <span className="spacer" />
        <button className="btn btn-sm" disabled={busy} onClick={() =>
          discover("Seed", async () => {
            const r = await api.post<SeedResponse>(`/datasets/${dataset.id}/relationships/seed`);
            return { title: "Seeded from FK rules", msg: r.created ? `Created ${r.created} relationship(s).` : "No new edges — rules already seeded." };
          })}>Seed from rules</button>
        <button className="btn btn-sm" disabled={busy} onClick={() =>
          discover("Discovery", async () => {
            // `suggested` counts pairs DERIVED, including ones that only refresh
            // an existing edge's evidence — so report new edges separately
            // rather than letting "suggested N" read as "N new relationships".
            const before = rels.data?.total ?? 0;
            const r = await api.post<SuggestResponse>(`/datasets/${dataset.id}/relationships/suggest`);
            const after = await api.get<Page<Relationship>>(`/datasets/${dataset.id}/relationships`);
            const added = Math.max(0, (after.total ?? 0) - before);
            const skip = r.skipped ? ` · ${r.skipped} skipped by the cap` : "";
            return {
              title: "Discovery complete",
              msg: `${fmtNum(r.pairs_examined)} pairs examined · ${r.suggested} matched · `
                + (added ? `${added} new` : "no new edges (existing evidence refreshed)") + skip,
            };
          })}>Run discovery</button>
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => setDeclaring(true)}>+ Declare</button>
      </div>

      <AsyncView state={rels} empty={<EmptyState icon="⧉" title="No relationships yet" hint="Seed from FK rules, run discovery, or declare one by hand." />}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="⧉" title={`No ${filter === "all" ? "" : filter + " "}relationships`} hint="Seed from FK rules, run discovery, or declare one by hand." />
        ) : (
          <Card pad={false}>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">From</th><th scope="col">To</th><th scope="col">Method</th><th scope="col">Status</th><th scope="col">Confidence</th><th scope="col" style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r) => (
                    <tr key={r.id}>
                      <td className="mono small">{r.from_sheet ? `${r.from_sheet}.` : ""}{r.from_column}</td>
                      <td className="mono small">
                        {r.to_dataset_id !== r.dataset_id && <Badge kind="accent">ext</Badge>}{" "}
                        {r.to_sheet ? `${r.to_sheet}.` : ""}{r.to_column}
                      </td>
                      <td><Badge kind={r.method === "manual" ? "accent" : r.method === "fk_rule" ? "good" : "neutral"}>{r.method}</Badge></td>
                      <td><Badge kind={STATUS_KIND[r.status]}>{r.status}</Badge></td>
                      <td className="small">{pctFromFraction(r.confidence)}</td>
                      <td>
                        <div className="row gap-6" style={{ justifyContent: "flex-end" }}>
                          {r.status === "suggested" && <>
                            <button className="btn btn-sm" disabled={busy} onClick={() => review(r, "confirm")}>Confirm</button>
                            <button className="btn btn-sm" disabled={busy} onClick={() => review(r, "reject")}>Reject</button>
                          </>}
                          {r.status === "confirmed" && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => setJoinOf(r)}>Join</button>}
                          {/* rejected → confirmed is an allowed transition server-side;
                              without this the steward's only way back is deletion,
                              which the dependent-join guard can itself refuse. */}
                          {r.status === "rejected" && <button className="btn btn-sm" disabled={busy} onClick={() => review(r, "confirm")}>Reinstate</button>}
                          <button className="btn btn-sm" onClick={() => setEvidenceOf(r)}>Evidence</button>
                          <button className="icon-btn" disabled={busy} title="Delete" aria-label="Delete" onClick={() => remove(r)}>✕</button>
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

      {declaring && (
        <DeclareModal
          datasetId={dataset.id}
          sheets={sheets}
          onClose={() => setDeclaring(false)}
          onDone={() => { setDeclaring(false); rels.reload(); }}
        />
      )}
      {evidenceOf && <EvidenceModal rel={evidenceOf} onClose={() => setEvidenceOf(null)} />}
      {joinOf && <JoinModal rel={joinOf} onClose={() => setJoinOf(null)} />}
    </div>
  );
}

/* ---- Declare a relationship ---- */
function DeclareModal({ datasetId, sheets, onClose, onDone }: {
  datasetId: string;
  sheets: ReturnType<typeof useAsync<Page<Sheet>>>;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const items = sheets.data?.items || [];
  const multi = items.length > 1;
  const [fromSheet, setFromSheet] = useState<string>("");
  const [fromCol, setFromCol] = useState<string>("");
  const [toSheet, setToSheet] = useState<string>("");
  const [toCol, setToCol] = useState<string>("");
  const [confirmed, setConfirmed] = useState(true);
  const [busy, setBusy] = useState(false);

  const cols = (name: string): Column[] => items.find((s) => s.name === (name || items[0]?.name))?.columns || [];
  const fromCols = cols(fromSheet);
  const toCols = cols(toSheet);
  const colName = (c: Column) => c.normalized_name || c.name;

  async function submit() {
    setBusy(true);
    // Belt and braces: never send a column that isn't in the chosen sheet.
    const pick = (val: string, list: Column[]) =>
      (list.some((c) => colName(c) === val) ? val : colName(list[0]));
    try {
      await api.post(`/datasets/${datasetId}/relationships`, {
        from_sheet: multi ? (fromSheet || items[0]?.name) : undefined,
        from_column: pick(fromCol, fromCols),
        to_sheet: multi ? (toSheet || items[0]?.name) : undefined,
        to_column: pick(toCol, toCols),
        confirmed,
      });
      toast({ kind: "good", title: "Relationship declared" });
      onDone();
    } catch (e) {
      toast({ kind: "error", title: "Declare failed", msg: apiMsg(e) });
    } finally { setBusy(false); }
  }

  const colSelect = (val: string, set: (s: string) => void, list: Column[]) => (
    <select className="select" value={val || (list[0] ? colName(list[0]) : "")} onChange={(e) => set(e.target.value)} style={{ minWidth: 160 }}>
      {list.map((c) => <option key={colName(c)} value={colName(c)}>{colName(c)}{c.dtype ? ` (${c.dtype})` : ""}</option>)}
    </select>
  );
  const sheetSelect = (val: string, set: (s: string) => void) => (
    <select className="select" value={val || items[0]?.name || ""} onChange={(e) => set(e.target.value)} style={{ minWidth: 160 }}>
      {items.map((s) => <option key={s.sheet_key} value={s.name}>{s.name}</option>)}
    </select>
  );

  return (
    <Modal
      title="Declare relationship"
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || items.length === 0} onClick={submit}>Declare</button>
      </>}
    >
      {sheets.loading ? <Loading /> : items.length === 0 ? (
        <div className="banner info">This dataset's current version has no readable sheets to relate.</div>
      ) : (
        <>
          <div className="secondary small" style={{ marginBottom: 12 }}>
            The owning (from) column references the target (to) column. Manual edges are confirmed unless you clear the box.
          </div>
          <Field label="From (owning side)">
            <div className="row gap-6">
              {/* Changing the sheet must clear the column, or the select shows the
                  new sheet's first column while state still holds the old one —
                  and submit() sends the stale name. */}
              {multi && sheetSelect(fromSheet, (s) => { setFromSheet(s); setFromCol(""); })}
              {colSelect(fromCol, setFromCol, fromCols)}
            </div>
          </Field>
          <div className="center small muted" style={{ margin: "6px 0" }}>references ↓</div>
          <Field label="To (target side)">
            <div className="row gap-6">
              {multi && sheetSelect(toSheet, (s) => { setToSheet(s); setToCol(""); })}
              {colSelect(toCol, setToCol, toCols)}
            </div>
          </Field>
          <label className="row gap-6 small mt-16" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            Mark as confirmed (ready to join)
          </label>
          {!multi && <div className="small muted mt-8">Single-sheet version — relating columns within one sheet.</div>}
        </>
      )}
    </Modal>
  );
}

/* ---- Evidence detail ---- */
function EvidenceModal({ rel, onClose }: { rel: Relationship; onClose: () => void }) {
  const entries = Object.entries(rel.evidence || {});
  return (
    <Modal title={<h3>Evidence · <span className="mono">{rel.from_column} → {rel.to_column}</span></h3>} onClose={onClose}>
      <div className="row row-wrap gap-6" style={{ marginBottom: 12 }}>
        <Badge kind={STATUS_KIND[rel.status]}>{rel.status}</Badge>
        <Badge kind="neutral">{rel.method}</Badge>
        {rel.confidence != null && <Badge kind="accent">confidence {pctFromFraction(rel.confidence)}</Badge>}
      </div>
      {entries.length === 0 ? (
        <div className="muted small">No evidence recorded for this edge.</div>
      ) : (
        <dl className="kv">
          {entries.map(([k, val]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k.replace(/_/g, " ")}</dt>
              <dd className="wrap-anywhere">{val === null ? "—" : typeof val === "number" ? fmtNum(val) : typeof val === "object" ? JSON.stringify(val) : String(val)}</dd>
            </div>
          ))}
        </dl>
      )}
      {rel.reviewed_by && <div className="small muted mt-16">Reviewed by {rel.reviewed_by}</div>}
    </Modal>
  );
}

/* ---- Join builder: preview → execute → publish ---- */
function JoinModal({ rel, onClose }: { rel: Relationship; onClose: () => void }) {
  const toast = useToast();
  const [how, setHow] = useState<"inner" | "left">("inner");
  const [preview, setPreview] = useState<JoinPreview | null>(null);
  const [run, setRun] = useState<JoinExecuteResponse | null>(null);
  const [phase, setPhase] = useState<"idle" | "previewing" | "executing" | "publishing">("idle");
  const [mode, setMode] = useState<"new_dataset" | "new_version">("new_dataset");
  const [name, setName] = useState("");

  const joinable = rel.status === "confirmed";

  async function doPreview() {
    setPhase("previewing"); setRun(null);
    try {
      const p = await api.post<JoinPreview>(`/joins/preview`, { relationship_id: rel.id, how });
      setPreview(p);
    } catch (e) { toast({ kind: "error", title: "Preview failed", msg: apiMsg(e) }); }
    finally { setPhase("idle"); }
  }
  async function doExecute() {
    setPhase("executing");
    try {
      const r = await api.post<JoinExecuteResponse>(`/joins/execute`, { relationship_id: rel.id, how });
      setRun(r);
      toast({ kind: "good", title: "Join executed", msg: `${fmtNum(r.row_count)} rows produced.` });
    } catch (e) { toast({ kind: "error", title: "Execute failed", msg: apiMsg(e) }); }
    finally { setPhase("idle"); }
  }
  async function doPublish() {
    if (!run) return;
    setPhase("publishing");
    try {
      const r = await api.post<PublishResponse>(`/joins/${run.run_id}/publish`, { mode, name: name || undefined });
      toast({ kind: "good", title: "Published", msg: `${r.dataset_name} (v${r.version_number}, ${r.mode})` });
      onClose();
    } catch (e) { toast({ kind: "error", title: "Publish failed", msg: apiMsg(e) }); }
    finally { setPhase("idle"); }
  }

  const w = preview?.warnings;
  return (
    <Modal
      wide
      title={<h3>Join · <span className="mono">{rel.from_column} → {rel.to_column}</span></h3>}
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Close</button>
        <button className="btn" disabled={!joinable || phase !== "idle"} onClick={doPreview}>{preview ? "Re-preview" : "Preview"}</button>
        <button className="btn btn-primary" disabled={!joinable || !preview || phase !== "idle"} onClick={doExecute}>Execute join</button>
      </>}
    >
      {!joinable && (
        <div className="banner error">Only a confirmed relationship can drive a join. Confirm this suggested edge first.</div>
      )}
      <div className="row row-wrap gap-6" style={{ margin: "8px 0 16px" }}>
        <Field label="Join type">
          <select className="select" value={how} onChange={(e) => { setHow(e.target.value as "inner" | "left"); setPreview(null); setRun(null); }} style={{ minWidth: 120 }}>
            <option value="inner">inner</option>
            <option value="left">left</option>
          </select>
        </Field>
        <div className="small muted" style={{ alignSelf: "flex-end", paddingBottom: 8 }}>
          Preview forecasts the result before anything is written.
        </div>
      </div>

      {phase === "previewing" ? <Loading label="Forecasting join…" /> : w && (
        <>
          <div className="grid grid-4" style={{ marginBottom: 12 }}>
            <StatTile label="Est. output rows" value={fmtNum(w.estimated_output_rows)} sub={`${fmtNum(w.left_rows)} × ${fmtNum(w.right_rows)} in`} />
            <StatTile label="Fan-out factor" value={`${w.row_expansion_factor.toFixed(2)}×`} sub="output per left row" />
            <StatTile label="Unmatched left" value={pctFromPercent(w.unmatched_left_pct)} />
            <StatTile label="Unmatched right" value={pctFromPercent(w.unmatched_right_pct)} />
          </div>
          <div className="row row-wrap gap-6" style={{ marginBottom: 12 }}>
            {w.many_to_many && <Badge kind="critical">many-to-many — output grows multiplicatively</Badge>}
            {w.left_duplicate_keys > 0 && <Badge kind="warning">{fmtNum(w.left_duplicate_keys)} dup keys left</Badge>}
            {w.right_duplicate_keys > 0 && <Badge kind="warning">{fmtNum(w.right_duplicate_keys)} dup keys right</Badge>}
            {w.column_collisions.length > 0 && <Badge kind="warning">collisions: {w.column_collisions.join(", ")}</Badge>}
            {!w.many_to_many && w.column_collisions.length === 0
              && w.left_duplicate_keys === 0 && w.right_duplicate_keys === 0
              && <Badge kind="good">clean join</Badge>}
          </div>
          {preview!.preview.length > 0 && (
            <div className="card" style={{ marginBottom: 4 }}>
              <div className="table-wrap" style={{ maxHeight: 240 }}>
                <table className="data">
                  <thead><tr>{preview!.output_columns.map((c) => <th scope="col" key={c} className="mono small">{c}</th>)}</tr></thead>
                  <tbody>
                    {preview!.preview.map((row, i) => (
                      <tr key={i}>{preview!.output_columns.map((c) => <td key={c} className="small">{row[c] == null ? "—" : String(row[c])}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <div className="small muted">Sample only. Execute writes a join_output artifact (needs write access).</div>
        </>
      )}

      {run && (
        <div className="card card-pad mt-16">
          <div className="row"><strong className="small">Executed · {fmtNum(run.row_count)} rows</strong><span className="spacer" /><span className="small muted mono">run {run.run_id.slice(0, 8)}</span></div>
          <div className="row row-wrap gap-6 mt-16">
            <Field label="Publish as">
              <select className="select" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} style={{ minWidth: 150 }}>
                <option value="new_dataset">New dataset</option>
                <option value="new_version">New version (left side)</option>
              </select>
            </Field>
            {mode === "new_dataset" && (
              <Field label="Name (optional)">
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="joined dataset" style={{ minWidth: 200 }} />
              </Field>
            )}
            <button className={cx("btn", "btn-primary")} style={{ alignSelf: "flex-end" }} disabled={phase !== "idle"} onClick={doPublish}>
              {phase === "publishing" ? "Publishing…" : "Publish"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
