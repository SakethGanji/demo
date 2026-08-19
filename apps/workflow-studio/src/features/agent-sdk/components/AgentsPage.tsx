/**
 * /agents — the fleet, its runs, and each run's evidence.
 *
 * Left: agents (role verb is DERIVED from bound tools, never declared).
 * Center: trigger form + runs table, polling while any run is live.
 * Right: the selected run — status, error, response, and the event stream
 * (agent:thinking / tool_call / tool_result / …) via after_seq polling; live
 * and replay are the same component.
 *
 * A failed run renders its error verbatim — with no LLM credit an agent run
 * fails fast with an auth error, and that truth belongs on screen.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import WorkflowSVG from '@/features/workflow-editor/components/WorkflowSVG';
import { definitionToPreviewData } from '@/features/workflow-editor/lib/workflowTransform';
import { BuildAttemptView, type BuildAttempt } from './BuildAttemptView';
import {
  agentsApi,
  modelsApi,
  workflowsApi,
  type AgentRunEvent,
  type AgentRunListItem,
} from '@/shared/lib/api';

const LIVE = new Set(['queued', 'running', 'waiting']);

const STATUS_COLOR: Record<string, string> = {
  success: 'var(--st-good)',
  failed: 'var(--st-crit)',
  cancelled: 'var(--st-warn)',
};

function StatusWord({ status }: { status: string }) {
  return (
    <span
      className="font-mono text-footnote"
      style={STATUS_COLOR[status] ? { color: STATUS_COLOR[status] } : undefined}
    >
      {status}
    </span>
  );
}

function eventSummary(e: AgentRunEvent): string {
  const p = e.payload as Record<string, unknown>;
  switch (e.type) {
    case 'agent:thinking':
      return String(p.content ?? '');
    case 'agent:plan':
      return String(p.plan ?? '');
    case 'agent:reflect':
      return String(p.reflection ?? '');
    case 'agent:tool_call':
      return `${String(p.tool)}(${JSON.stringify(p.arguments ?? {})})`;
    case 'agent:tool_result':
      return p.is_error
        ? `error: ${JSON.stringify(p.result)}`
        : JSON.stringify(p.result);
    case 'agent:response':
      return String(p.content ?? '');
    case 'agent:output_validation':
      return `schema ${String(p.status)}`;
    default:
      return JSON.stringify(p);
  }
}

/** Every build_workflow attempt in a run's event stream: the script from the
 * tool_call, the outcome from the matching tool_result (paired by id). */
function buildAttempts(events: AgentRunEvent[] | undefined): BuildAttempt[] {
  const attempts: Array<BuildAttempt & { callId?: string }> = [];
  const byCallId = new Map<string, BuildAttempt & { callId?: string }>();
  for (const e of events ?? []) {
    const p = e.payload as Record<string, unknown>;
    if (e.type === 'agent:tool_call') {
      const args = p.arguments as Record<string, unknown> | undefined;
      if (args && typeof args.script === 'string') {
        const attempt = {
          seq: e.seq,
          iteration: Number(p.iteration ?? 0),
          script: args.script,
          name: typeof args.name === 'string' ? args.name : undefined,
          result: null,
          callId: typeof p.id === 'string' ? p.id : undefined,
        };
        attempts.push(attempt);
        if (attempt.callId) byCallId.set(attempt.callId, attempt);
      }
    } else if (e.type === 'agent:tool_result' && typeof p.id === 'string') {
      const attempt = byCallId.get(p.id);
      if (!attempt) continue;
      let result: unknown = p.result;
      if (typeof result === 'string') {
        try {
          result = JSON.parse(result);
        } catch {
          continue;
        }
      }
      if (result && typeof result === 'object') {
        attempt.result = result as BuildAttempt['result'];
      }
    }
  }
  return attempts;
}

/** Workflow ids the run's build_workflow tool calls persisted, from the
 * event stream — the artifact trail that links an agent run to the canvas. */
function producedWorkflowIds(events: AgentRunEvent[] | undefined): string[] {
  const ids: string[] = [];
  for (const e of events ?? []) {
    if (e.type !== 'agent:tool_result') continue;
    const raw = (e.payload as Record<string, unknown>).result;
    let result: unknown = raw;
    if (typeof raw === 'string') {
      try {
        result = JSON.parse(raw);
      } catch {
        continue;
      }
    }
    if (result && typeof result === 'object') {
      const r = result as Record<string, unknown>;
      if (r.persisted === true && typeof r.workflow_id === 'string') {
        ids.push(r.workflow_id);
      }
    }
  }
  return [...new Set(ids)];
}

