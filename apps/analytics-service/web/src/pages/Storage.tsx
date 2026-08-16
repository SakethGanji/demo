import { api } from "../api/client";
import { AsyncView, Card, StatTile, fmtBytes, useAsync } from "../components/ui";
import { seriesColor } from "../components/charts";
import { useIdentity } from "../app/identity";

interface Usage { total_bytes: number; datasets_bytes: number; samples_bytes: number; exports_bytes: number; uploads_bytes: number; }

export function Storage() {
  const { identity } = useIdentity();
  const state = useAsync(() => api.get<Usage>("/storage/usage"), [identity.userId, identity.teamId]);
  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Storage</h1>
          <div className="page-sub">Bytes held across datasets and derived artifacts, by kind.</div>
        </div>
      </div>
      <AsyncView state={state}>
        {(u) => {
          const parts = [
            { label: "Datasets", val: u.datasets_bytes },
            { label: "Samples", val: u.samples_bytes },
            { label: "Exports", val: u.exports_bytes },
            { label: "Uploads (staging)", val: u.uploads_bytes },
          ];
          const total = u.total_bytes || 1;
          return (
            <>
              <div className="grid grid-4" style={{ marginBottom: 20 }}>
                <StatTile label="Total" value={fmtBytes(u.total_bytes)} />
                {parts.slice(0, 3).map((p) => <StatTile key={p.label} label={p.label} value={fmtBytes(p.val)} />)}
              </div>
              <Card title="Breakdown by kind">
                <div style={{ display: "flex", height: 14, borderRadius: 999, overflow: "hidden", gap: 2, background: "var(--surface-2)" }}>
                  {parts.map((p, i) => p.val > 0 && (
                    <div key={p.label} title={`${p.label}: ${fmtBytes(p.val)}`} style={{ width: `${(p.val / total) * 100}%`, background: seriesColor(i) }} />
                  ))}
                </div>
                <div className="chart-legend">
                  {parts.map((p, i) => (
                    <span className="legend-item" key={p.label}><span className="legend-swatch" style={{ background: seriesColor(i) }} /> {p.label} · {fmtBytes(p.val)}</span>
                  ))}
                </div>
              </Card>
            </>
          );
        }}
      </AsyncView>
    </div>
  );
}
