import { useState } from "react";
import { api } from "../../api/client";
import {
  AsyncView, Badge, Card, StatTile, fmtBytes, fmtNum, useAsync, useToast,
} from "../../components/ui";
import { seriesColor } from "../../components/charts";
import { useIdentity } from "../../app/identity";
import { ConfirmModal, errText } from "./common";

interface Usage { total_bytes: number; datasets_bytes: number; samples_bytes: number; exports_bytes: number; uploads_bytes: number; }
interface RetentionRule { artifact_type: string; retention_days: number | null; }
interface Retention { rules: RetentionRule[]; orphan_grace_hours: number; expired_pending: number; }
interface GcResult { expired_deleted: number; orphans_deleted: number; bytes_freed: number; by_type: Record<string, number>; more_remaining: boolean; }

export function StorageTab() {
  const { identity } = useIdentity();
  const toast = useToast();
  const deps = [identity.userId, identity.teamId];
  const usage = useAsync(() => api.get<Usage>("/storage/usage"), deps);
  const retention = useAsync(() => api.get<Retention>("/storage/retention"), deps);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function runGc() {
    setBusy(true);
    try {
      const r = await api.post<GcResult>("/storage/gc");
      const freed = fmtBytes(r.bytes_freed);
      toast({
        kind: "good",
        title: "Garbage collection complete",
        msg: `Reclaimed ${freed} — ${r.expired_deleted} expired, ${r.orphans_deleted} orphaned artifact(s) deleted${r.more_remaining ? " · more remaining, run again" : ""}.`,
      });
      setConfirming(false);
      usage.reload();
      retention.reload();
    } catch (e) {
      toast({ kind: "error", title: "Garbage collection failed", msg: errText(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="col" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <AsyncView state={usage}>
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
              <div className="grid grid-4">
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

      <AsyncView state={retention}>
        {(r) => (
          <Card
            title="Retention policy"
            actions={<button className="btn btn-primary" onClick={() => setConfirming(true)}>Run cleanup now</button>}
            pad={false}
          >
            <div className="card-pad row row-wrap" style={{ gap: 24, borderBottom: "1px solid var(--border)" }}>
              <div><div className="stat-label">Orphan grace</div><div style={{ fontWeight: 600 }}>{r.orphan_grace_hours} h</div></div>
              <div>
                <div className="stat-label">Awaiting sweep</div>
                <div style={{ fontWeight: 600 }}>
                  {r.expired_pending > 0 ? <Badge kind="warning">{fmtNum(r.expired_pending)} expired</Badge> : <Badge kind="good">none</Badge>}
                </div>
              </div>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th scope="col">Artifact type</th><th scope="col" className="num">Retention</th></tr></thead>
                <tbody>
                  {r.rules.map((rule) => (
                    <tr key={rule.artifact_type}>
                      <td>{rule.artifact_type}</td>
                      <td className="num">{rule.retention_days == null ? <Badge kind="accent">kept indefinitely</Badge> : `${rule.retention_days} days`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </AsyncView>

      {confirming && (
        <ConfirmModal
          title="Run garbage collection"
          confirmLabel="Run cleanup"
          busy={busy}
          onConfirm={runGc}
          onClose={() => setConfirming(false)}
          body={
            <>
              This deletes artifacts past their retention deadline and reclaims orphaned blobs across every team.
              The sweep is bounded per run{retention.data?.expired_pending ? <> — <strong>{fmtNum(retention.data.expired_pending)}</strong> item(s) are currently due</> : ""}. Continue?
            </>
          }
        />
      )}
    </div>
  );
}