/** Inline preview of a workflow an agent produced, straight off the API. */
function ProducedWorkflow({ workflowId }: { workflowId: string }) {
  const detail = useQuery({
    queryKey: ['workflow-detail', workflowId],
    queryFn: () => workflowsApi.get(workflowId),
  });
  const preview = useMemo(() => {
    const def = detail.data?.definition as
      | { nodes?: unknown[]; connections?: Array<Record<string, unknown>> }
      | undefined;
    if (!def?.nodes?.length) return null;
    // The stored definition uses snake_case connection keys; the preview
    // transform expects camelCase — accept either.
    const definition = {
      nodes: def.nodes,
      connections: (def.connections ?? []).map((c) => ({
        sourceNode: c.sourceNode ?? c.source_node,
        targetNode: c.targetNode ?? c.target_node,
        sourceOutput: c.sourceOutput ?? c.source_output ?? 'main',
        targetInput: c.targetInput ?? c.target_input ?? 'main',
      })),
    };
    return definitionToPreviewData(
      definition as unknown as Parameters<typeof definitionToPreviewData>[0],
    );
  }, [detail.data]);

  if (!preview) return null;
  return (
    <div className="mt-2 rounded border border-border" data-testid="run-produced">
      <div className="flex items-center gap-2 border-b border-border px-2 py-1">
        <span className="text-footnote text-muted-foreground">produced workflow</span>
        <span className="font-mono text-footnote">{detail.data?.name}</span>
        <a
          className="ml-auto text-footnote underline underline-offset-2"
          href={`/editor?workflowId=${workflowId}`}
        >
          open in editor →
        </a>
      </div>
      <div className="h-40">
        <WorkflowSVG
          nodes={preview.nodes}
          edges={preview.edges}
          showIcons
          className="h-full w-full"
        />
      </div>
    </div>
  );
}

