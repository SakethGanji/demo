import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Play, Save } from 'lucide-react';
import { smokeApi } from '@/shared/lib/api';
import { qk } from '@/shared/api/queryKeys';
import { Button } from '@/shared/components/ui/button';
import { Badge } from '@/shared/components/ui/badge';
import { Checkbox } from '@/shared/components/ui/checkbox';
import { Input } from '@/shared/components/ui/input';
import { Switch } from '@/shared/components/ui/switch';
import { useWorkflowStore } from '../../stores/workflowStore';
import { runSmoke, saveSmoke } from '../../lib/testAiActions';
import { EMPTY_SMOKE, smokeBody, smokeForm, toggleIn, type SmokeConfig, type SmokeRun } from '../../lib/testsAi';

function CheckRow({ id, checked, onChange, label }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex min-w-0 items-center gap-2 text-[12.5px] text-foreground">
      <Checkbox checked={checked} onCheckedChange={(v) => onChange(!!v)} data-testid={id} aria-label={label} />
      <span className="truncate" title={label}>{label}</span>
    </label>
  );
}

function runTone(status: SmokeRun['status']) {
  return status === 'passed' ? 'success' : 'error';
}

function RunRow({ run }: { run: SmokeRun }) {
  const bad = run.results.filter((r) => r.status !== 'passed');
  return (
    <div className="flex flex-col gap-0.5 rounded-ctl px-2 py-1 text-[12.5px]" data-testid="smoke-run-row" data-status={run.status} data-trigger={run.trigger}>
      <div className="flex items-center gap-2">
        <Badge variant={runTone(run.status)}>{run.status === 'passed' ? 'Passed' : run.status === 'failed' ? 'Failed' : 'Error'}</Badge>
        <span className="text-muted-foreground">{run.trigger === 'manual' ? 'Run by hand' : run.trigger === 'schedule' ? 'Scheduled' : 'Before release'}</span>
        {run.started_at && <span className="text-faint">{new Date(run.started_at).toLocaleString()}</span>}
        <span className="text-muted-foreground">{run.results.length - bad.length}/{run.results.length} tests passed</span>
      </div>
      {bad.map((r) => (
        <div key={r.test_id} className="pl-2 text-[12px] text-muted-foreground">
          <span className="font-medium text-foreground">{r.name}</span>: {r.failures?.[0]?.message ?? r.status}
        </div>
      ))}
    </div>
  );
}

/** Live checks: run chosen tests against the real system for the chosen steps (structure only). */
export default function SmokeSettings() {
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const tests = useWorkflowStore((s) => s.tests);
  const nodes = useWorkflowStore((s) => s.nodes);
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: qk.smokeConfig(workflowId ?? ''), queryFn: () => smokeApi.get(workflowId!), enabled: !!workflowId });
  const runs = useQuery({ queryKey: qk.smokeRuns(workflowId ?? ''), queryFn: () => smokeApi.runs(workflowId!, 10), enabled: !!workflowId });
  // what the user changed on top of the stored settings (null = nothing yet)
  const [edits, setEdits] = useState<Partial<SmokeConfig> | null>(null);
  const form: SmokeConfig = { ...smokeForm(cfg.data ?? EMPTY_SMOKE), ...edits };
  const [busy, setBusy] = useState<'save' | 'run' | null>(null);

  const nodeNames = useMemo(
    () => nodes.map((n) => (n.data as { name?: string }).name).filter((n): n is string => !!n),
    [nodes],
  );
  const testIds = useMemo(() => tests.map((t) => t.id), [tests]);
  const patch = (p: Partial<SmokeConfig>) => setEdits((e) => ({ ...e, ...p }));

  if (!workflowId) return null;

  const save = async () => {
    setBusy('save');
    const saved = await saveSmoke(workflowId, smokeBody(form, testIds, nodeNames));
    if (saved) {
      qc.setQueryData(qk.smokeConfig(workflowId), saved);
      setEdits(null);
    }
    setBusy(null);
    return saved;
  };

  const run = async () => {
    setBusy('run');
    // The run uses the saved settings, so save what is on screen first.
    const saved = await save();
    setBusy('run');
    if (saved) {
      const r = await runSmoke(workflowId);
      if (r) await qc.invalidateQueries({ queryKey: qk.smokeRuns(workflowId) });
    }
    setBusy(null);
  };

  return (
    <section className="mt-2 flex flex-col gap-2 rounded-panel bg-recess p-2.5" data-testid="smoke-settings">
      <div className="flex items-center gap-3">
        <span className="text-[12.5px] font-medium text-foreground">Live checks</span>
        <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          <Switch checked={form.enabled} onCheckedChange={(v) => patch({ enabled: v })} data-testid="smoke-enabled" aria-label="Live checks on" />
          {form.enabled ? 'On' : 'Off'}
        </label>
      </div>
      <p className="text-[12px] text-muted-foreground">
        Runs the chosen tests against the real system for the chosen steps. Only the shape of the reply is checked, because live data changes.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-[12px] font-medium text-muted-foreground">Tests to run</span>
          {tests.length === 0 && <span className="text-[12px] text-faint">No tests yet</span>}
          {tests.map((t) => (
            <CheckRow
              key={t.id}
              id={`smoke-test-${t.id}`}
              label={t.name}
              checked={form.test_ids.includes(t.id)}
              onChange={(v) => patch({ test_ids: toggleIn(form.test_ids, t.id, v) })}
            />
          ))}
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-[12px] font-medium text-muted-foreground">Steps that call the real system</span>
          {nodeNames.map((name) => (
            <CheckRow
              key={name}
              id={`smoke-live-${name}`}
              label={name}
              checked={form.live_nodes.includes(name)}
              onChange={(v) => patch({ live_nodes: toggleIn(form.live_nodes, name, v) })}
            />
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          Schedule
          <Input
            value={form.schedule ?? ''}
            onChange={(e) => patch({ schedule: e.target.value })}
            placeholder="0 * * * *"
            className="h-7 w-36 font-mono"
            data-testid="smoke-schedule"
            aria-label="Schedule (cron)"
          />
        </label>
        <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          <Checkbox
            checked={form.before_release}
            onCheckedChange={(v) => patch({ before_release: !!v })}
            data-testid="smoke-before-release"
            aria-label="Check before every release"
          />
          Check before every release
        </label>
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => void save()} disabled={busy !== null} data-testid="smoke-save">
          {busy === 'save' ? <Loader2 className="animate-spin" /> : <Save />} Save
        </Button>
        <Button size="sm" onClick={() => void run()} disabled={busy !== null} data-testid="smoke-run">
          {busy === 'run' ? <Loader2 className="animate-spin" /> : <Play />} Run now
        </Button>
      </div>

      {(runs.data ?? []).map((r) => <RunRow key={r.id} run={r} />)}
    </section>
  );
}
