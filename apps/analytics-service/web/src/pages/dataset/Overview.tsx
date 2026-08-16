import { api } from "../../api/client";
import { AsyncView, Badge, Card, StatTile, fmtBytes, fmtNum, statusKind, useAsync } from "../../components/ui";
import { useIdentity } from "../../app/identity";
import type { DatasetTabProps } from "../DatasetDetail";

interface Dimension { status?: string; summary?: string; evidence?: Record<string, unknown>; }
interface Health { dataset_id: string; current_version_number?: number | null; dimensions: Record<string, Dimension>; }

export function Overview({ dataset }: DatasetTabProps) {
  const { identity } = useIdentity();
  const state = useAsync(() => api.get<Health>(`/datasets/${dataset.id}/health`), [dataset.id, identity.userId, identity.teamId]);
  return (
    <div>
      <div className="grid grid-4" style={{ marginBottom: 20 }}>
        <StatTile label="Rows" value={fmtNum(dataset.row_count)} />
        <StatTile label="Size" value={fmtBytes(dataset.size_bytes)} />
        <StatTile label="Version" value={dataset.current_version ?? "—"} />
        <StatTile label="Validation" value={dataset.validation_status ? <Badge kind={statusKind(dataset.validation_status)}>{dataset.validation_status}</Badge> : "—"} />
      </div>

      <h2 style={{ marginBottom: 12 }}>Health scorecard</h2>
      <AsyncView state={state}>
        {(h) => {
          const dims = Object.entries(h.dimensions || {});
          if (!dims.length) return <Card><div className="muted">No health signals yet — run profiling on the Explore tab.</div></Card>;
          return (
            <div className="grid grid-3">
              {dims.map(([name, dim]) => (
                <Card key={name}>
                  <div className="row">
                    <h3 style={{ flex: 1, textTransform: "capitalize" }}>{name.replace(/_/g, " ")}</h3>
                    <Badge kind={statusKind(dim.status)}>{dim.status || "—"}</Badge>
                  </div>
                  <div className="secondary small mt-8">{dim.summary || "No summary."}</div>
                  {dim.evidence && (
                    <dl className="kv mt-16">
                      {Object.entries(dim.evidence).slice(0, 5).map(([k, v]) => (
                        <div key={k} style={{ display: "contents" }}>
                          <dt>{k.replace(/_/g, " ")}</dt>
                          <dd>{typeof v === "number" ? fmtNum(v) : String(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </Card>
              ))}
            </div>
          );
        }}
      </AsyncView>
    </div>
  );
}
