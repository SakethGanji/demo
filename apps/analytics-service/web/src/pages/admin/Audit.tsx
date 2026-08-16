import { useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Page } from "../../api/client";
import { Badge, Card, EmptyState, ErrorBanner, Loading, fmtDate, fmtNum, useAsync } from "../../components/ui";
import { useIdentity } from "../../app/identity";

type BadgeKind = "good" | "warning" | "serious" | "critical" | "accent" | "neutral";

interface AuditEntry {
  id: number; occurred_at: string; actor_user_id?: string | null; actor_email?: string | null;
  team_id?: string | null; action: string; method: string; path: string; status_code: number;
  resource_type?: string | null; resource_id?: string | null; duration_ms?: number | null;
}

const PAGE = 25;

function codeKind(code: number): BadgeKind {
  if (code >= 500) return "critical";
  if (code >= 400) return "warning";
  if (code >= 200 && code < 300) return "good";
  return "neutral";
}

export function AuditTab() {
  const { identity } = useIdentity();
  const [offset, setOffset] = useState(0);
  const state = useAsync(
    () => api.get<Page<AuditEntry>>("/audit", { limit: PAGE, offset }),
    [offset, identity.userId, identity.teamId],
  );

  if (state.loading && state.data === null) return <Loading />;
  if (state.error) {
    if (state.error instanceof ApiError && state.error.status === 403) {
      return <EmptyState icon="🔒" title="Restricted"
        hint="The audit trail is available to platform administrators only. Switch to a superuser identity to view it." />;
    }
    return <ErrorBanner error={state.error} />;
  }
  const page = state.data;
  if (!page) return <EmptyState icon="📜" title="No audit entries" />;

  const from = page.total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE, page.total);

  return (
    <Card
      title="Audit trail"
      actions={
        <div className="row gap-6" style={{ alignItems: "center" }}>
          <span className="small muted">{from}–{to} of {fmtNum(page.total)}</span>
          <button className="btn" disabled={offset === 0 || state.loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>‹ Newer</button>
          <button className="btn" disabled={to >= page.total || state.loading} onClick={() => setOffset(offset + PAGE)}>Older ›</button>
        </div>
      }
      pad={false}
    >
      {page.items.length === 0 ? (
        <EmptyState icon="📜" title="No audit entries" />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th scope="col">When</th><th scope="col">Actor</th><th scope="col">Action</th><th scope="col" className="num">Status</th><th scope="col">Resource</th><th scope="col" className="num">Duration</th></tr>
            </thead>
            <tbody>
              {page.items.map((e) => (
                <tr key={e.id}>
                  <td className="small secondary" style={{ whiteSpace: "nowrap" }}>{fmtDate(e.occurred_at)}</td>
                  <td className="small">{e.actor_email || <span className="muted">—</span>}</td>
                  <td className="small wrap-anywhere"><span className="badge">{e.method}</span> {e.path}</td>
                  <td className="num"><Badge kind={codeKind(e.status_code)}>{e.status_code}</Badge></td>
                  <td className="small secondary">{e.resource_type ? `${e.resource_type}${e.resource_id ? ` · ${e.resource_id.slice(0, 8)}` : ""}` : "—"}</td>
                  <td className="num small">{e.duration_ms != null ? `${e.duration_ms} ms` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