export function AgentsPage() {
  const queryClient = useQueryClient();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [openAttemptSeq, setOpenAttemptSeq] = useState<number | null>(null);
  const [agentId, setAgentId] = useState('');
  const [task, setTask] = useState('');
  const [newAgentName, setNewAgentName] = useState('');
  const [model, setModel] = useState('');

  const agents = useQuery({ queryKey: ['agents'], queryFn: agentsApi.list });
  const models = useQuery({ queryKey: ['models'], queryFn: modelsApi.list });

  // Preselect the backend's default (first model with a configured credential),
  // so a created agent runs instead of dead-ending on a keyless model.
  const defaultModel = useMemo(() => {
    const list = models.data ?? [];
    return (list.find((m) => m.default) ?? list.find((m) => m.available) ?? list[0])?.id ?? '';
  }, [models.data]);
  const selectedModel = model || defaultModel;

  const runs = useQuery({
    queryKey: ['agent-runs'],
    queryFn: () => agentsApi.runs({ limit: 50 }),
    refetchInterval: (query) =>
      query.state.data?.some((r: AgentRunListItem) => LIVE.has(r.status)) ? 2500 : false,
  });

  const runDetail = useQuery({
    queryKey: ['agent-run', selectedRunId],
    queryFn: () => agentsApi.run(selectedRunId as string),
    enabled: selectedRunId !== null,
    refetchInterval: (query) => (query.state.data && LIVE.has(query.state.data.status) ? 1500 : false),
  });

  const events = useQuery({
    queryKey: ['agent-run-events', selectedRunId],
    queryFn: () => agentsApi.runEvents(selectedRunId as string),
    enabled: selectedRunId !== null,
    refetchInterval: () =>
      runDetail.data && LIVE.has(runDetail.data.status) ? 1500 : false,
  });

  const createAgent = useMutation({
    mutationFn: () =>
      agentsApi.create({
        name: newAgentName.trim(),
        model: selectedModel,
        description: 'Builds and runs workflows from plain-English tasks.',
        system_prompt:
          'You are a workflow author. Use build_workflow to create workflows, ' +
          'list_workflows to find existing ones, and run_workflow to execute ' +
          'and chain them. Prefer running an existing workflow over rebuilding it.',
        // The full toolkit: build → discover → run → chain.
        tools: [{ source: 'sdk', tool_key: 'build_workflow', enabled: true }],
      }),
    onSuccess: () => {
      setNewAgentName('');
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const trigger = useMutation({
    mutationFn: () => agentsApi.trigger({ agent_id: agentId, task: task.trim() }),
    onSuccess: (run) => {
      setTask('');
      setSelectedRunId(run.id);
      queryClient.invalidateQueries({ queryKey: ['agent-runs'] });
    },
  });

  const cancelRun = useMutation({
    mutationFn: (runId: string) => agentsApi.cancel(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-run', selectedRunId] });
      queryClient.invalidateQueries({ queryKey: ['agent-runs'] });
    },
  });

  return (
    <div className="flex h-[calc(100vh-92px)] min-h-0" data-testid="agents-page">
      {/* Fleet rail */}
      <div className="flex w-64 shrink-0 flex-col border-r border-border">
        <div className="border-b border-border px-4 py-2 text-label font-medium">
          Agents{' '}
          <span className="text-footnote text-muted-foreground">
            {agents.data ? agents.data.length : '—'}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto" data-testid="agents-list">
          {agents.data?.length === 0 && (
            <div className="px-4 py-3 text-small text-muted-foreground">
              No agents yet — create one below.
            </div>
          )}
          {agents.data?.map((a) => (
            <button
              key={a.id}
              type="button"
              data-testid="agent-row"
              onClick={() => setAgentId(a.id)}
              className={`block w-full px-4 py-2 text-left hover:bg-secondary ${
                agentId === a.id ? 'bg-secondary' : ''
              }`}
            >
              <div className="text-small">{a.name}</div>
              <div className="text-footnote text-muted-foreground">
                {a.role} · {a.model} · {String(a.tool_count ?? 0)} tool
                {(a.tool_count ?? 0) === 1 ? '' : 's'}
              </div>
            </button>
          ))}
        </div>
        <div className="border-t border-border p-3">
          <div className="flex gap-2">
            <Input
              className="h-7 text-small"
              placeholder="new agent name"
              value={newAgentName}
              onChange={(e) => setNewAgentName(e.target.value)}
              data-testid="new-agent-name"
            />
            <Button
              size="sm"
              variant="outline"
              data-testid="new-agent-create"
              disabled={!newAgentName.trim() || !selectedModel || createAgent.isPending}
              onClick={() => createAgent.mutate()}
            >
              Create
            </Button>
          </div>
          <select
            className="mt-2 h-7 w-full rounded border border-border bg-transparent px-2 text-small"
            value={selectedModel}
            onChange={(e) => setModel(e.target.value)}
            data-testid="new-agent-model"
          >
            {(models.data ?? []).map((m) => (
              <option key={m.id} value={m.id} disabled={!m.available}>
                {m.label}
                {m.available ? '' : ' — no credential'}
              </option>
            ))}
          </select>
          <div className="mt-1 text-footnote text-muted-foreground">
            Created with the workflow toolkit bound (build · list · run).
          </div>
        </div>
      </div>

      {/* Runs */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2">
          <select
            className="h-7 rounded border border-border bg-background px-2 text-small"
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            data-testid="run-agent-select"
          >
            <option value="">choose agent…</option>
            {agents.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <Input
            className="h-7 flex-1 text-small"
            placeholder="Describe the task — e.g. build a workflow that fetches an export every Monday and loads it"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            data-testid="run-task"
          />
          <Button
            size="sm"
            data-testid="run-trigger"
            disabled={!agentId || !task.trim() || trigger.isPending}
            onClick={() => trigger.mutate()}
          >
            Run
          </Button>
        </div>
        {trigger.isError && (
          <div className="border-b border-border px-4 py-2 text-small text-destructive" data-testid="trigger-error">
            {(trigger.error as Error).message}
          </div>
        )}

        {(() => {
          const attempt =
            openAttemptSeq !== null
              ? buildAttempts(events.data).find((a) => a.seq === openAttemptSeq)
              : undefined;
          if (attempt) {
            return <BuildAttemptView attempt={attempt} onBack={() => setOpenAttemptSeq(null)} />;
          }
          return null;
        })() || (
        <div className="min-h-0 flex-1 overflow-auto" data-testid="runs-table">
          <table className="w-full text-small">
            <thead className="sticky top-0 bg-background text-left text-footnote text-muted-foreground">
              <tr className="border-b border-border">
                <th className="px-4 py-1.5 font-normal">status</th>
                <th className="px-2 py-1.5 font-normal">task</th>
                <th className="px-2 py-1.5 font-normal">iter</th>
                <th className="px-2 py-1.5 font-normal">tools</th>
                <th className="px-2 py-1.5 font-normal">events</th>
                <th className="px-2 py-1.5 font-normal">started</th>
              </tr>
            </thead>
            <tbody>
              {runs.data?.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-3 text-muted-foreground">
                    No runs yet.
                  </td>
                </tr>
              )}
              {runs.data?.map((r) => (
                <tr
                  key={r.id}
                  data-testid="run-row"
                  onClick={() => {
                    setSelectedRunId(r.id);
                    setOpenAttemptSeq(null);
                  }}
                  className={`cursor-pointer border-b border-border hover:bg-secondary ${
                    selectedRunId === r.id ? 'bg-secondary' : ''
                  }`}
                >
                  <td className="px-4 py-1.5">
                    <StatusWord status={r.status} />
                  </td>
                  <td className="max-w-[380px] truncate px-2 py-1.5">{r.task}</td>
                  <td className="px-2 py-1.5 font-mono">{r.iterations}</td>
                  <td className="px-2 py-1.5 font-mono">{r.tool_call_count}</td>
                  <td className="px-2 py-1.5 font-mono">{r.event_count}</td>
                  <td className="px-2 py-1.5 text-muted-foreground">
                    {new Date(r.started_at).toLocaleTimeString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </div>

      {/* Run detail */}
      {selectedRunId && (
        <div className="flex w-[420px] shrink-0 flex-col border-l border-border" data-testid="run-detail">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2">
            <span className="text-label font-medium">Run</span>
            <span className="font-mono text-footnote text-muted-foreground">{selectedRunId}</span>
            <span className="ml-auto flex items-center gap-2">
              {runDetail.data && LIVE.has(runDetail.data.status) && (
                <Button size="sm" variant="ghost" onClick={() => cancelRun.mutate(selectedRunId)}>
                  Cancel
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setSelectedRunId(null)}>
                ✕
              </Button>
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-4 py-3 text-small">
            {runDetail.data && (
              <>
                <div className="flex items-center gap-3">
                  <span data-testid="run-status">
                    <StatusWord status={runDetail.data.status} />
                  </span>
                  <span className="font-mono text-footnote text-muted-foreground">
                    {runDetail.data.iterations} iter · {runDetail.data.tool_call_count} tool calls ·{' '}
                    {runDetail.data.input_tokens + runDetail.data.output_tokens} tok
                  </span>
                </div>
                {runDetail.data.error && (
                  <div
                    className="mt-2 rounded border border-border bg-secondary p-2 font-mono text-footnote text-destructive"
                    data-testid="run-error"
                  >
                    {runDetail.data.error}
                  </div>
                )}
                {runDetail.data.response && (
                  <div className="mt-2 whitespace-pre-wrap" data-testid="run-response">
                    {runDetail.data.response}
                  </div>
                )}
                {producedWorkflowIds(events.data).map((id) => (
                  <ProducedWorkflow key={id} workflowId={id} />
                ))}
                <div className="mt-3 text-footnote text-muted-foreground">
                  Events{' '}
                  {events.data ? `${events.data.length}` : '—'}
                </div>
                <div className="mt-1 space-y-2" data-testid="run-events">
                  {events.data?.map((e) => {
                    const p = e.payload as Record<string, unknown>;
                    const isBuildCall =
                      e.type === 'agent:tool_call' &&
                      typeof (p.arguments as Record<string, unknown> | undefined)?.script ===
                        'string';
                    return (
                      <div key={e.seq} className="rounded border border-border p-2">
                        <div className="flex items-center font-mono text-footnote text-muted-foreground">
                          {e.seq} · {e.type}
                          {e.truncated ? ' · truncated' : ''}
                          {isBuildCall && (
                            <button
                              type="button"
                              className="ml-auto underline underline-offset-2"
                              data-testid="attempt-open"
                              onClick={() => setOpenAttemptSeq(e.seq)}
                            >
                              view build →
                            </button>
                          )}
                        </div>
                        <div className="mt-0.5 break-words font-mono text-footnote">
                          {eventSummary(e).slice(0, 500)}
                        </div>
                      </div>
                    );
                  })}
                  {events.data?.length === 0 && (
                    <div className="text-footnote text-muted-foreground">
                      No events — a run that fails before its first model call leaves none.
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
