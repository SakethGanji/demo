import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { qk } from '@/shared/api/queryKeys';
import { Button } from '@/shared/components/ui/button';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useDrift } from '../../hooks/useDrift';
import { resolveDrift } from '../../lib/testAiActions';
import { openNodeByName } from '../../lib/testActions';

/** Shown in the editor while a workflow has open drift: a real reply no longer matches what it expected. */
export default function DriftBanner() {
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const { data } = useDrift();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const alerts = data?.alerts ?? [];
  const alert = alerts[0];
  if (!workflowId || !alert) return null;

  const resolve = async () => {
    setBusy(true);
    const ok = await resolveDrift(workflowId, alert.id);
    setBusy(false);
    if (ok) {
      setOpen(false);
      await qc.invalidateQueries({ queryKey: qk.workflowDrift(workflowId) });
    }
  };

  return (
    <div
      className="glass-overlay flex max-w-[calc(100%-24px)] flex-col gap-1.5 rounded-panel py-2 pr-2 pl-3 text-[12.5px]"
      data-testid="drift-banner"
      data-count={alerts.length}
    >
      <div className="flex items-center gap-3">
        <AlertTriangle size={14} className="shrink-0 text-warning-text" aria-hidden />
        <span className="min-w-0 truncate" data-testid="drift-title">
          {alert.title}
          {alerts.length > 1 && <span className="text-muted-foreground"> and {alerts.length - 1} more</span>}
        </span>
        <Button variant="outline" size="xs" onClick={() => setOpen((v) => !v)} aria-expanded={open} data-testid="drift-details">
          {open ? 'Hide details' : 'Details'}
        </Button>
        <Button variant="secondary" size="xs" onClick={() => void resolve()} disabled={busy} data-testid="drift-resolve">
          Resolve
        </Button>
      </div>
      {open && (
        <div className="flex flex-col gap-1 pr-1 text-muted-foreground" data-testid="drift-detail-body">
          <div>
            <button type="button" className="font-medium text-foreground hover:underline" onClick={() => openNodeByName(alert.node_name)}>
              {alert.node_name}
            </button>
            {' '}replied differently than expected ({alert.basis}). Tests that mock it may be out of date.
          </div>
          <ul className="list-disc pl-4">
            {alert.diffs.map((d, i) => (
              <li key={`${d.path}-${i}`} className="font-mono text-[11.5px]">
                {d.path}: {d.kind}
              </li>
            ))}
          </ul>
          {alert.summary && <div className="text-[12px]">{alert.summary}</div>}
        </div>
      )}
    </div>
  );
}
