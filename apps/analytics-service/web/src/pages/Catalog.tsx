import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Page } from "../api/client";
import { AsyncView, Badge, EmptyState, StatTile, fmtBytes, fmtNum, statusKind, useAsync } from "../components/ui";
import { useIdentity } from "../app/identity";

export interface DatasetInfo {
  id: string; name: string; description?: string | null; domain?: string | null;
  classification?: string | null; deprecated?: boolean; is_favorite?: boolean;
  current_version?: number | null; row_count?: number | null; size_bytes?: number | null;
  validation_status?: string | null; has_schema_drift?: boolean; documentation?: string | null;
  updated_at?: string | null;
}

const PAGE_SIZE = 100;

export function Catalog() {
  const { identity } = useIdentity();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [docFilter, setDocFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const state = useAsync(
    () => api.get<Page<DatasetInfo>>("/datasets", {
      q: q || undefined, documentation: docFilter || undefined, limit: PAGE_SIZE, offset,
    }),
    [q, docFilter, offset, identity.userId, identity.teamId],
  );

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Catalog</h1>
          <div className="page-sub">Every dataset you can see, with health and documentation at a glance.</div>
        </div>
        <div className="spacer" />
        <Link to="/upload" className="btn btn-primary">↑ Upload dataset</Link>
      </div>

      <div className="row row-wrap" style={{ marginBottom: 18 }}>
        <input className="input" style={{ maxWidth: 320 }} aria-label="Search datasets"
          placeholder="Search datasets…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" style={{ maxWidth: 200 }} aria-label="Filter by documentation coverage"
          value={docFilter} onChange={(e) => setDocFilter(e.target.value)}>
          <option value="">All documentation</option>
          <option value="full">Fully documented</option>
          <option value="partial">Partly documented</option>
          <option value="none">Undocumented</option>
        </select>
      </div>

      <AsyncView state={state} empty={<EmptyState icon="◇" title="No datasets yet" hint="Upload a CSV or Excel workbook to get started." action={<Link to="/upload" className="btn btn-primary">Upload dataset</Link>} />}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="◇" title="No datasets match" hint="Try a different search or filter." />
        ) : (
          <>
            {/* The three right-hand tiles sum only what this page holds, so they
                say so when the result set spans more than one page — otherwise
                "250 datasets" sat beside totals computed from just 100 rows. */}
            <div className="grid grid-4" style={{ marginBottom: 20 }}>
              <StatTile label="Datasets" value={fmtNum(page.total)}
                sub={page.total > page.items.length ? `showing ${fmtNum(page.items.length)} on this page` : undefined} />
              <StatTile label={page.total > page.items.length ? "Rows (this page)" : "Total rows"}
                value={fmtNum(page.items.reduce((a, d) => a + (d.row_count || 0), 0))} />
              <StatTile label={page.total > page.items.length ? "Storage (this page)" : "Storage"}
                value={fmtBytes(page.items.reduce((a, d) => a + (d.size_bytes || 0), 0))} />
              <StatTile label={page.total > page.items.length ? "With drift (page)" : "With drift"}
                value={fmtNum(page.items.filter((d) => d.has_schema_drift).length)} />
            </div>
            <div className="card">
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr><th scope="col">Name</th><th scope="col">Domain</th><th scope="col">Validation</th><th scope="col">Docs</th><th scope="col" className="num">Rows</th><th scope="col" className="num">Size</th><th scope="col" className="num">Ver</th><th scope="col">Updated</th></tr>
                  </thead>
                  <tbody>
                    {page.items.map((d) => (
                      <tr key={d.id} className="clickable" onClick={() => nav(`/datasets/${d.id}`)}>
                        <td>
                          <Link to={`/datasets/${d.id}`} onClick={(e) => e.stopPropagation()} style={{ fontWeight: 600 }}>{d.name}</Link>
                          {d.deprecated && <Badge kind="neutral">deprecated</Badge>}
                          {d.description && <div className="small muted" style={{ marginTop: 2 }}>{d.description}</div>}
                        </td>
                        <td className="secondary">{d.domain || "—"}</td>
                        <td>{d.validation_status ? <Badge kind={statusKind(d.validation_status)}>{d.validation_status}</Badge> : <span className="muted">—</span>}</td>
                        <td><DocBadge doc={d.documentation} /></td>
                        <td className="num">{fmtNum(d.row_count)}</td>
                        <td className="num">{fmtBytes(d.size_bytes)}</td>
                        <td className="num">{d.current_version ?? "—"}</td>
                        <td className="small secondary">{d.updated_at ? new Date(d.updated_at).toLocaleDateString() : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            {page.total > PAGE_SIZE && (
              <div className="row mt-16">
                <button className="btn btn-sm" disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>← Prev</button>
                <button className="btn btn-sm" disabled={offset + page.items.length >= page.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}>Next →</button>
                <span className="spacer" />
                <span className="small muted">
                  {fmtNum(offset + 1)}–{fmtNum(offset + page.items.length)} of {fmtNum(page.total)}
                </span>
              </div>
            )}
          </>
        )}
      </AsyncView>
    </div>
  );
}

function DocBadge({ doc }: { doc?: string | null }) {
  if (!doc) return <span className="muted">—</span>;
  const kind = doc === "full" ? "good" : doc === "partial" ? "warning" : "neutral";
  return <Badge kind={kind}>{doc}</Badge>;
}
